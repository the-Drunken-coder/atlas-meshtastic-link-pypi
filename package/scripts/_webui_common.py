"""Shared helpers for local gateway/asset web UI scripts."""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from atlas_meshtastic_link._link import _async_main
from atlas_meshtastic_link.config.modes import load_mode_profile
from atlas_meshtastic_link.config.schema import (
    AssetConfig,
    GatewayConfig,
    LinkConfig,
    RadioConfig,
    TransportConfig,
    load_config,
)

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


def setup_script_logging(level: str = "INFO") -> None:
    """Configure process logging once for scripts."""
    root = logging.getLogger()
    if root.handlers:
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=LOG_FORMAT,
    )


class InMemoryLogBufferHandler(logging.Handler):
    """Thread-safe ring buffer for web UI log streaming."""

    def __init__(self, max_lines: int = 800) -> None:
        super().__init__()
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        with self._lock:
            self._lines.append(msg)

    def lines(self, limit: int = 200) -> list[str]:
        with self._lock:
            if limit <= 0:
                return []
            return list(self._lines)[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()


def install_log_capture(handler: InMemoryLogBufferHandler, logger_names: list[str]) -> None:
    """Attach in-memory log capture to selected logger trees."""
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        if handler not in logger.handlers:
            logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)


class LinkProcessController:
    """Owns lifecycle of a single link runner instance."""

    def __init__(self, mode: str, logger: logging.Logger) -> None:
        self._mode = mode
        self._logger = logger
        self._lock = threading.Lock()
        self._state = "stopped"
        self._last_error: str | None = None
        self._started_at: float | None = None
        self._config: LinkConfig | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._pending_stop = False
        self._channel_connected: bool | None = None
        self._channel_url: str | None = None
        self._connected_assets: list[str] = []
        self._asset_intent_path: str | None = None
        self._world_state_path: str | None = None
        self._gateway_asset_intents: dict[str, dict[str, Any]] = {}
        self._sync_health_by_asset: dict[str, dict[str, Any]] = {}
        self._sync_health_event: dict[str, Any] | None = None

    def start(self, config: LinkConfig) -> tuple[bool, str]:
        with self._lock:
            if self._state in {"starting", "running", "stopping"}:
                return False, f"{self._mode} link is already {self._state}."
            self._state = "starting"
            self._last_error = None
            self._started_at = None
            self._pending_stop = False
            self._channel_connected = None
            self._channel_url = None
            self._connected_assets = []
            self._config = config
            self._thread = threading.Thread(
                target=self._thread_main,
                args=(config,),
                name=f"atlas-{self._mode}-runner",
                daemon=True,
            )
            thread = self._thread

        thread.start()
        self._logger.info("[WEBUI] %s start requested", self._mode)
        return True, f"{self._mode} start requested."

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if self._state in {"stopped", "error"}:
                return False, f"{self._mode} link is already {self._state}."
            self._state = "stopping"
            self._pending_stop = True
            loop = self._loop
            stop_event = self._stop_event

        if loop is not None and stop_event is not None:
            loop.call_soon_threadsafe(stop_event.set)

        self._logger.info("[WEBUI] %s stop requested", self._mode)
        return True, f"{self._mode} stop requested."

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            uptime = None
            if self._started_at is not None:
                uptime = round(max(0.0, time.time() - self._started_at), 2)
            return {
                "mode": self._mode,
                "state": self._state,
                "last_error": self._last_error,
                "uptime_seconds": uptime,
                "channel_connected": self._channel_connected,
                "channel_url": self._channel_url,
                "connected_assets": list(self._connected_assets),
                "asset_intent_path": self._asset_intent_path,
                "world_state_path": self._world_state_path,
                "gateway_asset_intents": dict(self._gateway_asset_intents),
                "sync_health_by_asset": dict(self._sync_health_by_asset),
                "sync_health_event": dict(self._sync_health_event) if isinstance(self._sync_health_event, dict) else None,
            }

    def effective_config(self) -> dict[str, Any] | None:
        with self._lock:
            if self._config is None:
                return None
            return asdict(self._config)

    def _thread_main(self, config: LinkConfig) -> None:
        try:
            asyncio.run(self._thread_main_async(config))
        except Exception as exc:  # pragma: no cover - defensive outer boundary
            self._logger.exception("[WEBUI] %s runner crashed", self._mode)
            with self._lock:
                self._state = "error"
                self._last_error = str(exc)
                self._thread = None
                self._loop = None
                self._stop_event = None

    async def _thread_main_async(self, config: LinkConfig) -> None:
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        with self._lock:
            self._loop = loop
            self._stop_event = stop_event
            pending_stop = self._pending_stop

        if pending_stop:
            stop_event.set()

        def on_ready() -> None:
            with self._lock:
                if self._started_at is None:
                    self._started_at = time.time()
                if self._state == "starting":
                    self._state = "running"

        def on_status(payload: dict[str, Any]) -> None:
            with self._lock:
                if "channel_connected" in payload:
                    value = payload.get("channel_connected")
                    self._channel_connected = bool(value) if value is not None else None
                if "channel_url" in payload:
                    url = payload.get("channel_url")
                    self._channel_url = str(url) if url else None
                if "connected_assets" in payload:
                    assets = payload.get("connected_assets")
                    if isinstance(assets, list):
                        self._connected_assets = [str(item) for item in assets]
                if "asset_intent_path" in payload:
                    path = payload.get("asset_intent_path")
                    self._asset_intent_path = str(path) if path else None
                if "world_state_path" in payload:
                    path = payload.get("world_state_path")
                    self._world_state_path = str(path) if path else None
                if "gateway_asset_intents" in payload:
                    intents = payload.get("gateway_asset_intents")
                    if isinstance(intents, dict):
                        normalized: dict[str, dict[str, Any]] = {}
                        for asset_id, item in intents.items():
                            if isinstance(item, dict):
                                normalized[str(asset_id)] = dict(item)
                        self._gateway_asset_intents = normalized
                if "sync_health_by_asset" in payload:
                    sync_health = payload.get("sync_health_by_asset")
                    if isinstance(sync_health, dict):
                        normalized_sync: dict[str, dict[str, Any]] = {}
                        for asset_id, item in sync_health.items():
                            if isinstance(item, dict):
                                normalized_sync[str(asset_id)] = dict(item)
                        self._sync_health_by_asset = normalized_sync
                if "sync_health_event" in payload:
                    event = payload.get("sync_health_event")
                    if isinstance(event, dict):
                        self._sync_health_event = dict(event)

        try:
            kwargs: dict[str, Any] = {"on_ready": on_ready, "stop_event": stop_event}
            try:
                signature = inspect.signature(_async_main)
                if "status_hook" in signature.parameters:
                    kwargs["status_hook"] = on_status
            except (TypeError, ValueError):
                pass

            await _async_main(config, **kwargs)
        except Exception as exc:
            self._logger.exception("[WEBUI] %s runner failed", self._mode)
            with self._lock:
                self._state = "error"
                self._last_error = str(exc)
        finally:
            with self._lock:
                if self._state != "error":
                    self._state = "stopped"
                self._loop = None
                self._stop_event = None
                self._pending_stop = False
                self._started_at = None
                self._thread = None


