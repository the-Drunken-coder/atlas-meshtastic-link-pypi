from __future__ import annotations

import json

from atlas_meshtastic_link.asset.intent_store import AssetIntentStore


def test_intent_store_creates_default(tmp_path):
    path = tmp_path / "intent.json"
    store = AssetIntentStore(path, asset_id="asset-1")
    loaded = store.load()
    assert loaded["asset_id"] == "asset-1"
    assert loaded["entity_type"] == "asset"
    assert isinstance(loaded["components"], dict)
    assert "entities" in loaded["subscriptions"]
    assert "tracks" not in loaded["subscriptions"]
    assert "geofeatures" not in loaded["subscriptions"]


def test_intent_store_change_detection(tmp_path):
    path = tmp_path / "intent.json"
    store = AssetIntentStore(path, asset_id="asset-1")
    changed, _ = store.changed_since_last_read()
    assert changed is False
    changed, _ = store.changed_since_last_read()
    assert changed is False
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["components"] = {"health": {"battery_percent": 90}, "status": {"value": "ready"}}
    path.write_text(json.dumps(raw), encoding="utf-8")
    changed, payload = store.changed_since_last_read()
    assert changed is True
    assert payload["components"]["health"]["battery_percent"] == 90
    assert payload["components"]["status"]["value"] == "ready"


def test_load_normalizes_legacy_supported_tasks_into_task_catalog(tmp_path):
    path = tmp_path / "intent.json"
    path.write_text(
        json.dumps(
            {
                "asset_id": "asset-1",
                "entity_type": "asset",
                "subtype": "rover",
                "components": {"supported_tasks": ["recon"]},
            }
        ),
        encoding="utf-8",
    )
    store = AssetIntentStore(path, asset_id="asset-1")

    loaded = store.load()

    assert "supported_tasks" not in loaded["components"]
    assert loaded["components"]["task_catalog"]["supported_tasks"] == ["recon"]


def test_load_preserves_non_list_legacy_supported_tasks(tmp_path):
    path = tmp_path / "intent.json"
    path.write_text(
        json.dumps(
            {
                "asset_id": "asset-1",
                "entity_type": "asset",
                "subtype": "rover",
                "components": {"supported_tasks": "recon"},
            }
        ),
        encoding="utf-8",
    )
    store = AssetIntentStore(path, asset_id="asset-1")

    loaded = store.load()

    assert loaded["components"]["supported_tasks"] == "recon"
