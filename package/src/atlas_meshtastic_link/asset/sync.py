"""Asset sync logic for gateway pushes and passive overhearing."""
from __future__ import annotations

import logging
import time
from typing import Any

from atlas_meshtastic_link.asset.intent_store import AssetIntentStore
from atlas_meshtastic_link.protocol.subscriptions import TASKS_SELF_KEY, subscription_keys
from atlas_meshtastic_link.state.overhearing import OverhearingFilter
from atlas_meshtastic_link.state.world_state import WorldStateStore

log = logging.getLogger(__name__)


class AssetSync:
    """Apply inbound mesh messages into world state sections."""

    def __init__(
        self,
        *,
        world_state: WorldStateStore,
        intent_store: AssetIntentStore,
        gateway_id: str | None = None,
    ) -> None:
        self._world_state = world_state
        self._intent_store = intent_store
        self._gateway_id = gateway_id
        self._overhearing = OverhearingFilter()

    async def handle_diff(self, diff_payload: dict[str, Any]) -> None:
        await self.handle_gateway_update(diff_payload, sender=self._gateway_id or "gateway")

    async def handle_gateway_update(self, payload: dict[str, Any], *, sender: str) -> None:
        intent_payload = self._intent_store.load()
        subscriptions = subscription_keys(intent_payload.get("subscriptions", {}))
        self._overhearing.set_subscriptions(subscriptions)
        raw_asset_id = (
            intent_payload.get("asset_id")
            or self._intent_store.asset_id
            or "asset-1"
        )
        asset_id = str(raw_asset_id)

        records = payload.get("records")
        if not isinstance(records, list):
            return

        now = time.time()
        for entry in records:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("kind", "")).strip() or "entities"
            if kind in {"tracks", "geofeatures"}:
                kind = "entities"
            item_id = str(entry.get("id", "")).strip()
            if not item_id:
                continue
            key = f"{kind}:{item_id}"
            record = {
                "kind": kind,
                "id": item_id,
                "data": entry.get("data"),
                "source": "gateway",
                "source_node": sender,
                "received_at": now,
                "version": entry.get("version"),
            }
            is_subscribed = key in subscriptions
            if not is_subscribed and kind == "tasks" and TASKS_SELF_KEY in subscriptions:
                data = entry.get("data")
                task_entity_id = data.get("entity_id") if isinstance(data, dict) else None
                is_subscribed = (
                    task_entity_id is not None and str(task_entity_id) == asset_id
                )
            if is_subscribed:
                self._world_state.upsert_section_record(
                    section="subscribed",
                    group=kind,
                    record_id=item_id,
                    record=record,
                )
            elif self._overhearing.should_ingest("gateway_update", key):
                subgroup = kind if kind in {"entities", "tasks", "objects"} else None
                rid = item_id if subgroup else key
                self._world_state.upsert_section_record(
                    section="passive",
                    group="gateway",
                    record_id=rid,
                    record=record,
                    subgroup=subgroup,
                )

    async def handle_gateway_index(self, payload: dict[str, Any], *, sender: str) -> None:
        entity_ids = payload.get("entity_ids", [])
        if not isinstance(entity_ids, list):
            return
        self._world_state.put(
            "index",
            {
                "entities": sorted(str(entity_id) for entity_id in entity_ids if entity_id),
                "updated_at": time.time(),
                "source_node": sender,
            },
        )

    async def handle_overheard_intent(self, payload: dict[str, Any], *, sender: str) -> None:
        components = payload.get("components")
        if not isinstance(components, dict):
            components = {}
        asset_id = str(payload.get("asset_id") or sender)
        self._world_state.upsert_section_record(
            section="passive",
            group="assets",
            record_id=asset_id,
            record={
                "asset_id": asset_id,
                "entity_type": payload.get("entity_type") or "asset",
                "subtype": payload.get("subtype"),
                "alias": payload.get("alias"),
                "source": "asset",
                "source_node": sender,
                "components": components,
                "subscriptions": payload.get("subscriptions"),
                "meta": payload.get("meta"),
                "received_at": time.time(),
            },
        )
