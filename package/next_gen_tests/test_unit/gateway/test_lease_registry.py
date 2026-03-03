"""Unit tests for gateway.lease_registry — GatewayLeaseManager."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from atlas_meshtastic_link.gateway.lease_registry import GatewayLeaseManager
from atlas_meshtastic_link.state.subscriptions import LeaseRegistry


def test_instantiation_creates_registry():
    mgr = GatewayLeaseManager(default_ttl_seconds=60.0)
    assert isinstance(mgr.registry, LeaseRegistry)


def test_process_subscription_request():
    async def _run() -> None:
        mgr = GatewayLeaseManager(default_ttl_seconds=60.0)
        await mgr.process_subscription_request("asset-1", "entities:e1")
        assert mgr.registry.is_active("asset-1", "entities:e1")

    asyncio.run(_run())


def test_process_subscription_request_ignores_empty_ids():
    async def _run() -> None:
        mgr = GatewayLeaseManager(default_ttl_seconds=60.0)
        await mgr.process_subscription_request("", "entities:e1")
        await mgr.process_subscription_request("asset-1", "")
        assert mgr.active_subscriptions("asset-1") == []

    asyncio.run(_run())


def test_process_subscription_set_replaces():
    async def _run() -> None:
        mgr = GatewayLeaseManager(default_ttl_seconds=60.0)
        await mgr.process_subscription_request("asset-1", "entities:old")
        await mgr.process_subscription_set("asset-1", {"entities:new1", "entities:new2"})
        subs = mgr.active_subscriptions("asset-1")
        assert "entities:old" not in subs
        assert "entities:new1" in subs
        assert "entities:new2" in subs

    asyncio.run(_run())


def test_process_subscription_set_ignores_empty_asset():
    async def _run() -> None:
        mgr = GatewayLeaseManager()
        await mgr.process_subscription_set("", {"entities:e1"})
        assert mgr.active_subscriptions("") == []

    asyncio.run(_run())


def test_active_subscriptions():
    mgr = GatewayLeaseManager(default_ttl_seconds=60.0)
    mgr.registry.register("asset-1", "entities:e1")
    mgr.registry.register("asset-1", "tasks:t1")
    result = mgr.active_subscriptions("asset-1")
    assert sorted(result) == ["entities:e1", "tasks:t1"]


def test_subscribers_for():
    mgr = GatewayLeaseManager(default_ttl_seconds=60.0)
    mgr.registry.register("asset-1", "entities:e1")
    mgr.registry.register("asset-2", "entities:e1")
    result = mgr.subscribers_for("entities:e1")
    assert sorted(result) == ["asset-1", "asset-2"]


def test_expire_delegates_to_registry():
    mgr = GatewayLeaseManager(default_ttl_seconds=0.1)
    with patch("atlas_meshtastic_link.state.subscriptions.time.monotonic", return_value=10.0):
        mgr.registry.register("asset-1", "entities:e1", ttl=0.1)
    with patch("atlas_meshtastic_link.state.subscriptions.time.monotonic", return_value=10.2):
        removed = mgr.expire()
    assert ("asset-1", "entities:e1") in removed


def test_custom_registry_injection():
    reg = LeaseRegistry(default_ttl_seconds=120.0)
    mgr = GatewayLeaseManager(lease_registry=reg)
    assert mgr.registry is reg
