"""Unit tests for asset.edge_client — EdgeClient."""
from __future__ import annotations

from atlas_meshtastic_link.asset.edge_client import EdgeClient


def test_edge_client_instantiation():
    client = EdgeClient()
    assert client is not None
