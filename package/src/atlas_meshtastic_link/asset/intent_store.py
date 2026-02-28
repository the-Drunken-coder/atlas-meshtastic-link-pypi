"""File-backed intent store for asset user-written input."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from atlas_meshtastic_link.protocol.subscriptions import subscription_keys


def default_intent(asset_id: str | None = None) -> dict[str, Any]:
    return {
        "entity_type": "asset",
        "subtype": "rover",
        "asset_id": asset_id or "asset-1",
        "alias": asset_id or "asset-1",
        "components": {},
        "subscriptions": {
            "entities": [],
            "tasks": ["self"],
            "objects": [],
        },
        "meta": {},
    }


class AssetIntentStore:
    def __init__(self, path: str | Path, *, asset_id: str | None = None) -> None:
        self._path = Path(path)
        self._asset_id = asset_id
        self._last_hash: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def asset_id(self) -> str | None:
        return self._asset_id

    def reset(self) -> None:
        """Reset the intent file to defaults, preserving only asset_id."""
        payload = default_intent(self._asset_id)
        self.write(payload)
        self._last_hash = None

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            payload = default_intent(self._asset_id)
            self.write(payload)
            return payload
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = default_intent(self._asset_id)
            self.write(payload)
            return payload
        if not isinstance(parsed, dict):
            parsed = default_intent(self._asset_id)
        return self._normalize(parsed)

    def write(self, payload: dict[str, Any]) -> None:
        normalized = self._normalize(payload)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temp_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
        temp_path.replace(self._path)
        self._last_hash = self._content_hash(normalized)

    def changed_since_last_read(self) -> tuple[bool, dict[str, Any]]:
        payload = self.load()
        digest = self._content_hash(payload)
        changed = digest != self._last_hash
        self._last_hash = digest
        return changed, payload

    def subscription_keys(self) -> set[str]:
        payload = self.load()
        return subscription_keys(payload.get("subscriptions", {}))

    def set_subscription(self, kind: str, item_id: str, enabled: bool) -> dict[str, Any]:
        payload = self.load()
        subs = payload.setdefault("subscriptions", {})
        if not isinstance(subs, dict):
            subs = {}
            payload["subscriptions"] = subs
        current = subs.setdefault(kind, [])
        if not isinstance(current, list):
            current = []
            subs[kind] = current
        if enabled:
            if item_id not in current:
                current.append(item_id)
        else:
            subs[kind] = [value for value in current if value != item_id]
        self.write(payload)
        return payload

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = default_intent(self._asset_id)
        for key in ("entity_type", "subtype", "asset_id", "alias", "meta"):
            value = payload.get(key)
            if value is not None:
                normalized[key] = value
        components = payload.get("components")
        if isinstance(components, dict):
            components = dict(components)
            if "supported_tasks" in components and "task_catalog" not in components:
                tasks = components.get("supported_tasks")
                if isinstance(tasks, list):
                    components.pop("supported_tasks", None)
                    components["task_catalog"] = {"supported_tasks": tasks}
            normalized["components"] = components
        else:
            normalized["components"] = {}

        subs = payload.get("subscriptions")
        if isinstance(subs, dict):
            for kind, values in subs.items():
                if not isinstance(values, list):
                    continue
                if str(kind) in {"tracks", "geofeatures"}:
                    continue
                normalized["subscriptions"][str(kind)] = [str(item) for item in values if str(item).strip()]
        if not normalized.get("entity_type"):
            normalized["entity_type"] = "asset"
        if not normalized.get("subtype"):
            normalized["subtype"] = "rover"
        if not normalized.get("asset_id"):
            normalized["asset_id"] = self._asset_id or "asset-1"
        if not normalized.get("alias"):
            normalized["alias"] = normalized["asset_id"]
        return normalized

    def _content_hash(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
