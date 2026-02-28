"""Edge client helpers for user code on asset nodes."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from atlas_meshtastic_link.asset.intent_store import AssetIntentStore

log = logging.getLogger(__name__)


class EdgeClient:
    """Thin file-based API for asset user code."""

    def __init__(
        self,
        *,
        intent_path: str | Path = "./asset_intent.json",
        world_state_path: str | Path = "./world_state.json",
        asset_id: str | None = None,
    ) -> None:
        self._intent_store = AssetIntentStore(intent_path, asset_id=asset_id)
        self._world_state_path = Path(world_state_path)

    async def subscribe(self, entity_id: str, *, kind: str = "entities") -> None:
        self._intent_store.set_subscription(kind, entity_id, True)

    async def unsubscribe(self, entity_id: str, *, kind: str = "entities") -> None:
        self._intent_store.set_subscription(kind, entity_id, False)

    async def set_subscriptions(self, subscriptions: dict[str, list[str]]) -> None:
        payload = self._intent_store.load()
        payload["subscriptions"] = subscriptions
        self._intent_store.write(payload)

    async def set_components(self, components: dict[str, Any]) -> None:
        current = self._intent_store.load()
        current["components"] = components
        self._intent_store.write(current)

    async def update_component(self, name: str, payload: dict[str, Any]) -> None:
        current = self._intent_store.load()
        components = current.get("components")
        if not isinstance(components, dict):
            components = {}
            current["components"] = components
        components[name] = payload
        self._intent_store.write(current)

    def get_world_state(self) -> dict[str, Any]:
        if not self._world_state_path.exists():
            return {}
        try:
            parsed = json.loads(self._world_state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to read world state from %s: %s", self._world_state_path, exc)
            return {}
        if not isinstance(parsed, dict):
            return {}
        return parsed

    def get_intent(self) -> dict[str, Any]:
        return self._intent_store.load()

    async def send_command(self, command_id: str, payload: dict | None = None) -> None:
        current = self._intent_store.load()
        components = current.get("components")
        if not isinstance(components, dict):
            components = {}
            current["components"] = components
        commands = components.get("custom_commands")
        if not isinstance(commands, list):
            commands = []
            components["custom_commands"] = commands
        commands.append({"command_id": command_id, "payload": payload or {}})
        self._intent_store.write(current)
