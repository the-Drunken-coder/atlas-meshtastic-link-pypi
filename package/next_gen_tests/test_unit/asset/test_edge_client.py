"""Unit tests for asset.edge_client — EdgeClient."""
from __future__ import annotations

import asyncio
import json

from atlas_meshtastic_link.asset.edge_client import EdgeClient


def test_subscribe_and_unsubscribe_persist_subscriptions(tmp_path):
    async def _run() -> None:
        intent_path = tmp_path / "intent.json"
        client = EdgeClient(intent_path=intent_path, world_state_path=tmp_path / "world.json")

        await client.subscribe("entity-1")
        await client.subscribe("task-9", kind="tasks")
        await client.unsubscribe("entity-1")

        payload = json.loads(intent_path.read_text(encoding="utf-8"))
        assert payload["subscriptions"]["entities"] == []
        assert payload["subscriptions"]["tasks"] == ["self", "task-9"]
        assert payload["subscriptions"]["objects"] == []

    asyncio.run(_run())


def test_set_subscriptions_replaces_subscription_map(tmp_path):
    async def _run() -> None:
        intent_path = tmp_path / "intent.json"
        client = EdgeClient(intent_path=intent_path, world_state_path=tmp_path / "world.json")

        await client.subscribe("entity-legacy")
        await client.set_subscriptions(
            {"entities": ["entity-2"], "tasks": [], "objects": ["object-3"]}
        )

        payload = json.loads(intent_path.read_text(encoding="utf-8"))
        assert payload["subscriptions"] == {
            "entities": ["entity-2"],
            "tasks": [],
            "objects": ["object-3"],
        }

    asyncio.run(_run())


def test_set_components_and_update_component_persist_merged_state(tmp_path):
    async def _run() -> None:
        intent_path = tmp_path / "intent.json"
        client = EdgeClient(intent_path=intent_path, world_state_path=tmp_path / "world.json")

        await client.set_components({"telemetry": {"latitude": 1.0}})
        await client.update_component("health", {"status": "ok"})
        await client.update_component("telemetry", {"latitude": 2.0, "speed_m_s": 3.5})

        payload = json.loads(intent_path.read_text(encoding="utf-8"))
        assert payload["components"] == {
            "telemetry": {"latitude": 2.0, "speed_m_s": 3.5},
            "health": {"status": "ok"},
        }

    asyncio.run(_run())


def test_get_world_state_returns_empty_dict_for_missing_invalid_or_non_dict_files(tmp_path):
    missing_client = EdgeClient(
        intent_path=tmp_path / "intent.json",
        world_state_path=tmp_path / "missing-world.json",
    )
    assert missing_client.get_world_state() == {}

    invalid_world = tmp_path / "invalid-world.json"
    invalid_world.write_text("{not valid json", encoding="utf-8")
    invalid_client = EdgeClient(
        intent_path=tmp_path / "intent-invalid.json",
        world_state_path=invalid_world,
    )
    assert invalid_client.get_world_state() == {}

    list_world = tmp_path / "list-world.json"
    list_world.write_text('["not", "a", "dict"]', encoding="utf-8")
    list_client = EdgeClient(
        intent_path=tmp_path / "intent-list.json",
        world_state_path=list_world,
    )
    assert list_client.get_world_state() == {}


def test_send_command_appends_and_preserves_prior_commands(tmp_path):
    async def _run() -> None:
        intent_path = tmp_path / "intent.json"
        client = EdgeClient(intent_path=intent_path, world_state_path=tmp_path / "world.json")

        await client.set_components(
            {"custom_commands": [{"command_id": "existing", "payload": {"count": 1}}]}
        )
        await client.send_command("next", {"mode": "scan"})
        await client.send_command("final")

        payload = json.loads(intent_path.read_text(encoding="utf-8"))
        assert payload["components"]["custom_commands"] == [
            {"command_id": "existing", "payload": {"count": 1}},
            {"command_id": "next", "payload": {"mode": "scan"}},
            {"command_id": "final", "payload": {}},
        ]

    asyncio.run(_run())
