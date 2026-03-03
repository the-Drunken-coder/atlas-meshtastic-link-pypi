"""Unit tests for scripts.gateway_webui."""
from __future__ import annotations

import time

import pytest

TestClient = pytest.importorskip(
    "fastapi.testclient",
    reason="fastapi is optional; webui script tests require it",
).TestClient

from scripts import _webui_common, gateway_webui


def _wait_for_status(client: TestClient, target_state: str, timeout: float = 2.5) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get("/status").json()["state"]
        if state == target_state:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for status={target_state!r}")


def test_index_contains_gateway_title():
    client = TestClient(gateway_webui.create_gateway_app(auto_start=False))
    response = client.get("/")
    assert response.status_code == 200
    assert "ATLAS Gateway Test Console" in response.text
    assert "Connected assets" in response.text
    assert "Channel:" in response.text


def test_gateway_start_and_stop(monkeypatch):
    async def fake_async_main(cfg, on_ready, stop_event):  # noqa: ANN001
        on_ready()
        await stop_event.wait()

    monkeypatch.setattr(_webui_common, "_async_main", fake_async_main)
    client = TestClient(gateway_webui.create_gateway_app(auto_start=False))

    start_response = client.post(
        "/start",
        data={
            "radio_mode": "serial",
            "radio_port": "COM7",
            "auto_discover": "on",
            "gateway_api_base_url": "http://localhost:8000",
            "gateway_api_token": "",
            "spool_path": "",
            "log_level": "INFO",
        },
    )
    assert start_response.status_code == 200
    assert start_response.json()["ok"] is True
    _wait_for_status(client, "running")

    config_response = client.get("/config/effective")
    assert config_response.status_code == 200
    config = config_response.json()["config"]
    assert config["mode"] == "gateway"
    assert config["radio"]["port"] == "COM7"
    assert config["gateway"]["asset_lease_timeout_seconds"] == 45.0

    stop_response = client.post("/stop")
    assert stop_response.status_code == 200
    _wait_for_status(client, "stopped")


def test_gateway_start_validation_error():
    client = TestClient(gateway_webui.create_gateway_app(auto_start=False))
    response = client.post(
        "/start",
        data={
            "radio_mode": "serial",
            "gateway_api_base_url": "http://localhost:8000",
            "log_level": "INFO",
        },
    )
    assert response.status_code == 400
    assert "Provide a serial port or enable auto-discover" in response.json()["message"]


def test_gateway_rejects_cross_origin_post():
    client = TestClient(gateway_webui.create_gateway_app(auto_start=False))
    response = client.post("/stop", headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["ok"] is False


def test_gateway_asset_intents_endpoint_filters_connected():
    app = gateway_webui.create_gateway_app(auto_start=False)
    client = TestClient(app)

    app.state.controller.status_snapshot = lambda: {  # type: ignore[method-assign]
        "connected_assets": ["asset-1"],
        "gateway_asset_intents": {
            "asset-1": {"updated_at_epoch": 1.23, "payload": {"asset_id": "asset-1"}},
            "asset-2": {"updated_at_epoch": 2.34, "payload": {"asset_id": "asset-2"}},
        },
    }

    response = client.get("/assets/intents")
    assert response.status_code == 200
    payload = response.json()
    assert payload["connected_assets"] == ["asset-1"]
    assert len(payload["asset_intents"]) == 1
    assert payload["asset_intents"][0]["asset_id"] == "asset-1"
