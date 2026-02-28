"""Shared test fixtures for atlas_meshtastic_link."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

@pytest.fixture()
def tmp_config(tmp_path: Path) -> Path:
    """Write a minimal config JSON and return its path."""
    config = {
        "mode": "gateway",
        "mode_profile": "general",
        "log_level": "DEBUG",
        "radio": {"auto_discover": True},
        "transport": {"segment_size": 200},
        "gateway": {"api_base_url": "https://atlascommandapi.org"},
        "asset": {"world_state_path": str(tmp_path / "world_state.json")},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path
