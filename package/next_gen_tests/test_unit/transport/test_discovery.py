"""Unit tests for transport.discovery — USB auto-discovery."""
from __future__ import annotations

from atlas_meshtastic_link.transport.discovery import KNOWN_VID_PIDS, PortInfo, discover_usb_ports


def test_known_vid_pids_not_empty():
    assert len(KNOWN_VID_PIDS) > 0


def test_port_info_fields():
    info = PortInfo(device="/dev/ttyUSB0", description="CH340", chip="CH340")
    assert info.device == "/dev/ttyUSB0"
    assert info.chip == "CH340"
