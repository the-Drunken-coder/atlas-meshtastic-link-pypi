"""Integration test configuration - hardware discovery and skip logic."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from atlas_meshtastic_link.transport.discovery import PortInfo, discover_usb_ports

# Ensure package root on path for combo_harness
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _discover_ports_safe() -> list[PortInfo]:
    try:
        return discover_usb_ports()
    except Exception:
        return []


@pytest.fixture(scope="session", autouse=True)
def _kill_stale_combo_before_hardware():
    """Kill leftover combo listeners only when radios are present for hardware tests."""
    if len(_discover_ports_safe()) < 2:
        return
    from scripts.integration_tests.combo_harness import kill_stale_port_listeners

    kill_stale_port_listeners([8840, 8841], log_prefix="[integration]")


@pytest.fixture(scope="session")
def detected_radios() -> list[PortInfo]:
    """Return discovered radios for hardware tests."""
    return _discover_ports_safe()


@pytest.fixture()
def two_radio_ports(detected_radios: list[PortInfo]) -> tuple[str, str]:
    """Return two radio port names or skip if fewer than two are present."""
    if len(detected_radios) < 2:
        pytest.skip("requires at least two Meshtastic USB radios")
    return detected_radios[0].device, detected_radios[1].device
