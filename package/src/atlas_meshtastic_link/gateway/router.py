"""GatewayRouter - receive, dispatch, and reply loop."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import secrets
import time
from typing import Any, Awaitable, Callable

from atlas_meshtastic_link.gateway.interaction_log import InteractionLog
from atlas_meshtastic_link.protocol.discovery_wire import (
    CHALLENGE,
    CHALLENGE_RESPONSE,
    DISCOVERY_SEARCH,
    GATEWAY_PRESENT,
    PROVISION_COMPLETE,
    PROVISION_CREDENTIALS,
    PROVISION_REJECTED,
    PROVISION_REQUEST,
    decode_discovery_message,
    encode_discovery_message,
    optional_session_id,
)

log = logging.getLogger(__name__)


@dataclass
class _ProvisionSession:
    session_id: str
    challenge_sent_at: float
    last_activity_at: float
    credentials_sent_at: float | None = None


class GatewayRouter:
    """Main gateway discovery/provisioning event loop."""

    def __init__(
        self,
        *,
        radio,  # noqa: ANN001
        gateway_id: str | None = None,
        challenge_code: str = "ATLAS_CHALLENGE",
        expected_response_code: str = "ATLAS_RESPONSE",
        command_channel_url: str | None = None,
        asset_lease_timeout_seconds: float = 45.0,
        stop_event: asyncio.Event | None = None,
        poll_seconds: float = 0.5,
        on_assets_changed: Callable[[list[str]], None] | None = None,
        on_business_message: Callable[[bytes, str], Awaitable[None] | None] | None = None,
        interaction_log: InteractionLog | None = None,
        ready_event: asyncio.Event | None = None,
    ) -> None:
        self._radio = radio
        self._ready_event = ready_event
        self._gateway_id = gateway_id
        self._challenge_code = challenge_code
        self._expected_response_code = expected_response_code
        self._command_channel_url = command_channel_url
        self._asset_lease_timeout_seconds = max(0.1, float(asset_lease_timeout_seconds))
        self._stop_event = stop_event or asyncio.Event()
        self._poll_seconds = max(0.05, poll_seconds)
        self._provision_sessions: dict[str, _ProvisionSession] = {}
        self._connected_assets: set[str] = set()
        self._asset_last_seen: dict[str, float] = {}
        self._on_assets_changed = on_assets_changed
        self._on_business_message = on_business_message
        self._interaction_log = interaction_log
        self._challenge_resend_interval_seconds = max(0.5, self._poll_seconds * 4.0)
        self._session_ttl_seconds = max(5.0, self._asset_lease_timeout_seconds)

    async def run(self) -> None:
        log.info("[ROUTER] Discovery router started")
        if self._ready_event is not None:
            self._ready_event.set()
        while not self._stop_event.is_set():
            try:
                raw, sender = await asyncio.wait_for(self._radio.receive(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                self._expire_stale_assets()
                continue
            except (ConnectionError, OSError, RuntimeError, ValueError):
                log.exception("[ROUTER] Receive failed")
                self._expire_stale_assets()
                continue

            self._mark_asset_activity(sender)

            message = decode_discovery_message(raw)
            if message is None:
                await self._dispatch_business_message(raw, sender)
                self._expire_stale_assets()
                continue

            op = message.get("op")
            try:
                if op == DISCOVERY_SEARCH:
                    await self._handle_search(sender)
                elif op == PROVISION_REQUEST:
                    await self._handle_provision_request(sender)
                elif op == CHALLENGE_RESPONSE:
                    await self._handle_challenge_response(sender, message)
                elif op == PROVISION_COMPLETE:
                    self._handle_provision_complete(sender, message)
                else:
                    await self._dispatch_business_message(raw, sender)
            except (ConnectionError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                log.exception("[ROUTER] Handler failed for op=%s sender=%s", op, sender)
            finally:
                self._expire_stale_assets()
                self._expire_stale_sessions()

        log.info("[ROUTER] Discovery router stopped")

    def _log_interaction(self, event_type: str, details: str = "") -> None:
        if self._interaction_log is not None:
            self._interaction_log.record(event_type, details)

    async def _handle_search(self, sender: str) -> None:
        gateway_id = await self._gateway_identity()
        await self._send(
            GATEWAY_PRESENT,
            destination=sender,
            gateway_id=gateway_id,
        )
        log.info("[ROUTER] Responded to discovery search from %s", sender)
        self._log_interaction("DISCOVERY_RESPONSE", f"asset={sender}")

    async def _handle_provision_request(self, sender: str) -> None:
        if sender in self._connected_assets:
            log.debug("[ROUTER] Ignoring provision request from already connected asset %s", sender)
            return
        now = time.monotonic()
        session = self._provision_sessions.get(sender)
        if session is None:
            session = _ProvisionSession(
                session_id=_new_session_id(),
                challenge_sent_at=0.0,
                last_activity_at=now,
            )
            self._provision_sessions[sender] = session
            self._log_interaction("PROVISION_REQUEST", f"asset={sender} session={session.session_id}")

        session.last_activity_at = now
        if session.credentials_sent_at is not None:
            # Asset may have missed credentials; resend them instead of restarting challenge flow.
            if now - session.credentials_sent_at < self._challenge_resend_interval_seconds:
                log.debug(
                    "[ROUTER] Ignoring duplicate provision request from %s while completion is in-flight (session=%s)",
                    sender,
                    session.session_id,
                )
                return
            sent = await self._send_credentials(sender, session)
            if sent:
                log.info(
                    "[ROUTER] Re-sent provision channel config to %s (session=%s)",
                    sender,
                    session.session_id,
                )
            return

        if session.challenge_sent_at and (now - session.challenge_sent_at) < self._challenge_resend_interval_seconds:
            log.debug(
                "[ROUTER] Ignoring duplicate provision request from %s (session=%s challenge_age=%.2fs)",
                sender,
                session.session_id,
                now - session.challenge_sent_at,
            )
            return

        await self._send(
            CHALLENGE,
            destination=sender,
            gateway_id=await self._gateway_identity(),
            challenge_code=self._challenge_code,
            session_id=session.session_id,
        )
        session.challenge_sent_at = now
        log.info("[ROUTER] Sent challenge to %s (session=%s)", sender, session.session_id)
        self._log_interaction("CHALLENGE_SENT", f"asset={sender} session={session.session_id}")

    async def _handle_challenge_response(self, sender: str, message: dict[str, Any]) -> None:
        response_code = message.get("response_code")
        if not isinstance(response_code, str):
            return
        response_session_id = optional_session_id(message.get("session_id"))

        if sender in self._connected_assets:
            log.debug("[ROUTER] Ignoring challenge response from already connected asset %s", sender)
            self._provision_sessions.pop(sender, None)
            return

        session = self._provision_sessions.get(sender)
        if session is None:
            await self._send(
                PROVISION_REJECTED,
                destination=sender,
                reason="challenge_not_issued",
            )
            return
        session.last_activity_at = time.monotonic()

        if response_session_id and response_session_id != session.session_id:
            await self._send(
                PROVISION_REJECTED,
                destination=sender,
                reason="invalid_session_id",
                session_id=session.session_id,
            )
            log.warning(
                "[ROUTER] Invalid session response from %s (got=%s expected=%s)",
                sender,
                response_session_id,
                session.session_id,
            )
            return

        if response_code != self._expected_response_code:
            await self._send(
                PROVISION_REJECTED,
                destination=sender,
                reason="invalid_response_code",
                session_id=session.session_id,
            )
            self._provision_sessions.pop(sender, None)
            log.warning("[ROUTER] Invalid challenge response from %s (session=%s)", sender, session.session_id)
            self._log_interaction("PROVISION_REJECTED", f"asset={sender} reason=invalid_response_code")
            return

        sent = await self._send_credentials(sender, session)
        if not sent:
            return
        log.info("[ROUTER] Provision channel config sent to %s (session=%s)", sender, session.session_id)

    async def _gateway_identity(self) -> str:
        if self._gateway_id:
            return self._gateway_id

        node_id = await self._radio.get_node_id()
        if node_id:
            self._gateway_id = str(node_id)
            return self._gateway_id

        self._gateway_id = "atlas-gateway"
        return self._gateway_id

    async def _resolve_command_channel_url(self) -> str | None:
        if self._command_channel_url:
            return self._command_channel_url

        channel_url = await self._radio.get_channel_url()
        if channel_url:
            self._command_channel_url = str(channel_url)
        return self._command_channel_url

    async def _send(self, op: str, *, destination: str, **fields: Any) -> None:
        payload = encode_discovery_message(op, **fields)
        await self._radio.send(payload, destination=destination)

    async def _send_credentials(self, sender: str, session: _ProvisionSession) -> bool:
        channel_url = await self._resolve_command_channel_url()
        if not channel_url:
            await self._send(
                PROVISION_REJECTED,
                destination=sender,
                reason="gateway_channel_unavailable",
                session_id=session.session_id,
            )
            self._provision_sessions.pop(sender, None)
            log.warning("[ROUTER] Cannot provision %s - command channel URL unavailable", sender)
            return False

        await self._send(
            PROVISION_CREDENTIALS,
            destination=sender,
            gateway_id=await self._gateway_identity(),
            channel_url=channel_url,
            session_id=session.session_id,
        )
        now = time.monotonic()
        session.credentials_sent_at = now
        session.last_activity_at = now
        self._log_interaction("CREDENTIALS_SENT", f"asset={sender} session={session.session_id}")
        return True

    def _emit_assets_changed(self) -> None:
        if self._on_assets_changed is None:
            return
        try:
            self._on_assets_changed(sorted(self._connected_assets))
        except (RuntimeError, TypeError, ValueError):
            log.debug("[ROUTER] on_assets_changed callback failed", exc_info=True)

    def _mark_asset_activity(self, sender: str) -> None:
        if sender in self._connected_assets:
            self._asset_last_seen[sender] = time.monotonic()

    def _expire_stale_assets(self) -> None:
        now = time.monotonic()
        expired = [
            asset_id
            for asset_id in self._connected_assets
            if now - self._asset_last_seen.get(asset_id, now) > self._asset_lease_timeout_seconds
        ]
        if not expired:
            return

        for asset_id in expired:
            self._connected_assets.discard(asset_id)
            self._asset_last_seen.pop(asset_id, None)
            log.warning(
                "[ROUTER] Asset lease expired for %s after %.1fs of silence",
                asset_id,
                self._asset_lease_timeout_seconds,
            )
            self._log_interaction("LEASE_EXPIRED", f"asset={asset_id} timeout={self._asset_lease_timeout_seconds:.1f}s")
        self._emit_assets_changed()

    def _handle_provision_complete(self, sender: str, message: dict[str, Any]) -> None:
        complete_session_id = optional_session_id(message.get("session_id"))
        session = self._provision_sessions.get(sender)
        if session is not None:
            if complete_session_id and complete_session_id != session.session_id:
                log.debug(
                    "[ROUTER] Ignoring provision complete from %s with mismatched session %s (expected %s)",
                    sender,
                    complete_session_id,
                    session.session_id,
                )
                return
            self._provision_sessions.pop(sender, None)
        elif complete_session_id is not None:
            # Unknown session completion: likely stale traffic from previous attempt.
            log.debug(
                "[ROUTER] Ignoring provision complete from %s for unknown session %s",
                sender,
                complete_session_id,
            )
            return

        was_connected = sender in self._connected_assets
        self._connected_assets.add(sender)
        self._asset_last_seen[sender] = time.monotonic()
        if not was_connected:
            self._emit_assets_changed()
        log.info("[ROUTER] Provisioning completed for asset %s (session=%s)", sender, complete_session_id or "legacy")
        self._log_interaction("PROVISION_COMPLETE", f"asset={sender} session={complete_session_id or 'legacy'}")

    def _expire_stale_sessions(self) -> None:
        now = time.monotonic()
        expired = [
            sender
            for sender, session in self._provision_sessions.items()
            if now - session.last_activity_at > self._session_ttl_seconds
        ]
        for sender in expired:
            session = self._provision_sessions.pop(sender, None)
            if session is None:
                continue
            log.debug(
                "[ROUTER] Dropped stale provisioning session for %s (session=%s idle=%.1fs)",
                sender,
                session.session_id,
                now - session.last_activity_at,
            )

    async def _dispatch_business_message(self, raw: bytes, sender: str) -> None:
        if self._on_business_message is None:
            return
        result = self._on_business_message(raw, sender)
        if asyncio.iscoroutine(result):
            await result


def _new_session_id() -> str:
    return secrets.token_hex(8)
