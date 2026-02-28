"""Unit tests for state.world_state — WorldStateStore."""
from __future__ import annotations

from atlas_meshtastic_link.state.world_state import WorldStateStore


def test_get_put():
    store = WorldStateStore()
    store.put("key", "value")
    assert store.get("key") == "value"


def test_get_missing_returns_none():
    store = WorldStateStore()
    assert store.get("missing") is None


def test_upsert_section_record_with_subgroup_preserves_existing_structure():
    store = WorldStateStore()

    store.upsert_section_record(
        section="passive",
        group="gateway",
        subgroup="entities",
        record_id="entity-1",
        record={"id": "entity-1"},
    )
    store.upsert_section_record(
        section="passive",
        group="gateway",
        subgroup="tasks",
        record_id="task-1",
        record={"id": "task-1"},
    )

    passive = store.get("passive")
    assert isinstance(passive, dict)
    gateway = passive.get("gateway")
    assert isinstance(gateway, dict)
    assert gateway["entities"]["entity-1"]["id"] == "entity-1"
    assert gateway["tasks"]["task-1"]["id"] == "task-1"
