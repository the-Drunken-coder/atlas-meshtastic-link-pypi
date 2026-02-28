"""ProvisioningHandshake - gateway discovery state machine."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

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


class ProvisioningHandshake:
    """State machine for asset -> gateway provisioning."""

    def __init__(
        self,
        *,
        radio,  # noqa: ANN001
        asset_id: str | None = None,
        expected_challenge_code: str = "ATLAS_CHALLENGE",
        response_code: str = "ATLAS_RESPONSE",
        timeout_seconds: float = 45.0,
        discovery_interval_seconds: float = 3.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._radio = radio
        self._asset_id = asset_id
        self._expected_challenge_code = expected_challenge_code
        self._response_code = response_code
        self._timeout_seconds = max(1.0, timeout_seconds)
        self._discovery_interval_seconds = max(0.25, discovery_interval_seconds)
        self._stop_event = stop_event or asyncio.Event()
        self._max_challenge_response_retries = 3

    async def run(self) -> bool:
        """Execute the provisioning handshake. Return True on success."""
        deadline = time.monotonic() + self._timeout_seconds

        while not self._stop_event.is_set() and time.monotonic() < deadline:
            await self._broadcast_search()
            remaining = max(0.0, deadline - time.monotonic())
            wait_seconds = min(self._discovery_interval_seconds, remaining)
            found_gateway = await self._wait_for_gateway(wait_seconds)
            if found_gateway is None:
                continue

            if await self._provision_with_gateway(found_gateway, deadline):
                return True

        return False

    async def _broadcast_search(self) -> None:
        payload = encode_discovery_message(
            DISCOVERY_SEARCH,
            asset_id=self._asset_id,
            asset_node_id=await self._radio.get_node_id(),
        )
        await self._radio.send(payload, destination="^all")
        log.info("[PROVISION] Broadcast discovery search")

    async def _wait_for_gateway(self, timeout_seconds: float) -> str | None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            envelope = await self._receive_discovery_message(remaining)
            if envelope is None:
                return None

            message, sender = envelope
            if message.get("op") != GATEWAY_PRESENT:
                continue

            gateway_id = message.get("gateway_id")
            if gateway_id:
                log.info("[PROVISION] Gateway present from %s (%s)", sender, gateway_id)
            else:
                log.info("[PROVISION] Gateway present from %s", sender)
            return sender

        return None

    async def _provision_with_gateway(self, gateway_sender: str, deadline: float) -> bool:
        request_retry_seconds = max(0.5, self._discovery_interval_seconds)
        response_retry_seconds = request_retry_seconds
        next_request_at = 0.0
        next_response_retry_at = 0.0
        awaiting_credentials = False
        challenge_session_id: str | None = None
        challenge_response_attempts = 0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            now = time.monotonic()
            if not awaiting_credentials and now >= next_request_at:
                await self._send(
                    PROVISION_REQUEST,
                    destination=gateway_sender,
                    asset_id=self._asset_id,
                    asset_node_id=await self._radio.get_node_id(),
                )
                log.info("[PROVISION] Sent provision request to %s", gateway_sender)
                next_request_at = now + request_retry_seconds
            elif awaiting_credentials and now >= next_response_retry_at:
                if challenge_response_attempts >= self._max_challenge_response_retries:
                    log.warning(
                        "[PROVISION] Timed out waiting for credentials from %s (session=%s)",
                        gateway_sender,
                        challenge_session_id or "legacy",
                    )
                    return False
                challenge_response_attempts += 1
                await self._send(
                    CHALLENGE_RESPONSE,
                    destination=gateway_sender,
                    response_code=self._response_code,
                    session_id=challenge_session_id,
                )
                next_response_retry_at = now + response_retry_seconds
                log.info(
                    "[PROVISION] Re-sent challenge response to %s (session=%s attempt=%d/%d)",
                    gateway_sender,
                    challenge_session_id or "legacy",
                    challenge_response_attempts,
                    self._max_challenge_response_retries,
                )

            remaining = deadline - now
            poll_timeout = min(remaining, request_retry_seconds if not awaiting_credentials else response_retry_seconds)
            envelope = await self._receive_discovery_message(poll_timeout)
            if envelope is None:
                continue

            message, sender = envelope
            if sender != gateway_sender:
                continue

            op = message.get("op")
            if op == CHALLENGE:
                challenge_code = message.get("challenge_code")
                if challenge_code != self._expected_challenge_code:
                    log.warning("[PROVISION] Challenge mismatch from %s", gateway_sender)
                    return False
                incoming_session = optional_session_id(message.get("session_id"))
                if challenge_session_id and incoming_session and incoming_session != challenge_session_id:
                    log.debug(
                        "[PROVISION] Ignoring stale challenge from %s with mismatched session %s (expected %s)",
                        gateway_sender,
                        incoming_session,
                        challenge_session_id,
                    )
                    continue
                if challenge_session_id is None:
                    challenge_session_id = incoming_session

                challenge_response_attempts = 1
                await self._send(
                    CHALLENGE_RESPONSE,
                    destination=gateway_sender,
                    response_code=self._response_code,
                    session_id=challenge_session_id,
                )
                awaiting_credentials = True
                next_response_retry_at = now + response_retry_seconds
                log.info(
                    "[PROVISION] Challenge response sent to %s (session=%s)",
                    gateway_sender,
                    challenge_session_id or "legacy",
                )
                continue

            if op == PROVISION_REJECTED:
                reason = message.get("reason", "unspecified")
                log.warning("[PROVISION] Provisioning rejected by %s: %s", gateway_sender, reason)
                return False

            if op == PROVISION_CREDENTIALS:
                incoming_session = optional_session_id(message.get("session_id"))
                if awaiting_credentials and challenge_session_id and incoming_session and incoming_session != challenge_session_id:
                    log.debug(
                        "[PROVISION] Ignoring credentials from %s with mismatched session %s (expected %s)",
                        gateway_sender,
                        incoming_session,
                        challenge_session_id,
                    )
                    continue
                if not awaiting_credentials and incoming_session is not None:
                    # Defensive fallback for reordered traffic.
                    challenge_session_id = incoming_session

                channel_url = message.get("channel_url")
                if not isinstance(channel_url, str) or not channel_url.strip():
                    log.warning("[PROVISION] Provisioning credentials missing channel_url")
                    return False

                await self._radio.set_channel_url(channel_url)
                await self._send(
                    PROVISION_COMPLETE,
                    destination=gateway_sender,
                    asset_id=self._asset_id,
                    session_id=challenge_session_id,
                )
                log.info(
                    "[PROVISION] Applied command channel credentials from %s (session=%s)",
                    gateway_sender,
                    challenge_session_id or "legacy",
                )
                return True

        return False

    async def _receive_discovery_message(self, timeout_seconds: float) -> tuple[dict[str, Any], str] | None:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            try:
                raw, sender = await asyncio.wait_for(self._radio.receive(), timeout=remaining)
            except asyncio.TimeoutError:
                return None

            message = decode_discovery_message(raw)
            if message is None:
                continue
            return message, sender
        return None

    async def _send(self, op: str, *, destination: str, **fields: Any) -> None:
        payload = encode_discovery_message(op, **fields)
        await self._radio.send(payload, destination=destination)
