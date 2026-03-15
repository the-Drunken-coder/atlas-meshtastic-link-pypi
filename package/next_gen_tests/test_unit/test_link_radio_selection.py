"""Unit tests for _link radio auto-discovery selection behavior."""
from __future__ import annotations

import pytest
from atlas_meshtastic_link import _link
from atlas_meshtastic_link.config.schema import LinkConfig, RadioConfig
from atlas_meshtastic_link.transport.discovery import PortInfo


def test_build_radio_auto_discovery_skips_busy_ports(monkeypatch):
    attempts: list[str] = []
    segment_sizes: list[int] = []
    reliability_methods: list[str] = []

    class FakeAdapter:
        def __init__(self, port: str, **kwargs) -> None:  # noqa: ANN003
            attempts.append(port)
            segment_sizes.append(kwargs.get("segment_size"))
            reliability_methods.append(kwargs.get("reliability_method"))
            if port == "COM8":
                raise RuntimeError("Serial port COM8 is already in use by another process.")
            self.port = port

        async def close(self) -> None:  # pragma: no cover - defensive compatibility
            return None

    monkeypatch.setattr(
        "atlas_meshtastic_link.transport.discovery.discover_usb_ports",
        lambda: [
            PortInfo("COM8", "CP2102", "CP2102"),
            PortInfo("COM9", "CP2102", "CP2102"),
        ],
    )
    monkeypatch.setattr("atlas_meshtastic_link.transport.serial_radio.SerialRadioAdapter", FakeAdapter)

    cfg = LinkConfig(radio=RadioConfig(port=None, auto_discover=True))
    radio = _link._build_radio(cfg)
    assert attempts == ["COM8", "COM9"]
    assert segment_sizes == [200, 200]
    assert reliability_methods == ["window", "window"]
    assert getattr(radio, "port", None) == "COM9"


def test_build_radio_auto_discovery_raises_when_all_busy(monkeypatch):
    class BusyAdapter:
        def __init__(self, port: str, **kwargs) -> None:  # noqa: ANN003
            raise RuntimeError(f"Serial port {port} is already in use by another process.")

    monkeypatch.setattr(
        "atlas_meshtastic_link.transport.discovery.discover_usb_ports",
        lambda: [
            PortInfo("COM8", "CP2102", "CP2102"),
            PortInfo("COM9", "CP2102", "CP2102"),
        ],
    )
    monkeypatch.setattr("atlas_meshtastic_link.transport.serial_radio.SerialRadioAdapter", BusyAdapter)

    cfg = LinkConfig(radio=RadioConfig(port=None, auto_discover=True))
    with pytest.raises(RuntimeError, match="all discovered ports are in use"):
        _link._build_radio(cfg)
