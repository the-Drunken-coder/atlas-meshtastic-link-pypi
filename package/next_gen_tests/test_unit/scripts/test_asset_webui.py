"""Unit tests for scripts.asset_webui."""
from __future__ import annotations

import time

import pytest

TestClient = pytest.importorskip(
    "fastapi.testclient",
    reason="fastapi is optional; webui script tests require it",
).TestClient

from scripts import _webui_common, asset_webui


def _wait_for_status(client: TestClient, target_state: str, timeout: float = 2.5) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get("/status").json()["state"]
        if state == target_state:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for status={target_state!r}")


def test_index_contains_asset_warning():
    client = TestClient(asset_webui.create_asset_app(auto_start=False))
    response = client.get("/")
    assert response.status_code == 200
    assert "ATLAS Asset Test Console" in response.text
    assert "diagnostics-focused" in response.text


def test_asset_start_and_stop(monkeypatch):
    async def fake_async_main(cfg, on_ready, stop_event):  # noqa: ANN001
        on_ready()
        await stop_event.wait()

    monkeypatch.setattr(_webui_common, "_async_main", fake_async_main)
    client = TestClient(asset_webui.create_asset_app(auto_start=False))

    start_response = client.post(
        "/start",
        data={
            "radio_mode": "serial",
            "radio_port": "COM8",
            "auto_discover": "on",
            "auto_provision": "on",
            "entity_id": "asset-01",
            "world_state_path": "./world_state.json",
            "spool_path": "",
            "log_level": "INFO",
        },
    )
    assert start_response.status_code == 200
    assert start_response.json()["ok"] is True
    _wait_for_status(client, "running")

    config_response = client.get("/config/effective")
    config = config_response.json()["config"]
    assert config["mode"] == "asset"
    assert config["asset"]["entity_id"] == "asset-01"

    stop_response = client.post("/stop")
    assert stop_response.status_code == 200
    _wait_for_status(client, "stopped")


def test_asset_intent_file_get_put(tmp_path):
    app = asset_webui.create_asset_app(auto_start=False)
    app.state.startup_config.asset.intent_path = str(tmp_path / "asset_intent.json")
    app.state.startup_config.asset.world_state_path = str(tmp_path / "world_state.json")
    client = TestClient(app)

    read_missing = client.get("/files/asset-intent")
    assert read_missing.status_code == 404
    assert read_missing.json()["ok"] is False

    write_response = client.put(
        "/files/asset-intent",
        json={"raw": '{"asset_id":"asset-01","subscriptions":{"entities":["e-1"]},"components":{},"meta":{}}'},
    )
    assert write_response.status_code == 200
    assert write_response.json()["ok"] is True

    read_after_write = client.get("/files/asset-intent")
    assert read_after_write.status_code == 200
    assert read_after_write.json()["content"]["asset_id"] == "asset-01"


def test_asset_rejects_cross_origin_post():
    client = TestClient(asset_webui.create_asset_app(auto_start=False))
    response = client.post("/stop", headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["ok"] is False


def test_world_state_file_read(tmp_path):
    app = asset_webui.create_asset_app(auto_start=False)
    app.state.startup_config.asset.intent_path = str(tmp_path / "asset_intent.json")
    app.state.startup_config.asset.world_state_path = str(tmp_path / "world_state.json")
    (tmp_path / "world_state.json").write_text('{"meta":{"x":1}}', encoding="utf-8")
    client = TestClient(app)

    response = client.get("/files/world-state")
    assert response.status_code == 200
    assert response.json()["content"]["meta"]["x"] == 1
