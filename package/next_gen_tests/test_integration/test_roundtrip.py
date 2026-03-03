"""Integration test: repeated payload roundtrip over real radios."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from atlas_meshtastic_link.transport.serial_radio import SerialRadioAdapter


async def _await_node_id(radio: SerialRadioAdapter, timeout_seconds: float = 12.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        node_id = await radio.get_node_id()
        if node_id:
            return str(node_id)
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("timed out waiting for radio node id")
        await asyncio.sleep(0.25)


async def _await_payload(radio: SerialRadioAdapter, expected: bytes, timeout_seconds: float = 20.0) -> tuple[bytes, str]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for expected payload")
        payload, sender = await asyncio.wait_for(radio.receive(), timeout=remaining)
        if payload == expected:
            return payload, sender


@pytest.mark.hardware
def test_repeated_roundtrip(two_radio_ports: tuple[str, str]):
    """Send multiple payloads in both directions and verify they are delivered."""

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

            assert gateway_radio is not None
            assert asset_radio is not None
            gateway_id = await _await_node_id(gateway_radio)
            asset_id = await _await_node_id(asset_radio)

            for i in range(3):
                outbound = f"ATLAS_HW_LOOP_A2G::{i}::{uuid.uuid4().hex}".encode("utf-8")
                await asset_radio.send(outbound, destination=gateway_id)
                inbound, sender = await _await_payload(gateway_radio, outbound)
                assert inbound == outbound
                assert sender == asset_id

            for i in range(3):
                outbound = f"ATLAS_HW_LOOP_G2A::{i}::{uuid.uuid4().hex}".encode("utf-8")
                await gateway_radio.send(outbound, destination=asset_id)
                inbound, sender = await _await_payload(asset_radio, outbound)
                assert inbound == outbound
                assert sender == gateway_id
        finally:
            if gateway_radio is not None:
                await gateway_radio.close()
            if asset_radio is not None:
                await asset_radio.close()

    asyncio.run(_run())
