from __future__ import annotations

import asyncio
import json

from atlas_meshtastic_link.asset.intent_store import AssetIntentStore
from atlas_meshtastic_link.asset.sync import AssetSync
from atlas_meshtastic_link.state.world_state import WorldStateStore


def test_handle_gateway_update_stores_task_in_subscribed_for_tasks_self(tmp_path):
    """When asset has tasks:self, tasks with matching entity_id go to subscribed."""
    async def _run() -> None:
        intent_path = tmp_path / "intent.json"
        intent_path.write_text(
            json.dumps({
                "asset_id": "asset-1",
                "entity_type": "asset",
                "subtype": "rover",
                "subscriptions": {"entities": [], "tasks": ["self"], "objects": []},
            }),
            encoding="utf-8",
        )
        world = WorldStateStore()
        intent = AssetIntentStore(intent_path, asset_id="asset-1")
        sync = AssetSync(world_state=world, intent_store=intent)

        await sync.handle_gateway_update(
            {
                "records": [
                    {
                        "kind": "tasks",
                        "id": "task-1",
                        "data": {
                            "task_id": "task-1",
                            "entity_id": "asset-1",
                            "status": "pending",
                        },
                        "version": "2026-02-26T00:00:00Z",
                    }
                ]
            },
            sender="gateway-node",
        )

        tasks = world.get("tasks")
        assert isinstance(tasks, dict)
        assert "task-1" in tasks
        assert tasks["task-1"].get("data", {}).get("entity_id") == "asset-1"

    asyncio.run(_run())


def test_handle_gateway_index_stores_entity_ids(tmp_path):
    async def _run() -> None:
        world = WorldStateStore()
        intent = AssetIntentStore(tmp_path / "intent.json", asset_id="asset-1")
        sync = AssetSync(world_state=world, intent_store=intent)

        await sync.handle_gateway_index(
            {"entity_ids": ["e-2", "e-1", None, ""]},
            sender="gateway-node",
        )

        index = world.get("index")
        assert isinstance(index, dict)
        assert index.get("entities") == ["e-1", "e-2"]
        assert None not in index.get("entities")
        assert "" not in index.get("entities")
        assert index.get("source_node") == "gateway-node"
        assert index.get("updated_at") is not None

    asyncio.run(_run())


def test_handle_gateway_update_tasks_self_uses_store_asset_id_fallback(tmp_path):
    async def _run() -> None:
        intent_path = tmp_path / "intent.json"
        intent_path.write_text(
            json.dumps(
                {
                    "asset_id": "",
                    "entity_type": "asset",
                    "subtype": "rover",
                    "subscriptions": {"entities": [], "tasks": ["self"], "objects": []},
                }
            ),
            encoding="utf-8",
        )
        world = WorldStateStore()
        intent = AssetIntentStore(intent_path, asset_id="asset-9")
        sync = AssetSync(world_state=world, intent_store=intent)

        await sync.handle_gateway_update(
            {
                "records": [
                    {
                        "kind": "tasks",
                        "id": "task-9",
                        "data": {"task_id": "task-9", "entity_id": "asset-9"},
                    }
                ]
            },
            sender="gateway-node",
        )
        tasks = world.get("tasks")
        assert isinstance(tasks, dict)
        assert "task-9" in tasks

    asyncio.run(_run())
