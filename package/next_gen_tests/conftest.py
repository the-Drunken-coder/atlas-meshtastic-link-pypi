"""Shared test fixtures for atlas_meshtastic_link."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from atlas_meshtastic_link.transport.discovery import discover_usb_ports
except ImportError:
    discover_usb_ports = None

try:
    from scripts.integration_tests.combo_harness import atlas_command_available
except ImportError:
    atlas_command_available = None


def pytest_collection_modifyitems(config, items):
    """Skip hardware and pi tests when fewer than two Meshtastic USB radios detected.
    Skip pi tests when Atlas Command is not running locally."""
    if config.pluginmanager.get_plugin("xdist"):
        if getattr(config.option, "dist", None) != "loadgroup":
            config.option.dist = "loadgroup"
        xdist_group = getattr(pytest.mark, "xdist_group", None)
        if xdist_group:
            for item in items:
                if "pi" in item.keywords or "hardware" in item.keywords:
                    item.add_marker(xdist_group("radio"))
    if discover_usb_ports is None:
        return
    reason = None
    try:
        ports = discover_usb_ports()
    except ImportError as exc:
        ports = []
        reason = str(exc)
    except Exception:
        ports = []
    if len(ports) < 2:
        if reason is None:
            reason = (
                "no Meshtastic USB radios detected"
                if not ports
                else "requires at least two Meshtastic USB radios"
            )
        skip_marker = pytest.mark.skip(reason=reason)
        for item in items:
            if "hardware" in item.keywords or "pi" in item.keywords:
                item.add_marker(skip_marker)
        return

    # Pi tests also require Atlas Command running locally
    if atlas_command_available is not None and not atlas_command_available():
        skip_marker = pytest.mark.skip(
            reason="Atlas Command not running locally (http://localhost:8000)"
        )
        for item in items:
            if "pi" in item.keywords:
                item.add_marker(skip_marker)


@pytest.fixture()
def tmp_config(tmp_path: Path) -> Path:
    """Write a minimal config JSON and return its path."""
    config = {
        "mode": "gateway",
        "mode_profile": "general",
        "log_level": "DEBUG",
        "radio": {"auto_discover": True},
        "transport": {"segment_size": 200},
        "gateway": {"api_base_url": "http://localhost:8000"},
        "asset": {"world_state_path": str(tmp_path / "world_state.json")},
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path
