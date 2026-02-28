"""Integration test configuration - hardware discovery and skip logic."""
from __future__ import annotations

import pytest

from atlas_meshtastic_link.transport.discovery import PortInfo, discover_usb_ports


def _discover_ports_safe() -> list[PortInfo]:
    try:
        return discover_usb_ports()
    except Exception:
        return []


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Auto-skip hardware tests only when no radios are detected."""
    ports = _discover_ports_safe()
    if ports:
        return

    skip_hw = pytest.mark.skip(reason="no Meshtastic USB radios detected")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip_hw)


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
