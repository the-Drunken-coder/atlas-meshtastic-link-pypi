"""Unit tests for gateway.router - GatewayRouter."""
from __future__ import annotations

import asyncio

import pytest

from atlas_meshtastic_link.gateway.router import GatewayRouter
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
)
from next_gen_tests.helpers.fake_radio import FakeRadio


def test_router_sets_ready_event_on_run_start():
    async def _run() -> None:
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers={})
        stop_event = asyncio.Event()
        ready_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            stop_event=stop_event,
            poll_seconds=0.05,
            ready_event=ready_event,
        )
        router_task = asyncio.create_task(router.run())
        await asyncio.wait_for(ready_event.wait(), timeout=1.0)
        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())


def test_router_handles_discovery_and_provision_success():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        assets_snapshots: list[list[str]] = []

        stop_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            gateway_id="gw-01",
            challenge_code="ATLAS_CHALLENGE",
            expected_response_code="ATLAS_RESPONSE",
            command_channel_url="meshtastic://atlas-command",
            asset_lease_timeout_seconds=45.0,
            stop_event=stop_event,
            poll_seconds=0.05,
            on_assets_changed=assets_snapshots.append,
        )
        router_task = asyncio.create_task(router.run())

        await asset_radio.send(encode_discovery_message(DISCOVERY_SEARCH), destination="^all")
        present_raw, present_sender = await asyncio.wait_for(asset_radio.receive(), timeout=1.0)
        present = decode_discovery_message(present_raw)
        assert present_sender == gateway_radio.node_id
        assert present is not None and present.get("op") == GATEWAY_PRESENT

        await asset_radio.send(encode_discovery_message(PROVISION_REQUEST), destination=gateway_radio.node_id)
        challenge_raw, challenge_sender = await asyncio.wait_for(asset_radio.receive(), timeout=1.0)
        challenge = decode_discovery_message(challenge_raw)
        assert challenge_sender == gateway_radio.node_id
        assert challenge is not None and challenge.get("op") == CHALLENGE
        assert challenge.get("challenge_code") == "ATLAS_CHALLENGE"
        assert isinstance(challenge.get("session_id"), str) and challenge.get("session_id")

        await asset_radio.send(
            encode_discovery_message(
                CHALLENGE_RESPONSE,
                response_code="ATLAS_RESPONSE",
                session_id=challenge.get("session_id"),
            ),
            destination=gateway_radio.node_id,
        )
        creds_raw, creds_sender = await asyncio.wait_for(asset_radio.receive(), timeout=1.0)
        creds = decode_discovery_message(creds_raw)
        assert creds_sender == gateway_radio.node_id
        assert creds is not None and creds.get("op") == PROVISION_CREDENTIALS
        assert creds.get("channel_url") == "meshtastic://atlas-command"
        assert creds.get("session_id") == challenge.get("session_id")

        await asset_radio.send(
            encode_discovery_message(PROVISION_COMPLETE),
            destination=gateway_radio.node_id,
        )
        await asyncio.sleep(0.1)
        assert assets_snapshots
        assert assets_snapshots[-1] == [asset_radio.node_id]

        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())


def test_router_rejects_invalid_response_code():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        stop_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            challenge_code="ATLAS_CHALLENGE",
            expected_response_code="ATLAS_RESPONSE",
            command_channel_url="meshtastic://atlas-command",
            asset_lease_timeout_seconds=45.0,
            stop_event=stop_event,
            poll_seconds=0.05,
        )
        router_task = asyncio.create_task(router.run())

        await asset_radio.send(encode_discovery_message(PROVISION_REQUEST), destination=gateway_radio.node_id)
        _ = await asyncio.wait_for(asset_radio.receive(), timeout=1.0)  # challenge

        await asset_radio.send(
            encode_discovery_message(CHALLENGE_RESPONSE, response_code="WRONG"),
            destination=gateway_radio.node_id,
        )
        rejected_raw, _ = await asyncio.wait_for(asset_radio.receive(), timeout=1.0)
        rejected = decode_discovery_message(rejected_raw)
        assert rejected is not None and rejected.get("op") == PROVISION_REJECTED
        assert rejected.get("reason") == "invalid_response_code"

        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())


