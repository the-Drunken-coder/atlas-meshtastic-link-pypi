"""Reliability strategy interfaces and basic windowed ACK tracking."""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class ReliabilityStrategy(Protocol):
    """Structural interface for reliability strategies."""

    async def send_reliable(self, data: bytes, destination: str | int | None) -> bool:
        """Send data with reliability guarantees. Return True on success."""
        ...

    def on_ack(self, msg_id: bytes) -> None:
        """Handle an incoming ACK for the given message ID."""
        ...

    def on_nack(self, msg_id: bytes) -> None:
        """Handle an incoming NACK for the given message ID."""
        ...


class WindowedReliability:
    """In-memory ACK tracker used by windowed transport implementations."""

    def __init__(self, *, round_trip_timeout_seconds: float = 1.0, max_round_trips: int = 6) -> None:
        self._round_trip_timeout_seconds = max(0.2, float(round_trip_timeout_seconds))
        self._max_round_trips = max(1, int(max_round_trips))
        self._pending: dict[bytes, asyncio.Event] = {}

    async def send_reliable(self, data: bytes, destination: str | int | None) -> bool:
        # Adapter-level send/resend behavior drives delivery; this strategy object
        # tracks ACK state only.
        return bool(data) or destination is not None

    def track_outbound(self, msg_id: bytes) -> None:
        self._pending[msg_id] = asyncio.Event()

    async def wait_for_ack(self, msg_id: bytes) -> bool:
        event = self._pending.get(msg_id)
        if event is None:
            return False

        for _ in range(self._max_round_trips):
            try:
                await asyncio.wait_for(event.wait(), timeout=self._round_trip_timeout_seconds)
                self._pending.pop(msg_id, None)
                return True
            except asyncio.TimeoutError:
                continue

        self._pending.pop(msg_id, None)
        return False

    def on_ack(self, msg_id: bytes) -> None:
        event = self._pending.get(msg_id)
        if event is not None:
            event.set()

    def on_nack(self, msg_id: bytes) -> None:
        if msg_id not in self._pending:
            self._pending[msg_id] = asyncio.Event()
