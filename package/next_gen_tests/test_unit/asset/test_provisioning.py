"""Unit tests for asset.provisioning - ProvisioningHandshake."""
from __future__ import annotations

import asyncio

from atlas_meshtastic_link.asset.provisioning import ProvisioningHandshake
from atlas_meshtastic_link.gateway.router import GatewayRouter
from atlas_meshtastic_link.protocol.discovery_wire import (
    CHALLENGE,
    DISCOVERY_SEARCH,
    GATEWAY_PRESENT,
    PROVISION_COMPLETE,
    PROVISION_CREDENTIALS,
    PROVISION_REQUEST,
    decode_discovery_message,
    encode_discovery_message,
)
from next_gen_tests.helpers.fake_radio import FakeRadio


def test_provisioning_handshake_success():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        stop_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            gateway_id="gw-01",
            challenge_code="ATLAS_CHALLENGE",
            expected_response_code="ATLAS_RESPONSE",
            command_channel_url="meshtastic://atlas-command",
            stop_event=stop_event,
            poll_seconds=0.05,
        )
        router_task = asyncio.create_task(router.run())

        handshake = ProvisioningHandshake(
            radio=asset_radio,
            asset_id="asset-01",
            expected_challenge_code="ATLAS_CHALLENGE",
            response_code="ATLAS_RESPONSE",
            timeout_seconds=2.0,
            discovery_interval_seconds=0.1,
            stop_event=stop_event,
        )
        success = await handshake.run()
        assert success is True
        assert asset_radio.channel_url == "meshtastic://atlas-command"

        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())


def test_provisioning_fails_with_challenge_mismatch():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        stop_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            challenge_code="DIFFERENT_CHALLENGE",
            expected_response_code="ATLAS_RESPONSE",
            command_channel_url="meshtastic://atlas-command",
            stop_event=stop_event,
            poll_seconds=0.05,
        )
        router_task = asyncio.create_task(router.run())

        handshake = ProvisioningHandshake(
            radio=asset_radio,
            expected_challenge_code="ATLAS_CHALLENGE",
            response_code="ATLAS_RESPONSE",
            timeout_seconds=0.8,
            discovery_interval_seconds=0.1,
            stop_event=stop_event,
        )
        success = await handshake.run()
        assert success is False
        assert asset_radio.channel_url == "meshtastic://public"

        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())


def test_provisioning_stops_request_retries_after_challenge():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        stop_event = asyncio.Event()
        request_count = 0
        saw_complete = asyncio.Event()

        async def gateway_driver() -> None:
            nonlocal request_count
            session_id = "session-abc123"
            while not stop_event.is_set():
                raw, sender = await gateway_radio.receive()
                message = decode_discovery_message(raw)
                if message is None:
                    continue
                op = message.get("op")
                if op == DISCOVERY_SEARCH:
                    await gateway_radio.send(
                        encode_discovery_message(GATEWAY_PRESENT, gateway_id="gw-01"),
                        destination=sender,
                    )
                elif op == PROVISION_REQUEST:
                    request_count += 1
                    if request_count == 1:
                        await gateway_radio.send(
                            encode_discovery_message(
                                CHALLENGE,
                                challenge_code="ATLAS_CHALLENGE",
                                gateway_id="gw-01",
                                session_id=session_id,
                            ),
                            destination=sender,
                        )
                        await asyncio.sleep(0.8)
                        await gateway_radio.send(
                            encode_discovery_message(
                                PROVISION_CREDENTIALS,
                                channel_url="meshtastic://atlas-command",
                                gateway_id="gw-01",
                                session_id=session_id,
                            ),
                            destination=sender,
                        )
                elif op == PROVISION_COMPLETE:
                    saw_complete.set()
                    return

        driver_task = asyncio.create_task(gateway_driver())

        handshake = ProvisioningHandshake(
            radio=asset_radio,
            asset_id="asset-01",
            expected_challenge_code="ATLAS_CHALLENGE",
            response_code="ATLAS_RESPONSE",
            timeout_seconds=3.0,
            discovery_interval_seconds=0.1,
            stop_event=stop_event,
        )
        success = await handshake.run()
        assert success is True
        assert asset_radio.channel_url == "meshtastic://atlas-command"
        await asyncio.wait_for(saw_complete.wait(), timeout=1.0)
        # Once challenge/response is underway, asset should not keep restarting request flow.
        assert request_count == 1

        stop_event.set()
        driver_task.cancel()
        await asyncio.gather(driver_task, return_exceptions=True)

    asyncio.run(_run())