def build_gateway_config(form: Mapping[str, Any]) -> LinkConfig:
    """Create a gateway-mode LinkConfig from form payload."""
    radio, transport, log_level, mode_profile = _build_common_config_sections(form)
    api_base_url = _require_text(form, "gateway_api_base_url", "Gateway API base URL")
    api_token = _optional_text(form.get("gateway_api_token"))
    lease_timeout = _float_field(form.get("asset_lease_timeout_seconds"), default=45.0, minimum=1.0)
    gateway = GatewayConfig(
        api_base_url=api_base_url,
        api_token=api_token,
        asset_lease_timeout_seconds=lease_timeout,
    )
    return LinkConfig(
        mode="gateway",
        mode_profile=mode_profile,
        log_level=log_level,
        radio=radio,
        transport=transport,
        gateway=gateway,
        asset=AssetConfig(),
    )


def build_asset_config(form: Mapping[str, Any]) -> LinkConfig:
    """Create an asset-mode LinkConfig from form payload."""
    radio, transport, log_level, mode_profile = _build_common_config_sections(form)
    entity_id = _optional_text(form.get("entity_id"))
    intent_path = _optional_text(form.get("intent_path")) or "./asset_intent.json"
    world_state_path = _optional_text(form.get("world_state_path")) or "./world_state.json"
    auto_provision = _checkbox(form, "auto_provision")
    asset = AssetConfig(
        entity_id=entity_id,
        intent_path=intent_path,
        world_state_path=world_state_path,
        auto_provision=auto_provision,
    )
    return LinkConfig(
        mode="asset",
        mode_profile=mode_profile,
        log_level=log_level,
        radio=radio,
        transport=transport,
        gateway=GatewayConfig(),
        asset=asset,
    )


