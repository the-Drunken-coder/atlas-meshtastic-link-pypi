"""Integration test: 1KB random payload transfer over two real radios."""
from __future__ import annotations

import asyncio
import secrets
import string

import pytest

from atlas_meshtastic_link.transport.serial_radio import SerialRadioAdapter

_ASCII_ALPHABET = string.ascii_letters + string.digits


def _random_ascii_payload(length: int = 1024) -> bytes:
    return "".join(secrets.choice(_ASCII_ALPHABET) for _ in range(length)).encode("ascii")


async def _await_node_id(radio: SerialRadioAdapter, timeout_seconds: float = 12.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        node_id = await radio.get_node_id()
        if node_id:
            return str(node_id)
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("timed out waiting for radio node id")
        await asyncio.sleep(0.25)


async def _await_payload(
    radio: SerialRadioAdapter,
    expected: bytes,
    timeout_seconds: float = 60.0,
) -> tuple[bytes, str]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for expected payload")
        payload, sender = await asyncio.wait_for(radio.receive(), timeout=remaining)
        if payload == expected:
            return payload, sender


@pytest.mark.hardware
def test_large_random_payload_roundtrip(two_radio_ports: tuple[str, str]):
    """Send 1KB random payloads in both directions and verify exact integrity."""

    async def _run() -> None:
        gateway_port, asset_port = two_radio_ports
        gateway_radio: SerialRadioAdapter | None = None
        asset_radio: SerialRadioAdapter | None = None
        try:
            try:
                gateway_radio = SerialRadioAdapter(gateway_port)
                asset_radio = SerialRadioAdapter(asset_port)
            except RuntimeError as exc:
                if "already in use" in str(exc):
                    pytest.skip(f"radio port busy; close other running radio apps and retry: {exc}")
                raise

            gateway_id = await _await_node_id(gateway_radio)
            asset_id = await _await_node_id(asset_radio)

            payload_to_gateway = _random_ascii_payload(1024)
            await asset_radio.send(payload_to_gateway, destination=gateway_id)
            inbound, sender = await _await_payload(gateway_radio, payload_to_gateway)
            assert inbound == payload_to_gateway
            assert sender == asset_id

            payload_to_asset = _random_ascii_payload(1024)
            await gateway_radio.send(payload_to_asset, destination=asset_id)
            inbound, sender = await _await_payload(asset_radio, payload_to_asset)
            assert inbound == payload_to_asset
            assert sender == gateway_id
        finally:
            if gateway_radio is not None:
                await gateway_radio.close()
            if asset_radio is not None:
                await asset_radio.close()

    asyncio.run(_run())
