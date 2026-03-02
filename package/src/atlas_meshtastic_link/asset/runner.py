"""Asset runtime event loop."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from atlas_meshtastic_link.asset.intent_store import AssetIntentStore
from atlas_meshtastic_link.asset.sync import AssetSync
from atlas_meshtastic_link.config.schema import AssetConfig
from atlas_meshtastic_link.protocol.billboard_wire import (
    ASSET_INTENT,
    GATEWAY_INDEX,
    GATEWAY_UPDATE,
    build_merge_diff,
    compute_intent_hash,
    decode_billboard_message,
    encode_asset_intent,
    encode_asset_intent_diff,
)
from atlas_meshtastic_link.state.world_state import WorldStateStore

log = logging.getLogger(__name__)


class AssetRunner:
    """Runs the asset-side business loop after provisioning."""

    def __init__(
        self,
        *,
        radio,  # noqa: ANN001
        config: AssetConfig,
        stop_event: asyncio.Event,
        status_hook: Any | None = None,
    ) -> None:
        self._radio = radio
        self._config = config
        self._stop_event = stop_event
        self._status_hook = status_hook
        self._intent = AssetIntentStore(config.intent_path, asset_id=config.entity_id)
        self._world = WorldStateStore(config.world_state_path)
        self._sync = AssetSync(world_state=self._world, intent_store=self._intent)
        self._last_publish_at = 0.0
        self._last_full_publish_at = 0.0
        self._last_intent_payload: dict[str, Any] | None = None
        self._last_intent_hash: str | None = None
        self._intent_seq = 0
        self._expected_max_silence_ms = int(
            max(
                float(config.publish_min_interval_seconds) * 2.0,
                float(config.intent_refresh_interval_seconds) * 2.0,
            )
            * 1000.0
        )

    async def run(self) -> None:
        self._world.reset()
        self._world.set_meta(asset_id=self._config.entity_id)
        if self._status_hook is not None:
            try:
                self._status_hook(
                    {
                        "asset_intent_path": self._config.intent_path,
                        "world_state_path": self._config.world_state_path,
                    }
                )
            except (RuntimeError, TypeError, ValueError):
                log.debug("[ASSET] status hook failed", exc_info=True)
        tasks = [
            asyncio.create_task(self._intent_loop(), name="atlas_asset_intent_loop"),
            asyncio.create_task(self._receive_loop(), name="atlas_asset_receive_loop"),
            asyncio.create_task(self._flush_loop(), name="atlas_asset_flush_loop"),
        ]
        try:
            await self._stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._world.flush()

    async def _intent_loop(self) -> None:
        poll_interval = max(0.1, float(self._config.intent_poll_interval_seconds))
        min_interval = max(0.1, float(self._config.publish_min_interval_seconds))
        refresh_interval = max(min_interval, float(self._config.intent_refresh_interval_seconds))
        diff_enabled = bool(self._config.intent_diff_enabled)
        while not self._stop_event.is_set():
            changed, payload = self._intent.changed_since_last_read()
            now = time.monotonic()
            current_payload, asset_id = _intent_payload(payload, self._config.entity_id)

            if self._last_intent_payload is None:
                self._last_intent_payload = current_payload
                await self._publish_full(asset_id=asset_id, payload=current_payload, now=now)
            elif changed and now - self._last_publish_at >= min_interval:
                if diff_enabled:
                    patch = build_merge_diff(self._last_intent_payload, current_payload)
                    if patch:
                        await self._publish_diff(
                            asset_id=asset_id,
                            payload=current_payload,
                            patch=patch,
                            now=now,
                        )
                else:
                    await self._publish_full(asset_id=asset_id, payload=current_payload, now=now)
                self._last_intent_payload = current_payload

            if (
                self._last_intent_payload is not None
                and now - self._last_full_publish_at >= refresh_interval
                and now - self._last_publish_at >= min_interval
            ):
                await self._publish_full(
                    asset_id=asset_id,
                    payload=self._last_intent_payload,
                    now=now,
                )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                continue

    async def _publish_full(self, *, asset_id: str, payload: dict[str, Any], now: float) -> None:
        validation_error = _validate_intent_payload(payload)
        if validation_error is not None:
            log.warning("[ASSET] Intent validation failed (%s): %s", asset_id, validation_error)
            return
        intent_hash = compute_intent_hash(payload)
        self._intent_seq += 1
        encoded = encode_asset_intent(
            asset_id=asset_id,
            subscriptions=payload.get("subscriptions", {}),
            intent_seq=self._intent_seq,
            intent_hash=intent_hash,
            generated_at_ms=int(time.time() * 1000),
            expected_max_silence_ms=self._expected_max_silence_ms,
            meta=payload.get("meta"),
            entity_type=str(payload.get("entity_type") or "asset"),
            subtype=_optional_str(payload.get("subtype")),
            alias=_optional_str(payload.get("alias")),
            components=payload.get("components") if isinstance(payload.get("components"), dict) else None,
        )
        await self._radio.send(encoded, destination="^all")
        self._last_publish_at = now
        self._last_full_publish_at = now
        self._last_intent_hash = intent_hash
        self._world.set_meta(last_outbound_intent_epoch=time.time())
        log.info("[ASSET] Published intent snapshot (%s)", asset_id)

    async def _publish_diff(self, *, asset_id: str, payload: dict[str, Any], patch: dict[str, Any], now: float) -> None:
        validation_error = _validate_intent_payload(payload)
        if validation_error is not None:
            log.warning("[ASSET] Intent validation failed (%s): %s", asset_id, validation_error)
            return
        base_hash = self._last_intent_hash or compute_intent_hash(self._last_intent_payload or {})
        intent_hash = compute_intent_hash(payload)
        self._intent_seq += 1
        encoded = encode_asset_intent_diff(
            asset_id=asset_id,
            patch=patch,
            intent_seq=self._intent_seq,
            intent_hash=intent_hash,
            base_hash=base_hash,
            generated_at_ms=int(time.time() * 1000),
            expected_max_silence_ms=self._expected_max_silence_ms,
        )
        await self._radio.send(encoded, destination="^all")
        self._last_publish_at = now
        self._last_intent_hash = intent_hash
        self._world.set_meta(last_outbound_intent_epoch=time.time())
        log.info("[ASSET] Published intent diff (%s)", asset_id)

    # Keep these as AssetRunner class methods (not nested under helpers),
    # otherwise runtime receive/flush loops won't be registered.
    async def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                raw, sender = await asyncio.wait_for(self._radio.receive(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            parsed = decode_billboard_message(raw)
            if parsed is None:
                continue
            msg_type = parsed.get("msg_type")
            if msg_type == GATEWAY_UPDATE:
                await self._sync.handle_gateway_update(parsed, sender=sender)
                self._world.set_meta(last_gateway_update_epoch=time.time())
            elif msg_type == GATEWAY_INDEX:
                await self._sync.handle_gateway_index(parsed, sender=sender)
                self._world.set_meta(last_index_update_epoch=time.time())
            elif msg_type == ASSET_INTENT:
                if str(parsed.get("asset_id") or "") == str(self._config.entity_id or ""):
                    continue
                await self._sync.handle_overheard_intent(parsed, sender=sender)
                self._world.set_meta(last_passive_update_epoch=time.time())

    async def _flush_loop(self) -> None:
        interval = max(0.2, float(self._config.world_state_flush_interval_seconds))
        while not self._stop_event.is_set():
            self._world.flush()
            if self._status_hook is not None:
                try:
                    self._status_hook({"world_state_path": self._config.world_state_path})
                except (RuntimeError, TypeError, ValueError):
                    log.debug("[ASSET] status hook failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _intent_payload(payload: dict[str, Any], fallback_asset_id: str | None) -> tuple[dict[str, Any], str]:
    asset_id = str(payload.get("asset_id") or fallback_asset_id or "asset-unknown")
    return (
        {
            "entity_type": str(payload.get("entity_type") or "asset"),
            "subtype": _optional_str(payload.get("subtype")),
            "asset_id": asset_id,
            "alias": _optional_str(payload.get("alias")),
            "subscriptions": payload.get("subscriptions", {}),
            "meta": payload.get("meta"),
            "components": payload.get("components") if isinstance(payload.get("components"), dict) else None,
        },
        asset_id,
    )


def _validate_intent_payload(payload: dict[str, Any]) -> str | None:
    components = payload.get("components")
    if not isinstance(components, dict):
        return None
    telemetry = components.get("telemetry")
    if not isinstance(telemetry, dict):
        return None

    latitude = telemetry.get("latitude")
    if latitude is not None:
        try:
            latitude_value = float(latitude)
        except (TypeError, ValueError):
            return "telemetry.latitude must be numeric"
        if latitude_value < -90.0 or latitude_value > 90.0:
            return "telemetry.latitude out of range [-90, 90]"

    longitude = telemetry.get("longitude")
    if longitude is not None:
        try:
            longitude_value = float(longitude)
        except (TypeError, ValueError):
            return "telemetry.longitude must be numeric"
        if longitude_value < -180.0 or longitude_value > 180.0:
            return "telemetry.longitude out of range [-180, 180]"

    return None
