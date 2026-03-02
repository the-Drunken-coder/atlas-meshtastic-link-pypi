"""Gateway business runtime for billboard operations."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from httpx import HTTPError

from atlas_meshtastic_link.config.schema import GatewayConfig
from atlas_meshtastic_link.gateway.interaction_log import InteractionLog
from atlas_meshtastic_link.protocol.billboard_wire import (
    ASSET_INTENT,
    ASSET_INTENT_DIFF,
    apply_merge_diff,
    decode_billboard_message,
    encode_gateway_index,
    encode_gateway_update,
)
from atlas_meshtastic_link.protocol.subscriptions import TASKS_SELF_KEY, subscription_keys
from atlas_meshtastic_link.gateway.operations.registry import OperationRegistry
from atlas_meshtastic_link.state.subscriptions import LeaseRegistry

log = logging.getLogger(__name__)
ephemeral_log = logging.getLogger("atlas_meshtastic_link.sync.ephemeral")


STATE_IN_SYNC = "IN_SYNC"
STATE_DEGRADED = "DEGRADED"


@dataclass
class _SyncHealth:
    state: str = STATE_IN_SYNC
    last_seq: int = 0
    last_hash: str = ""
    last_apply_epoch: float = 0.0
    last_generated_at_ms: int = 0
    expected_max_silence_ms: int = 0
    discrepancy_since_epoch: float | None = None
    last_reason: str = "startup"


class GatewayOperationsRuntime:
    """Tracks asset intent and fanout of incremental Atlas changes."""

    def __init__(
        self,
        *,
        radio,  # noqa: ANN001
        bridge,  # noqa: ANN001
        config: GatewayConfig,
        stop_event: asyncio.Event,
        status_hook=None,  # noqa: ANN001
        interaction_log: InteractionLog | None = None,
    ) -> None:
        self._radio = radio
        self._bridge = bridge
        self._config = config
        self._stop_event = stop_event
        self._status_hook = status_hook
        self._interaction_log = interaction_log
        self._leases = LeaseRegistry(default_ttl_seconds=config.asset_intent_ttl_seconds)
        self._asset_subscriptions: dict[str, set[str]] = {}
        self._asset_intents: dict[str, dict[str, Any]] = {}
        self._asset_full_payloads: dict[str, dict[str, Any]] = {}
        self._known_entity_ids: set[str] = set()
        self._last_index_broadcast = 0.0
        self._last_index_diff_broadcast = 0.0
        self._index_dirty = False
        self._last_since = datetime.now(timezone.utc).isoformat()
        self._send_window_started = time.monotonic()
        self._sent_this_window = 0
        self._registry = OperationRegistry()
        self._registry.register(ASSET_INTENT, self._handle_asset_intent)
        self._registry.register(ASSET_INTENT_DIFF, self._handle_asset_intent_diff)
        self._sync_health_by_asset: dict[str, _SyncHealth] = {}
        self._sync_stale_after_seconds = max(0.01, float(config.sync_stale_after_seconds))
        self._sync_summary_interval_seconds = max(5.0, float(config.sync_health_summary_interval_seconds))
        self._last_sync_summary_at = 0.0

    async def run(self) -> None:
        await self._seed_entity_index()
        poll_interval = max(0.2, float(self._config.api_poll_interval_seconds))
        while not self._stop_event.is_set():
            self._check_sync_staleness()
            self._maybe_emit_sync_summary()
            await self._poll_and_publish()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                continue

    async def on_radio_message(self, raw: bytes, sender: str) -> None:
        message = decode_billboard_message(raw)
        if message is None:
            return
        msg_type = str(message.get("msg_type") or "")
        await self._registry.dispatch(msg_type, message, sender)

    async def _handle_asset_intent(self, message: dict[str, Any], sender: str) -> None:
        asset_id = str(message.get("asset_id") or sender)
        try:
            intent_seq = int(message["intent_seq"])
            intent_hash = str(message["intent_hash"])
            generated_at_ms = int(message["generated_at_ms"])
            expected_max_silence_ms = int(message["expected_max_silence_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("[GATEWAY] Dropping malformed asset intent from %s: %s", sender, exc)
            return
        payload = _intent_payload(message)
        self._asset_full_payloads[sender] = payload
        canonical_message = _intent_wire_message(payload)
        keys = subscription_keys(payload.get("subscriptions", {}))
        self._asset_subscriptions[asset_id] = keys
        # Cache by sender node id so UI lookups match connected_assets ids.
        self._asset_intents[sender] = {
            "node_id": sender,
            "asset_id": asset_id,
            "updated_at_epoch": time.time(),
            "payload": canonical_message,
        }
        self._leases.replace_asset_subscriptions(
            asset_id,
            keys,
            ttl=self._config.asset_intent_ttl_seconds,
        )
        self._mark_in_sync(
            asset_id=asset_id,
            seq=intent_seq,
            intent_hash=intent_hash,
            generated_at_ms=generated_at_ms,
            expected_max_silence_ms=expected_max_silence_ms,
            reason="full_snapshot",
        )
        await self._publish_asset_presence(asset_id=asset_id, message=canonical_message)
        self._emit_status(
            gateway_asset_intents=self._asset_intents,
            sync_health_by_asset=self._sync_health_payload(),
        )
        if self._interaction_log is not None:
            self._interaction_log.record(
                "ASSET_INTENT_RECEIVED",
                f"asset={asset_id} seq={intent_seq} hash={intent_hash} subscriptions={len(keys)}\n{json.dumps(canonical_message, indent=2)}",
            )
        log.debug("[GATEWAY] Updated subscriptions for %s (%d)", asset_id, len(keys))
        if keys:
            await self._push_current_subscribed_records(asset_id=asset_id, keys=keys)

    async def _handle_asset_intent_diff(self, message: dict[str, Any], sender: str) -> None:
        asset_id = str(message.get("asset_id") or sender)
        try:
            intent_seq = int(message["intent_seq"])
            intent_hash = str(message["intent_hash"])
            generated_at_ms = int(message["generated_at_ms"])
            expected_max_silence_ms = int(message["expected_max_silence_ms"])
            base_hash = str(message["base_hash"])
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("[GATEWAY] Dropping malformed asset intent diff from %s: %s", sender, exc)
            return
        health = self._sync_health_by_asset.setdefault(asset_id, _SyncHealth())
        base_payload = self._asset_full_payloads.get(sender)
        if base_payload is None:
            log.debug("[GATEWAY] Ignoring intent diff for %s: missing base payload", asset_id)
            self._emit_status(intent_diff_event={"asset_id": asset_id, "status": "ignored", "reason": "missing_base"})
            self._mark_degraded(asset_id=asset_id, reason="missing_base")
            return
        patch = message.get("patch")
        if not isinstance(patch, dict):
            log.debug("[GATEWAY] Ignoring intent diff for %s: invalid patch type", asset_id)
            self._emit_status(intent_diff_event={"asset_id": asset_id, "status": "ignored", "reason": "invalid_patch"})
            self._mark_degraded(asset_id=asset_id, reason="invalid_patch")
            return
        if not health.last_hash:
            self._mark_degraded(asset_id=asset_id, reason="missing_last_hash")
            return
        if base_hash != health.last_hash:
            self._mark_degraded(
                asset_id=asset_id,
                reason=f"base_hash_mismatch expected={health.last_hash} got={base_hash}",
            )
            if self._interaction_log is not None:
                self._interaction_log.record(
                    "SYNC_DIFF_REJECTED_BASE_MISMATCH",
                    f"asset={asset_id} seq={intent_seq} expected_base_hash={health.last_hash} got_base_hash={base_hash}",
                )
            return
        if health.last_seq and intent_seq != health.last_seq + 1:
            self._mark_degraded(
                asset_id=asset_id,
                reason=f"seq_gap expected={health.last_seq + 1} got={intent_seq}",
            )
            return

        merged_payload = apply_merge_diff(base_payload, patch)
        self._asset_full_payloads[sender] = merged_payload
        canonical_message = _intent_wire_message(merged_payload)
        keys = subscription_keys(merged_payload.get("subscriptions", {}))
        self._asset_subscriptions[asset_id] = keys
        self._asset_intents[sender] = {
            "node_id": sender,
            "asset_id": asset_id,
            "updated_at_epoch": time.time(),
            "payload": canonical_message,
        }
        self._leases.replace_asset_subscriptions(
            asset_id,
            keys,
            ttl=self._config.asset_intent_ttl_seconds,
        )
        self._mark_in_sync(
            asset_id=asset_id,
            seq=intent_seq,
            intent_hash=intent_hash,
            generated_at_ms=generated_at_ms,
            expected_max_silence_ms=expected_max_silence_ms,
            reason="diff_applied",
        )
        await self._publish_asset_presence(asset_id=asset_id, message=canonical_message)
        self._emit_status(
            gateway_asset_intents=self._asset_intents,
            sync_health_by_asset=self._sync_health_payload(),
            intent_diff_event={"asset_id": asset_id, "status": "applied"},
        )
        if self._interaction_log is not None:
            self._interaction_log.record(
                "ASSET_INTENT_DIFF_RECEIVED",
                f"asset={asset_id} seq={intent_seq} hash={intent_hash}\npatch={json.dumps(patch, indent=2)}\nmerged={json.dumps(canonical_message, indent=2)}",
            )
        log.debug("[GATEWAY] Applied intent diff for %s", asset_id)
        if keys:
            await self._push_current_subscribed_records(asset_id=asset_id, keys=keys)

    async def _poll_and_publish(self) -> None:
        self._leases.expire()
        try:
            changes = await self._bridge.get_changed_since(
                since=self._last_since,
                limit_per_type=self._config.changed_since_limit_per_type,
            )
        except (ConnectionError, HTTPError, OSError, RuntimeError, TimeoutError, ValueError):
            log.exception("[GATEWAY] get_changed_since failed")
            return

        server_ts = changes.get("timestamp")
        if isinstance(server_ts, str) and server_ts:
            self._last_since = server_ts
        else:
            self._last_since = datetime.now(timezone.utc).isoformat()
        await self._update_entity_index(changes)
        if not self._asset_subscriptions:
            return
        records = _records_from_changes(changes)
        if not records:
            return

        subscribed_union: set[str] = set()
        tasks_self_assets: set[str] = set()
        for aid, keys in self._asset_subscriptions.items():
            subscribed_union.update(keys)
            if TASKS_SELF_KEY in keys:
                tasks_self_assets.add(aid)

        outbound: list[dict[str, Any]] = []
        for record in records:
            key = f"{record['kind']}:{record['id']}"
            if key in subscribed_union:
                outbound.append(record)
            elif record.get("kind") == "tasks" and tasks_self_assets:
                task_entity_id = (record.get("data") or {}).get("entity_id")
                task_entity_id_str = str(task_entity_id) if task_entity_id is not None else None
                if task_entity_id_str and task_entity_id_str in tasks_self_assets:
                    outbound.append(record)

        if not outbound:
            return

        # Keep broadcast pressure bounded.
        max_records = max(1, int(max(1.0, self._config.publish_max_messages_per_second)))
        outbound = outbound[:max_records]
        if not self._consume_tokens(len(outbound)):
            return
        payload = encode_gateway_update(records=outbound, meta={"since": self._last_since})
        await self._radio.send(payload, destination="^all")
        if self._interaction_log is not None:
            self._interaction_log.record("GATEWAY_BROADCAST", f"records={len(outbound)}")
        log.info("[GATEWAY] Broadcast %d update records", len(outbound))

    async def _seed_entity_index(self) -> None:
        """Fetch the full dataset once at startup to seed the entity index."""
        try:
            dataset = await self._bridge.get_full_dataset()
        except (ConnectionError, HTTPError, OSError, RuntimeError, TimeoutError, ValueError):
            log.exception("[GATEWAY] Failed to seed entity index from full dataset")
            return

        for entity in dataset.get("entities", []) or []:
            if isinstance(entity, dict):
                entity_id = entity.get("entity_id")
                if entity_id:
                    self._known_entity_ids.add(str(entity_id))

        if self._known_entity_ids:
            self._index_dirty = True
            log.info("[GATEWAY] Seeded entity index with %d entities", len(self._known_entity_ids))

    async def _push_current_subscribed_records(
        self, *, asset_id: str, keys: set[str]
    ) -> None:
        """Fetch the full dataset and push records matching subscription keys."""
        try:
            dataset = await self._bridge.get_full_dataset()
        except (ConnectionError, HTTPError, OSError, RuntimeError, TimeoutError, ValueError):
            log.exception("[GATEWAY] Failed to fetch full dataset for subscription push")
            return

        records = _records_from_changes(dataset)

        def _matches(r: dict[str, Any]) -> bool:
            key = f"{r['kind']}:{r['id']}"
            if key in keys:
                return True
            if r.get("kind") == "tasks" and TASKS_SELF_KEY in keys:
                task_entity_id = (r.get("data") or {}).get("entity_id")
                return task_entity_id is not None and str(task_entity_id) == asset_id
            return False

        outbound = [r for r in records if _matches(r)]
        if not outbound:
            return

        payload = encode_gateway_update(records=outbound, meta={"reason": "subscription_init"})
        await self._radio.send(payload, destination="^all")
        if self._interaction_log is not None:
            self._interaction_log.record("SUBSCRIPTION_INIT_PUSH", f"records={len(outbound)}")
        log.info("[GATEWAY] Pushed %d current records for new subscriptions", len(outbound))

    async def _update_entity_index(self, changes: dict[str, Any]) -> None:
        previous_ids = set(self._known_entity_ids)

        for entity in changes.get("entities", []) or []:
            if isinstance(entity, dict):
                entity_id = entity.get("entity_id")
                if entity_id:
                    self._known_entity_ids.add(str(entity_id))

        for deleted in changes.get("deleted_entities", []) or []:
            if isinstance(deleted, dict):
                entity_id = deleted.get("entity_id")
                if entity_id:
                    self._known_entity_ids.discard(str(entity_id))

        if self._known_entity_ids != previous_ids:
            self._index_dirty = True

        now = time.monotonic()
        index_interval = max(1.0, float(self._config.index_broadcast_interval_seconds))
        diff_min = max(1.0, float(self._config.index_diff_min_interval_seconds))

        should_broadcast = False
        if now - self._last_index_broadcast >= index_interval:
            should_broadcast = True
        elif self._index_dirty and now - self._last_index_diff_broadcast >= diff_min:
            should_broadcast = True

        # Broadcast when due and either there are known entities or the index changed
        # (including the case where it became empty).
        if should_broadcast and (self._known_entity_ids or self._index_dirty):
            payload = encode_gateway_index(entity_ids=sorted(self._known_entity_ids))
            await self._radio.send(payload, destination="^all")
            self._last_index_broadcast = now
            self._last_index_diff_broadcast = now
            self._index_dirty = False
            if self._interaction_log is not None:
                self._interaction_log.record("ENTITY_INDEX_BROADCAST", f"count={len(self._known_entity_ids)}")
            log.info("[GATEWAY] Broadcast entity index (%d entities)", len(self._known_entity_ids))

    def _consume_tokens(self, count: int) -> bool:
        rate = max(1.0, float(self._config.publish_max_messages_per_second))
        now = time.monotonic()
        if now - self._send_window_started >= 1.0:
            self._send_window_started = now
            self._sent_this_window = 0
        if self._sent_this_window + count > rate:
            return False
        self._sent_this_window += count
        return True

    def _emit_status(self, **payload: Any) -> None:
        if self._status_hook is None:
            return
        try:
            self._status_hook(payload)
        except (RuntimeError, TypeError, ValueError):
            log.debug("[GATEWAY] status hook failed", exc_info=True)

    async def _publish_asset_presence(self, *, asset_id: str, message: dict[str, Any]) -> None:
        publish_fn = getattr(self._bridge, "publish_asset_intent", None)
        if not callable(publish_fn):
            return
        try:
            checkin_response = await publish_fn(asset_id=asset_id, intent=message)
            if self._interaction_log is not None:
                self._interaction_log.record(
                    "ASSET_INTENT_PUBLISHED",
                    f"asset={asset_id}\n{json.dumps(message, indent=2)}",
                )
        except (ConnectionError, HTTPError, OSError, RuntimeError, TimeoutError, ValueError):
            self._mark_degraded(asset_id=asset_id, reason="publish_asset_presence_failed")
            log.exception("[GATEWAY] Failed to publish asset intent to Atlas Command (asset_id=%s)", asset_id)
            return

        await self._forward_checkin_tasks(asset_id=asset_id, checkin_response=checkin_response or {})

    async def _forward_checkin_tasks(self, *, asset_id: str, checkin_response: dict[str, Any]) -> None:
        """If checkin returns pending tasks, broadcast them to mesh for asset-side filtering."""
        tasks = checkin_response.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            return

        records: list[dict[str, Any]] = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = task.get("task_id")
            if not task_id:
                continue
            records.append({
                "kind": "tasks",
                "id": str(task_id),
                "data": task,
                "version": _extract_version(task),
            })

        if not records:
            return

        max_records = max(1, int(max(1.0, self._config.publish_max_messages_per_second)))
        if len(records) > max_records:
            log.debug(
                "[GATEWAY] Truncating checkin tasks from %d to %d for asset %s",
                len(records),
                max_records,
                asset_id,
            )
            records = records[:max_records]
        if not self._consume_tokens(len(records)):
            log.debug(
                "[GATEWAY] Rate limit exhausted; skipping checkin task broadcast (%d records) for asset %s",
                len(records),
                asset_id,
            )
            return

        payload = encode_gateway_update(records=records, meta={"reason": "checkin_tasks"})
        await self._radio.send(payload, destination="^all")
        if self._interaction_log is not None:
            task_ids = [r["id"] for r in records]
            self._interaction_log.record(
                "CHECKIN_TASKS_FORWARDED",
                f"asset={asset_id} tasks={task_ids}",
            )
        log.info(
            "[GATEWAY] Broadcast %d checkin task(s) for asset %s (asset-side filtering applies)",
            len(records),
            asset_id,
        )

    def _sync_health_payload(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        payload: dict[str, dict[str, Any]] = {}
        for asset_id, health in self._sync_health_by_asset.items():
            discrepancy_age_ms = None
            if health.discrepancy_since_epoch is not None:
                discrepancy_age_ms = int(max(0.0, now - health.discrepancy_since_epoch) * 1000.0)
            payload[asset_id] = {
                "state": health.state,
                "last_seq": health.last_seq,
                "last_hash": health.last_hash,
                "last_apply_epoch": health.last_apply_epoch,
                "last_generated_at_ms": health.last_generated_at_ms,
                "expected_max_silence_ms": health.expected_max_silence_ms,
                "discrepancy_age_ms": discrepancy_age_ms,
                "last_reason": health.last_reason,
            }
        return payload

    def _mark_in_sync(
        self,
        *,
        asset_id: str,
        seq: int,
        intent_hash: str,
        generated_at_ms: int,
        expected_max_silence_ms: int,
        reason: str,
    ) -> None:
        now = time.time()
        health = self._sync_health_by_asset.setdefault(asset_id, _SyncHealth())
        previous_state = health.state
        health.state = STATE_IN_SYNC
        health.last_seq = seq
        health.last_hash = intent_hash
        health.last_generated_at_ms = generated_at_ms
        health.expected_max_silence_ms = max(1, expected_max_silence_ms)
        health.last_apply_epoch = now
        health.last_reason = reason
        health.discrepancy_since_epoch = None

        if previous_state != STATE_IN_SYNC:
            log.info("[SYNC] Asset %s recovered (%s)", asset_id, reason)
            ephemeral_log.info(
                "[SYNC_EPHEMERAL] event=recovered asset=%s from=%s to=%s seq=%d reason=%s",
                asset_id,
                previous_state,
                STATE_IN_SYNC,
                seq,
                reason,
            )
            self._emit_status(
                sync_health_event={
                    "asset_id": asset_id,
                    "from": previous_state,
                    "to": STATE_IN_SYNC,
                    "reason": reason,
                    "seq": seq,
                    "discrepancy_age_ms": 0,
                }
            )
            if self._interaction_log is not None:
                self._interaction_log.record(
                    "SYNC_DISCREPANCY_RECOVERED",
                    f"asset={asset_id} seq={seq} reason={reason}",
                )
        if self._interaction_log is not None:
            event_type = "SYNC_INTENT_APPLIED" if reason == "full_snapshot" else "SYNC_DIFF_APPLIED"
            self._interaction_log.record(event_type, f"asset={asset_id} seq={seq} hash={intent_hash}")

    def _mark_degraded(self, *, asset_id: str, reason: str) -> None:
        now = time.time()
        health = self._sync_health_by_asset.setdefault(asset_id, _SyncHealth())
        previous_state = health.state
        if health.discrepancy_since_epoch is None:
            health.discrepancy_since_epoch = now
        health.state = STATE_DEGRADED
        health.last_reason = reason

        if previous_state == STATE_DEGRADED:
            return

        age_ms = int(max(0.0, now - (health.discrepancy_since_epoch or now)) * 1000.0)
        log.warning("[SYNC] Asset %s degraded (%s)", asset_id, reason)
        ephemeral_log.warning(
            "[SYNC_EPHEMERAL] event=degraded asset=%s from=%s to=%s seq=%d age_ms=%d reason=%s",
            asset_id,
            previous_state,
            STATE_DEGRADED,
            health.last_seq,
            age_ms,
            reason,
        )
        self._emit_status(
            sync_health_event={
                "asset_id": asset_id,
                "from": previous_state,
                "to": STATE_DEGRADED,
                "reason": reason,
                "seq": health.last_seq,
                "discrepancy_age_ms": age_ms,
            },
            sync_health_by_asset=self._sync_health_payload(),
        )
        if self._interaction_log is not None:
            self._interaction_log.record(
                "SYNC_DISCREPANCY_DETECTED",
                f"asset={asset_id} seq={health.last_seq} reason={reason}",
            )

    def _check_sync_staleness(self) -> None:
        now = time.time()
        for asset_id, health in self._sync_health_by_asset.items():
            if health.last_apply_epoch <= 0:
                continue
            timeout_seconds = self._sync_stale_after_seconds
            if health.expected_max_silence_ms > 0:
                timeout_seconds = max(timeout_seconds, health.expected_max_silence_ms / 1000.0)
            if now - health.last_apply_epoch > timeout_seconds:
                self._mark_degraded(
                    asset_id=asset_id,
                    reason=f"stale_apply_timeout>{timeout_seconds:.1f}s",
                )

    def _maybe_emit_sync_summary(self) -> None:
        now = time.time()
        if now - self._last_sync_summary_at < self._sync_summary_interval_seconds:
            return
        self._last_sync_summary_at = now
        total = len(self._sync_health_by_asset)
        degraded = sum(1 for health in self._sync_health_by_asset.values() if health.state == STATE_DEGRADED)
        max_age_ms = 0
        for health in self._sync_health_by_asset.values():
            if health.discrepancy_since_epoch is None:
                continue
            max_age_ms = max(max_age_ms, int(max(0.0, now - health.discrepancy_since_epoch) * 1000.0))
        log.info("[SYNC] Summary assets=%d in_sync=%d degraded=%d max_discrepancy_age_ms=%d", total, total - degraded, degraded, max_age_ms)
        ephemeral_log.info(
            "[SYNC_EPHEMERAL] event=summary assets=%d in_sync=%d degraded=%d max_discrepancy_age_ms=%d",
            total,
            total - degraded,
            degraded,
            max_age_ms,
        )
        if self._interaction_log is not None:
            self._interaction_log.record(
                "SYNC_HEALTH_SUMMARY",
                f"assets={total} in_sync={total - degraded} degraded={degraded} max_discrepancy_age_ms={max_age_ms}",
            )


def _extract_version(record: dict[str, Any]) -> str | None:
    """Extract the updated_at timestamp from an API response record.

    The core Atlas Command API nests updated_at inside a ``metadata`` block,
    e.g. ``{"metadata": {"updated_at": "..."}}``.  Older or minimal payloads
    may place it at the top level, so we fall back there for compatibility.
    """
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        val = metadata.get("updated_at")
        if val is not None:
            return val
    return record.get("updated_at")


def _records_from_changes(changes: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entity in changes.get("entities", []) or []:
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("entity_id")
        if entity_id:
            records.append(
                {
                    "kind": "entities",
                    "id": str(entity_id),
                    "data": entity,
                    "version": _extract_version(entity),
                }
            )
    for task in changes.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        task_id = task.get("task_id")
        if task_id:
            records.append(
                {
                    "kind": "tasks",
                    "id": str(task_id),
                    "data": task,
                    "version": _extract_version(task),
                }
            )
    for obj in changes.get("objects", []) or []:
        if not isinstance(obj, dict):
            continue
        object_id = obj.get("object_id")
        if object_id:
            records.append(
                {
                    "kind": "objects",
                    "id": str(object_id),
                    "data": obj,
                    "version": _extract_version(obj),
                }
            )
    return records

def _canonicalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    # Stable key ordering makes UI comparisons easier without changing semantics.
    return json.loads(json.dumps(payload, sort_keys=True))


def _intent_payload(message: dict[str, Any]) -> dict[str, Any]:
    # Keep payload fields unchanged and strip only the wire envelope marker.
    return {k: v for k, v in message.items() if k != "msg_type"}


def _intent_wire_message(payload: dict[str, Any]) -> dict[str, Any]:
    return _canonicalize_payload({"msg_type": ASSET_INTENT, **payload})
