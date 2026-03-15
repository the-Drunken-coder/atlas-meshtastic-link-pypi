"""Unit tests for scripts._webui_common."""
from __future__ import annotations

import asyncio
import json
import logging
import time

from atlas_meshtastic_link.config.schema import LinkConfig, RadioConfig
from scripts import _webui_common


def _wait_for_state(controller: _webui_common.LinkProcessController, target_state: str, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = controller.status_snapshot()["state"]
        if current == target_state:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for state {target_state!r}")


def test_log_buffer_handler_stores_recent_lines():
    handler = _webui_common.InMemoryLogBufferHandler(max_lines=2)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.webui_common.logs")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("line-1")
        logger.info("line-2")
        logger.info("line-3")
        assert handler.lines() == ["line-2", "line-3"]
    finally:
        logger.removeHandler(handler)


def test_controller_start_stop_cycle(monkeypatch):
    async def fake_async_main(cfg, on_ready, stop_event):  # noqa: ANN001
        on_ready()
        await stop_event.wait()

    monkeypatch.setattr(_webui_common, "_async_main", fake_async_main)
    controller = _webui_common.LinkProcessController(
        mode="gateway",
        logger=logging.getLogger("test.webui_common.controller"),
    )

    started, _ = controller.start(LinkConfig(mode="gateway"))
    assert started is True
    _wait_for_state(controller, "running")
    snapshot = controller.status_snapshot()
    assert "connected_assets" in snapshot
    assert snapshot["connected_assets"] == []
    assert "gateway_asset_intents" in snapshot

    stopped, _ = controller.stop()
    assert stopped is True
    _wait_for_state(controller, "stopped")


def test_controller_records_error_state(monkeypatch):
    async def failing_async_main(cfg, on_ready, stop_event):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr(_webui_common, "_async_main", failing_async_main)
    controller = _webui_common.LinkProcessController(
        mode="asset",
        logger=logging.getLogger("test.webui_common.error"),
    )

    started, _ = controller.start(LinkConfig(mode="asset"))
    assert started is True
    _wait_for_state(controller, "error")
    assert "boom" in (controller.status_snapshot()["last_error"] or "")


def test_autostart_serial_only(monkeypatch):
    async def fake_async_main(cfg, on_ready, stop_event):  # noqa: ANN001
        on_ready()
        await stop_event.wait()

    monkeypatch.setattr(_webui_common, "_async_main", fake_async_main)
    controller = _webui_common.LinkProcessController(
        mode="gateway",
        logger=logging.getLogger("test.webui_common.autostart"),
    )
    cfg = LinkConfig(mode="gateway", radio=RadioConfig(port="COM9", auto_discover=False))

    asyncio.run(
        _webui_common.autostart_serial_only(
            controller=controller,
            config=cfg,
            logger=logging.getLogger("test.webui_common.autostart"),
            mode_name="gateway",
        )
    )

    _wait_for_state(controller, "running")
    stopped, _ = controller.stop()
    assert stopped is True
    _wait_for_state(controller, "stopped")


def test_load_mode_config_resolves_asset_paths_from_package_root(tmp_path):
    package_root = tmp_path / "pkg"
    config_path = package_root / "scripts" / "config" / "asset_webui.json"
    config_path.parent.mkdir(parents=True)
    (package_root / "pyproject.toml").write_text("[project]\nname='pkg'\n", encoding="utf-8")
    config_path.write_text(
        json.dumps(
            {
                "mode": "asset",
                "asset": {
                    "intent_path": "./scripts/config/asset_intent.json",
                    "world_state_path": "./world_state.json",
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = _webui_common.load_mode_config(config_path, "asset")

    assert cfg.asset.intent_path == str(package_root / "scripts" / "config" / "asset_intent.json")
    assert cfg.asset.world_state_path == str(package_root / "world_state.json")
