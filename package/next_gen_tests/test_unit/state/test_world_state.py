"""Unit tests for state.world_state — WorldStateStore."""
from __future__ import annotations

import json

from atlas_meshtastic_link.state.world_state import WorldStateStore


def test_get_put():
    store = WorldStateStore()
    store.put("key", "value")
    assert store.get("key") == "value"


def test_get_missing_returns_none():
    store = WorldStateStore()
    assert store.get("missing") is None


def test_upsert_record_with_subgroup_preserves_existing_structure():
    store = WorldStateStore()

    store.upsert_record(
        group="entities",
        record_id="entity-1",
        record={"id": "entity-1"},
    )
    store.upsert_record(
        group="tasks",
        record_id="task-1",
        record={"id": "task-1"},
    )

    entities = store.get("entities")
    assert isinstance(entities, dict)
    assert entities["entity-1"]["id"] == "entity-1"
    
    tasks = store.get("tasks")
    assert isinstance(tasks, dict)
    assert tasks["task-1"]["id"] == "task-1"


def test_load_migrates_legacy_subscribed_and_passive_sections(tmp_path):
    path = tmp_path / "world_state.json"
    path.write_text(
        json.dumps(
            {
                "subscribed": {"tasks": {"task-1": {"id": "task-1"}}},
                "passive": {
                    "gateway": {"objects": {"obj-1": {"id": "obj-1"}}},
                    "assets": {"entity-1": {"id": "entity-1"}},
                },
            }
        ),
        encoding="utf-8",
    )
    store = WorldStateStore(path)
    store.load()

    assert store.get("tasks")["task-1"]["id"] == "task-1"
    assert store.get("objects")["obj-1"]["id"] == "obj-1"
    assert store.get("entities")["entity-1"]["id"] == "entity-1"


def test_load_migration_subscribed_wins_over_passive_on_overlap(tmp_path):
    """When both subscribed and passive contain the same record ID, subscribed wins."""
    path = tmp_path / "world_state.json"
    path.write_text(
        json.dumps(
            {
                "subscribed": {"tasks": {"task-1": {"id": "task-1", "source": "subscribed"}}},
                "passive": {
                    "gateway": {"tasks": {"task-1": {"id": "task-1", "source": "passive"}}},
                },
            }
        ),
        encoding="utf-8",
    )
    store = WorldStateStore(path)
    store.load()

    assert store.get("tasks")["task-1"]["source"] == "subscribed"


def test_load_migration_canonical_wins_over_legacy(tmp_path):
    """Top-level canonical records are preserved over legacy subscribed/passive."""
    path = tmp_path / "world_state.json"
    path.write_text(
        json.dumps(
            {
                "tasks": {"task-1": {"id": "task-1", "source": "canonical"}},
                "subscribed": {"tasks": {"task-1": {"id": "task-1", "source": "subscribed"}}},
                "passive": {
                    "gateway": {"tasks": {"task-1": {"id": "task-1", "source": "passive"}}},
                },
            }
        ),
        encoding="utf-8",
    )
    store = WorldStateStore(path)
    store.load()

    assert store.get("tasks")["task-1"]["source"] == "canonical"
