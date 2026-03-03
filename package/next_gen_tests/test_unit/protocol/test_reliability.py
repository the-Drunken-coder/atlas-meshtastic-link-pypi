"""Unit tests for protocol.reliability."""
from __future__ import annotations

import asyncio

from atlas_meshtastic_link.protocol.reliability import ReliabilityStrategy, WindowedReliability


def test_windowed_is_reliability_strategy():
    assert isinstance(WindowedReliability(), ReliabilityStrategy)


def test_windowed_ack_tracking():
    async def _run() -> None:
        strategy = WindowedReliability(round_trip_timeout_seconds=0.2, max_round_trips=3)
        msg_id = b"abc12345"
        strategy.track_outbound(msg_id)

        async def _ack_soon() -> None:
            await asyncio.sleep(0.05)
            strategy.on_ack(msg_id)

        ack_task = asyncio.create_task(_ack_soon())
        try:
            assert await strategy.wait_for_ack(msg_id) is True
        finally:
            await ack_task

    asyncio.run(_run())


def test_windowed_ack_timeout():
    async def _run() -> None:
        strategy = WindowedReliability(round_trip_timeout_seconds=0.05, max_round_trips=2)
        msg_id = b"abc12345"
        strategy.track_outbound(msg_id)
        assert await strategy.wait_for_ack(msg_id) is False

    asyncio.run(_run())
