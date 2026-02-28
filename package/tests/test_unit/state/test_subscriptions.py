"""Unit tests for state.subscriptions — LeaseRegistry."""
from __future__ import annotations

from atlas_meshtastic_link.state.subscriptions import LeaseRegistry


def test_lease_registry_instantiation():
    reg = LeaseRegistry(default_ttl_seconds=60.0)
    assert reg._default_ttl == 60.0