def test_router_ignores_duplicate_provision_request_during_active_session():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        stop_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            challenge_code="ATLAS_CHALLENGE",
            expected_response_code="ATLAS_RESPONSE",
            command_channel_url="meshtastic://atlas-command",
            asset_lease_timeout_seconds=45.0,
            stop_event=stop_event,
            poll_seconds=0.05,
        )
        router_task = asyncio.create_task(router.run())

        await asset_radio.send(encode_discovery_message(PROVISION_REQUEST), destination=gateway_radio.node_id)
        first_challenge_raw, _ = await asyncio.wait_for(asset_radio.receive(), timeout=1.0)
        first_challenge = decode_discovery_message(first_challenge_raw)
        assert first_challenge is not None and first_challenge.get("op") == CHALLENGE

        await asset_radio.send(encode_discovery_message(PROVISION_REQUEST), destination=gateway_radio.node_id)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asset_radio.receive(), timeout=0.25)

        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())


def test_router_rejects_invalid_session_id():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        stop_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            challenge_code="ATLAS_CHALLENGE",
            expected_response_code="ATLAS_RESPONSE",
            command_channel_url="meshtastic://atlas-command",
            asset_lease_timeout_seconds=45.0,
            stop_event=stop_event,
            poll_seconds=0.05,
        )
        router_task = asyncio.create_task(router.run())

        await asset_radio.send(encode_discovery_message(PROVISION_REQUEST), destination=gateway_radio.node_id)
        challenge_raw, _ = await asyncio.wait_for(asset_radio.receive(), timeout=1.0)
        challenge = decode_discovery_message(challenge_raw)
        assert challenge is not None and challenge.get("op") == CHALLENGE

        await asset_radio.send(
            encode_discovery_message(
                CHALLENGE_RESPONSE,
                response_code="ATLAS_RESPONSE",
                session_id="bad-session-id",
            ),
            destination=gateway_radio.node_id,
        )
        rejected_raw, _ = await asyncio.wait_for(asset_radio.receive(), timeout=1.0)
        rejected = decode_discovery_message(rejected_raw)
        assert rejected is not None and rejected.get("op") == PROVISION_REJECTED
        assert rejected.get("reason") == "invalid_session_id"

        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())


def test_router_expires_silent_assets():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        assets_snapshots: list[list[str]] = []
        stop_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            challenge_code="ATLAS_CHALLENGE",
            expected_response_code="ATLAS_RESPONSE",
            command_channel_url="meshtastic://atlas-command",
            asset_lease_timeout_seconds=0.25,
            stop_event=stop_event,
            poll_seconds=0.05,
            on_assets_changed=assets_snapshots.append,
        )
        router_task = asyncio.create_task(router.run())

        await asset_radio.send(
            encode_discovery_message(PROVISION_COMPLETE),
            destination=gateway_radio.node_id,
        )
        await asyncio.sleep(0.1)
        assert assets_snapshots
        assert assets_snapshots[-1] == [asset_radio.node_id]

        await asyncio.sleep(0.35)
        assert assets_snapshots[-1] == []

        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())


def test_router_any_asset_traffic_refreshes_lease():
    async def _run() -> None:
        radios: dict[str, FakeRadio] = {}
        gateway_radio = FakeRadio(node_id="!gateway", channel_url="meshtastic://atlas-command", peers=radios)
        asset_radio = FakeRadio(node_id="!asset", channel_url="meshtastic://public", peers=radios)
        radios[gateway_radio.node_id] = gateway_radio
        radios[asset_radio.node_id] = asset_radio

        assets_snapshots: list[list[str]] = []
        stop_event = asyncio.Event()
        router = GatewayRouter(
            radio=gateway_radio,
            challenge_code="ATLAS_CHALLENGE",
            expected_response_code="ATLAS_RESPONSE",
            command_channel_url="meshtastic://atlas-command",
            asset_lease_timeout_seconds=0.3,
            stop_event=stop_event,
            poll_seconds=0.05,
            on_assets_changed=assets_snapshots.append,
        )
        router_task = asyncio.create_task(router.run())

        await asset_radio.send(encode_discovery_message(PROVISION_COMPLETE), destination=gateway_radio.node_id)
        await asyncio.sleep(0.1)
        assert assets_snapshots[-1] == [asset_radio.node_id]

        await asyncio.sleep(0.1)
        await asset_radio.send(b"not-json-but-valid-traffic", destination=gateway_radio.node_id)
        await asyncio.sleep(0.15)
        assert assets_snapshots[-1] == [asset_radio.node_id]

        await asyncio.sleep(0.35)
        assert assets_snapshots[-1] == []

        stop_event.set()
        await asyncio.wait_for(router_task, timeout=1.0)

    asyncio.run(_run())
