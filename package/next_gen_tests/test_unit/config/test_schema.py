"""Unit tests for config.schema — LinkConfig and load_config."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from atlas_meshtastic_link.config.schema import (
    DEFAULT_GATEWAY_SECRETS_WARNING,
    ConfigError,
    LinkConfig,
    load_config,
)


def test_default_config():
    cfg = LinkConfig()
    assert cfg.mode == "gateway"
    assert cfg.radio.auto_discover is True
    assert cfg.transport.segment_size == 200
    assert cfg.gateway.asset_lease_timeout_seconds == 45.0
    assert cfg.asset.intent_refresh_interval_seconds == 30.0
    assert cfg.asset.intent_diff_enabled is False


def test_load_config_passthrough():
    cfg = LinkConfig(mode="asset")
    assert load_config(cfg) is cfg


def test_load_config_from_json(tmp_path: Path):
    data = {"mode": "asset", "radio": {"port": "COM9", "auto_discover": False}}
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.mode == "asset"
    assert cfg.radio.port == "COM9"
    assert cfg.radio.auto_discover is False


def test_load_config_rejects_simulate_field(tmp_path: Path):
    data = {"mode": "asset", "radio": {"simulate": True}}
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="radio.simulate is not supported"):
        load_config(p)


def test_load_config_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/path.json")


def test_load_config_invalid_json(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid JSON"):
        load_config(p)


def test_load_config_rejects_non_object_section(tmp_path: Path):
    p = tmp_path / "bad_section.json"
    p.write_text(json.dumps({"radio": "COM9"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="section 'radio' must be a JSON object"):
        load_config(p)


def test_load_config_rejects_unknown_radio_fields(tmp_path: Path):
    p = tmp_path / "unknown_radio_field.json"
    p.write_text(json.dumps({"radio": {"typo_port": "COM9"}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="Invalid radio config"):
        load_config(p)


def test_load_config_null_mode_profile_uses_general(tmp_path: Path, monkeypatch):
    seen_profile: dict[str, str] = {}

    def _load_profile(name: str) -> dict[str, object]:
        seen_profile["name"] = name
        return {}

    monkeypatch.setattr("atlas_meshtastic_link.config.schema.load_mode_profile", _load_profile)
    p = tmp_path / "null_mode_profile.json"
    p.write_text(json.dumps({"mode_profile": None}), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.mode_profile == "general"
    assert seen_profile["name"] == "general"


def test_load_config_rejects_non_string_mode_profile(tmp_path: Path):
    p = tmp_path / "bad_mode_profile_type.json"
    p.write_text(json.dumps({"mode_profile": 123}), encoding="utf-8")
    with pytest.raises(ConfigError, match="mode_profile' must be a string"):
        load_config(p)


def test_load_config_uses_mode_profile_transport_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "atlas_meshtastic_link.config.schema.load_mode_profile",
        lambda name: {"segment_size": 333, "reliability_method": "profile-window"},
    )
    data = {
        "mode": "gateway",
        "mode_profile": "general",
        "transport": {"spool_path": "./spool"},
    }
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.transport.segment_size == 333
    assert cfg.transport.reliability_method == "profile-window"


def test_asset_mode_no_gateway_section_skips_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    p = tmp_path / "asset_cfg.json"
    p.write_text(json.dumps({"mode": "asset"}), encoding="utf-8")
    load_config(p)
    assert DEFAULT_GATEWAY_SECRETS_WARNING not in caplog.text


def test_asset_mode_with_gateway_section_warns_default_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    p = tmp_path / "asset_cfg_with_gateway_section.json"
    p.write_text(json.dumps({"mode": "asset", "gateway": {}}), encoding="utf-8")
    load_config(p)
    assert DEFAULT_GATEWAY_SECRETS_WARNING in caplog.text


def test_gateway_mode_warns_default_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    p = tmp_path / "gateway_cfg.json"
    p.write_text(json.dumps({"mode": "gateway"}), encoding="utf-8")
    load_config(p)
    assert DEFAULT_GATEWAY_SECRETS_WARNING in caplog.text
