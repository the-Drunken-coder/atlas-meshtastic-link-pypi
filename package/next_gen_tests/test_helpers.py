"""Shared helpers for next_gen integration tests."""

from __future__ import annotations

import asyncio

from atlas_meshtastic_link.transport.serial_radio import SerialRadioAdapter


async def _await_payload(
    radio: SerialRadioAdapter, expected: bytes, timeout_seconds: float = 20.0
) -> tuple[bytes, str]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for expected payload")
        payload, sender = await asyncio.wait_for(radio.receive(), timeout=remaining)
        if payload == expected:
            return payload, sender
        await asyncio.sleep(0)