def _build_common_config_sections(form: Mapping[str, Any]) -> tuple[RadioConfig, TransportConfig, str, str]:
    radio_mode = str(form.get("radio_mode", "serial")).strip().lower()
    if radio_mode != "serial":
        raise ValueError("Only serial radio mode is supported.")
    radio_port = _optional_text(form.get("radio_port"))
    auto_discover = _checkbox(form, "auto_discover")
    if not auto_discover and not radio_port:
        raise ValueError("Provide a serial port or enable auto-discover when using real serial mode.")

    mode_profile = _optional_text(form.get("mode_profile")) or "general"
    try:
        profile = load_mode_profile(mode_profile)
    except FileNotFoundError as exc:
        raise ValueError(f"Unknown mode profile: {mode_profile}") from exc

    segment_size = _int_field(profile.get("segment_size"), default=200, minimum=50)
    reliability_method = _optional_text(profile.get("reliability_method")) or "window"
    spool_path = _optional_text(form.get("spool_path"))
    log_level = (_optional_text(form.get("log_level")) or "INFO").upper()

    radio = RadioConfig(port=radio_port, auto_discover=auto_discover)
    transport = TransportConfig(
        segment_size=segment_size,
        spool_path=spool_path,
        reliability_method=reliability_method,
    )
    return radio, transport, log_level, mode_profile


def _checkbox(form: Mapping[str, Any], key: str) -> bool:
    value = form.get(key)
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_text(form: Mapping[str, Any], key: str, label: str) -> str:
    value = _optional_text(form.get(key))
    if not value:
        raise ValueError(f"{label} is required.")
    return value


def _int_field(value: Any, default: int, minimum: int) -> int:
    raw = _optional_text(value)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"Expected an integer value, got: {raw!r}") from exc
    if parsed < minimum:
        raise ValueError(f"Integer value must be at least {minimum}.")
    return parsed


def _float_field(value: Any, default: float, minimum: float) -> float:
    raw = _optional_text(value)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ValueError(f"Expected a numeric value, got: {raw!r}") from exc
    if parsed < minimum:
        raise ValueError(f"Numeric value must be at least {minimum}.")
    return parsed


def default_config_path(script_file: str, filename: str) -> Path:
    """Return scripts/config/<filename> path."""
    return Path(script_file).resolve().parent / "config" / filename


def validate_same_origin(request: Any) -> None:
    """Allow POST only from same-origin browser requests."""
    origin = request.headers.get("origin")
    if not origin:
        return
    parsed_origin = urlsplit(origin)
    expected_origin = urlsplit(str(request.base_url))
    origin_key = (parsed_origin.scheme.lower(), parsed_origin.netloc.lower())
    expected_key = (expected_origin.scheme.lower(), expected_origin.netloc.lower())
    if origin_key != expected_key:
        raise ValueError("Cross-origin POST rejected.")


def load_mode_config(config_path: str | Path, expected_mode: str) -> LinkConfig:
    """Load a mode-specific LinkConfig JSON file."""
    path = Path(config_path)
    cfg = load_config(path)
    if cfg.mode != expected_mode:
        raise ValueError(f"Expected config mode={expected_mode!r}, got {cfg.mode!r} in {path}.")
    if expected_mode == "asset":
        resolved_path = path.resolve()
        package_root = next(
            (parent for parent in resolved_path.parents if (parent / "pyproject.toml").exists()),
            resolved_path.parent,
        )
        if not Path(cfg.asset.intent_path).is_absolute():
            cfg.asset.intent_path = str(package_root / cfg.asset.intent_path)
        if not Path(cfg.asset.world_state_path).is_absolute():
            cfg.asset.world_state_path = str(package_root / cfg.asset.world_state_path)
    return cfg


async def autostart_serial_only(
    *,
    controller: LinkProcessController,
    config: LinkConfig,
    logger: logging.Logger,
    mode_name: str,
) -> None:
    """Start link on app startup using serial configuration only."""
    ok, message = controller.start(config)
    if not ok:
        logger.warning("[WEBUI] %s auto-start skipped: %s", mode_name, message)
        return
    logger.info("[WEBUI] %s auto-start requested (serial)", mode_name)
    await asyncio.sleep(0)
