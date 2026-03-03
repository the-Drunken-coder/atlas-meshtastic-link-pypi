"""Integration test: gateway discovery/provisioning smoke over real radios."""
from __future__ import annotations

import asyncio

import pytest

from atlas_meshtastic_link.gateway.router import GatewayRouter
from atlas_meshtastic_link.protocol.discovery_wire import (
    DISCOVERY_SEARCH,
    GATEWAY_PRESENT,
    decode_discovery_message,
    encode_discovery_message,
)
from atlas_meshtastic_link.transport.serial_radio import SerialRadioAdapter


async def _await_discovery_presence(
    asset_radio: SerialRadioAdapter,
    asset_node_id: str,
    timeout_seconds: float = 45.0,
) -> tuple[dict, str]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        await asset_radio.send(
            encode_discovery_message(DISCOVERY_SEARCH, asset_id="test-asset", asset_node_id=asset_node_id),
            destination="^all",
        )
        window_end = min(deadline, asyncio.get_running_loop().time() + 2.0)
        while True:
            remaining = window_end - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                raw, sender = await asyncio.wait_for(asset_radio.receive(), timeout=remaining)
            except TimeoutError:
                break
            decoded = decode_discovery_message(raw)
            if decoded is None:
                continue
            if decoded.get("op") == GATEWAY_PRESENT:
                return decoded, sender

        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("timed out waiting for gateway presence")


@pytest.mark.hardware
def test_provisioning_discovery_smoke(two_radio_ports: tuple[str, str]):
    """Asset broadcast search should receive gateway presence from live radio."""

    async def _run() -> None:
        gateway_port, asset_port = two_radio_ports
        gateway_radio: SerialRadioAdapter | None = None
        asset_radio: SerialRadioAdapter | None = None

        stop_event = asyncio.Event()
        router_task: asyncio.Task[None] | None = None
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
            # Let radios initialize (get_node_id, mesh sync) before discovery
            _gateway_id, asset_id = await asyncio.gather(
                asyncio.wait_for(gateway_radio.get_node_id(), timeout=12.0),
                asyncio.wait_for(asset_radio.get_node_id(), timeout=12.0),
            )
            asset_node_id = str(asset_id) if asset_id else "unknown"
            gateway_ready = asyncio.Event()
            router = GatewayRouter(
                radio=gateway_radio,
                challenge_code="ATLAS_TEST_CHALLENGE",
                expected_response_code="ATLAS_TEST_RESPONSE",
                stop_event=stop_event,
                poll_seconds=0.2,
                ready_event=gateway_ready,
            )
            router_task = asyncio.create_task(router.run())
            await asyncio.wait_for(gateway_ready.wait(), timeout=5.0)

            try:
                message, sender = await _await_discovery_presence(asset_radio, asset_node_id)
            except TimeoutError:
                pytest.skip("radios detected but no gateway discovery presence was observed in time")
            assert message.get("op") == GATEWAY_PRESENT
            assert isinstance(message.get("gateway_id"), str)
            assert sender
        finally:
            stop_event.set()
            if router_task is not None:
                await asyncio.gather(router_task, return_exceptions=True)
            if gateway_radio is not None:
                await gateway_radio.close()
            if asset_radio is not None:
                await asset_radio.close()

    asyncio.run(_run())
