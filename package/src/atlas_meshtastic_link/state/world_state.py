"""WorldStateStore — in-memory dict with atomic JSON flush."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class WorldStateStore:
    """In-memory world state with periodic flush to a JSON file.

    This store is not thread-safe; access it from a single asyncio event loop.

    Implementation deferred to business-logic phase.
    """

    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._persist_path = Path(persist_path) if persist_path else None
        self._data: dict[str, Any] = _default_world_state()

    def get(self, key: str) -> Any:
        return self._data.get(key)

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value

    def apply_diff(self, diff: dict[str, Any]) -> None:
        """Merge a diff dict into the store."""
        _deep_merge(self._data, diff)
        self._touch_meta()

    def flush(self) -> None:
        """Atomically write current state to disk."""
        if self._persist_path is None:
            return
        self._touch_meta()
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._persist_path.with_suffix(f"{self._persist_path.suffix}.tmp")
        temp_path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self._persist_path)

    def reset(self) -> None:
        """Reset to default empty state and flush to disk."""
        self._data = _default_world_state()
        self._touch_meta()
        self.flush()

    def load(self) -> None:
        """Load state from the persist file if it exists."""
        if self._persist_path is None or not self._persist_path.exists():
            return
        loaded = json.loads(self._persist_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return
        self._data = _default_world_state()
        _deep_merge(self._data, loaded)
        _normalize_world_state(self._data)
        self._touch_meta()

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data))

    def set_meta(self, **values: Any) -> None:
        meta = self._data.setdefault("meta", {})
        if not isinstance(meta, dict):
            meta = {}
            self._data["meta"] = meta
        meta.update(values)
        self._touch_meta()

    def upsert_record(
        self,
        *,
        group: str,
        record_id: str,
        record: dict[str, Any],
        subgroup: str | None = None,
    ) -> None:
        sections = self._data
        bucket = sections.setdefault(group, {})
        if not isinstance(bucket, dict):
            bucket = {}
            sections[group] = bucket
        if subgroup is not None:
            bucket = bucket.setdefault(subgroup, {})
            if not isinstance(bucket, dict):
                bucket = {}
                sections[group][subgroup] = bucket
        bucket[record_id] = record
        self._touch_meta()

    def prune_older_than(self, *, group: str, cutoff_epoch: float) -> int:
        sections = self._data
        bucket = sections.get(group, {})
        if not isinstance(bucket, dict):
            return 0
        removed = 0
        to_remove: list[str] = []
        for key, value in bucket.items():
            if not isinstance(value, dict):
                continue
            received_at = value.get("received_at")
            if isinstance(received_at, (int, float)) and float(received_at) < cutoff_epoch:
                to_remove.append(key)
        for key in to_remove:
            bucket.pop(key, None)
            removed += 1
        if removed:
            self._touch_meta()
        return removed

    def _touch_meta(self) -> None:
        meta = self._data.setdefault("meta", {})
        if isinstance(meta, dict):
            meta["last_updated_epoch"] = time.time()


def _default_world_state() -> dict[str, Any]:
    return {
        "meta": {},
        "index": {
            "entities": [],
            "updated_at": None,
            "source_node": None,
        },
        "entities": {},
        "tasks": {},
        "objects": {},
    }


def _deep_merge(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
            continue
        target[key] = value


def _normalize_world_state(payload: dict[str, Any]) -> None:
    """Structural normalization of canonical world-state shape."""
    for group in ("entities", "tasks", "objects"):
        bucket = payload.get(group)
        if not isinstance(bucket, dict):
            payload[group] = {}

    # Merge order matters: subscribed first, then passive.  When both legacy
    # sections contain the same record ID, subscribed wins because it is merged
    # first and _merge_legacy_records skips records already present in target.
    subscribed = payload.get("subscribed")
    if isinstance(subscribed, dict):
        for group in ("entities", "tasks", "objects"):
            _merge_legacy_records(payload[group], subscribed.get(group))

    passive = payload.get("passive")
    if isinstance(passive, dict):
        gateway = passive.get("gateway")
        if isinstance(gateway, dict):
            for group in ("entities", "tasks", "objects"):
                _merge_legacy_records(payload[group], gateway.get(group))
        _merge_legacy_records(payload["entities"], passive.get("assets"))


def _merge_legacy_records(target: dict[str, Any], source: Any) -> None:
    if not isinstance(source, dict):
        return
    for record_id, record in source.items():
        # Preserve canonical top-level records when both legacy and new keys exist.
        if record_id not in target:
            target[record_id] = record
