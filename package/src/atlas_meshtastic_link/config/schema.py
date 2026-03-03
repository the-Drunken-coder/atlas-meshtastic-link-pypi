"""LinkConfig dataclass hierarchy and JSON config loader."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from atlas_meshtastic_link.config.modes import load_mode_profile

log = logging.getLogger(__name__)

DEFAULT_CHALLENGE_CODE = "ATLAS_CHALLENGE"
DEFAULT_RESPONSE_CODE = "ATLAS_RESPONSE"
DEFAULT_GATEWAY_SECRETS_WARNING = (
    "[CONFIG] Gateway provisioning challenge/response codes are default values; set unique secrets before deployment."
)


class ConfigError(Exception):
    """Raised when configuration is invalid or cannot be loaded."""


@dataclass
class RadioConfig:
    port: Optional[str] = None
    auto_discover: bool = True


@dataclass
class TransportConfig:
    segment_size: int = 200
    spool_path: Optional[str] = None
    reliability_method: str = "window"


@dataclass
class GatewayConfig:
    api_base_url: str = "https://atlascommandapi.org"
    api_token: Optional[str] = None
    gateway_id: Optional[str] = None
    challenge_code: str = DEFAULT_CHALLENGE_CODE
    expected_response_code: str = DEFAULT_RESPONSE_CODE
    command_channel_url: Optional[str] = None
    asset_lease_timeout_seconds: float = 45.0
    api_poll_interval_seconds: float = 1.0
    publish_max_messages_per_second: float = 15.0
    index_broadcast_interval_seconds: float = 30.0
    index_diff_min_interval_seconds: float = 5.0
    asset_intent_ttl_seconds: float = 30.0
    passive_ttl_seconds: float = 300.0
    changed_since_limit_per_type: Optional[int] = None
    interaction_log_path: Optional[str] = None
    sync_stale_after_seconds: float = 10.0
    sync_health_summary_interval_seconds: float = 30.0


@dataclass
class AssetConfig:
    entity_id: Optional[str] = None
    intent_path: str = "./asset_intent.json"
    world_state_path: str = "./world_state.json"
    auto_provision: bool = True
    expected_challenge_code: str = DEFAULT_CHALLENGE_CODE
    response_code: str = DEFAULT_RESPONSE_CODE
    provision_timeout_seconds: float = 45.0
    discovery_interval_seconds: float = 3.0
    intent_poll_interval_seconds: float = 1.0
    publish_min_interval_seconds: float = 5.0
    intent_refresh_interval_seconds: float = 30.0
    intent_diff_enabled: bool = False
    world_state_flush_interval_seconds: float = 1.0
    passive_ttl_seconds: float = 300.0


@dataclass
class LinkConfig:
    mode: str = "gateway"
    mode_profile: str = "general"
    log_level: str = "INFO"
    radio: RadioConfig = field(default_factory=RadioConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    asset: AssetConfig = field(default_factory=AssetConfig)


def load_config(path_or_config: str | Path | LinkConfig) -> LinkConfig:
    """Load a LinkConfig from a JSON file path or return an existing instance.

    Raises ConfigError on missing file or invalid JSON.
    """
    if isinstance(path_or_config, LinkConfig):
        return path_or_config

    config_path = Path(path_or_config)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file must contain a JSON object, got {type(raw).__name__}")

    raw_radio = _section_dict(raw, "radio")
    if "simulate" in raw_radio:
        raise ConfigError("radio.simulate is not supported; configure a serial port or auto_discover.")
    try:
        radio = RadioConfig(**raw_radio)
    except TypeError as exc:
        raise ConfigError(f"Invalid radio config: {exc}") from exc
    mode_profile_raw = raw.get("mode_profile", "general")
    if mode_profile_raw is not None and not isinstance(mode_profile_raw, str):
        raise ConfigError(
            f"Config field 'mode_profile' must be a string when provided, got {type(mode_profile_raw).__name__}"
        )
    mode_profile = "general" if mode_profile_raw is None else mode_profile_raw.strip()
    if not mode_profile:
        mode_profile = "general"
    try:
        mode_defaults = load_mode_profile(mode_profile)
    except FileNotFoundError as exc:
        raise ConfigError(f"Mode profile not found: {mode_profile!r}") from exc

    raw_transport = _section_dict(raw, "transport")
    if "segment_size" not in raw_transport and "segment_size" in mode_defaults:
        raw_transport["segment_size"] = mode_defaults["segment_size"]
    if "reliability_method" not in raw_transport and "reliability_method" in mode_defaults:
        raw_transport["reliability_method"] = mode_defaults["reliability_method"]
    try:
        transport = TransportConfig(**raw_transport)
    except TypeError as exc:
        raise ConfigError(f"Invalid transport config: {exc}") from exc
    raw_gateway = _section_dict(raw, "gateway")
    raw_asset = _section_dict(raw, "asset")
    try:
        gateway = GatewayConfig(**raw_gateway)
    except TypeError as exc:
        raise ConfigError(f"Invalid gateway config: {exc}") from exc
    try:
        asset = AssetConfig(**raw_asset)
    except TypeError as exc:
        raise ConfigError(f"Invalid asset config: {exc}") from exc

    mode = raw.get("mode", "gateway")
    if (
        (mode == "gateway" or "gateway" in raw)
        and gateway.challenge_code == DEFAULT_CHALLENGE_CODE
        and gateway.expected_response_code == DEFAULT_RESPONSE_CODE
    ):
        log.warning(DEFAULT_GATEWAY_SECRETS_WARNING)

    cfg = LinkConfig(
        mode=mode,
        mode_profile=mode_profile,
        log_level=raw.get("log_level", "INFO"),
        radio=radio,
        transport=transport,
        gateway=gateway,
        asset=asset,
    )
    log.info("[CONFIG] Loaded config from %s (mode=%s)", config_path, cfg.mode)
    return cfg


def _section_dict(raw: dict[str, object], key: str) -> dict[str, object]:
    section = raw.get(key, {})
    if not isinstance(section, dict):
        raise ConfigError(f"Config section '{key}' must be a JSON object, got {type(section).__name__}")
    return dict(section)
