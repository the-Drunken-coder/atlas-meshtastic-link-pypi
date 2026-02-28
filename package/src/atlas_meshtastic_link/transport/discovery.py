"""USB auto-discovery for Meshtastic radios via pyserial VID/PID matching."""
from __future__ import annotations

import logging
from typing import NamedTuple

log = logging.getLogger(__name__)

# Known Meshtastic-compatible USB VID/PID pairs.
KNOWN_VID_PIDS: list[tuple[int, int, str]] = [
    (0x1A86, 0x7523, "CH340"),
    (0x1A86, 0x55D4, "CH9102X"),
    (0x10C4, 0xEA60, "CP2102"),
    (0x0403, 0x6001, "FTDI"),
    (0x303A, 0x1001, "Espressif"),
    (0x239A, 0x8029, "nRF52840"),
]


class PortInfo(NamedTuple):
    device: str
    description: str
    chip: str


def discover_usb_ports() -> list[PortInfo]:
    """Return all serial ports whose VID/PID matches a known Meshtastic chip."""
    try:
        from serial.tools.list_ports import comports
    except ImportError as exc:
        raise ImportError(
            "pyserial is required for USB discovery. Install it with: pip install pyserial"
        ) from exc

    results: list[PortInfo] = []
    for port in comports():
        for vid, pid, chip_name in KNOWN_VID_PIDS:
            if port.vid == vid and port.pid == pid:
                results.append(PortInfo(port.device, port.description, chip_name))
                log.info("[DISCOVERY] Found %s on %s (%s)", chip_name, port.device, port.description)
                break
    return results


def auto_select_port() -> str | None:
    """Return the first discovered Meshtastic port, or None if none found."""
    ports = discover_usb_ports()
    if not ports:
        log.warning("[DISCOVERY] No Meshtastic USB devices found")
        return None
    selected = ports[0].device
    log.info("[DISCOVERY] Auto-selected port: %s", selected)
    return selected
