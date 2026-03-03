"""JSON wire helpers for post-provision broadcast billboard messages."""
from __future__ import annotations

import hashlib
import json
from typing import Any

ASSET_INTENT = "atlas.intent"
ASSET_INTENT_DIFF = "atlas.intent.diff"
GATEWAY_UPDATE = "atlas.gateway.update"
DELETE_TOMBSTONE = "__delete__"
GATEWAY_INDEX = "atlas.gateway.index"


def encode_asset_intent(
    *,
    asset_id: str,
    subscriptions: dict[str, list[str]],
    intent_seq: int,
    intent_hash: str,
    generated_at_ms: int,
    expected_max_silence_ms: int,
    meta: dict[str, Any] | None = None,
    entity_type: str | None = None,
    subtype: str | None = None,
    alias: str | None = None,
    components: dict[str, Any] | None = None,
    tracks: list[dict[str, Any]] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "msg_type": ASSET_INTENT,
        "asset_id": asset_id,
        "subscriptions": subscriptions,
        "intent_seq": intent_seq,
        "intent_hash": intent_hash,
        "generated_at_ms": generated_at_ms,
        "expected_max_silence_ms": expected_max_silence_ms,
    }
    if meta is not None:
        payload["meta"] = meta
    if entity_type is not None:
        payload["entity_type"] = entity_type
    if subtype is not None:
        payload["subtype"] = subtype
    if alias is not None:
        payload["alias"] = alias
    if components is not None:
        payload["components"] = components
    if tracks is not None:
        payload["tracks"] = tracks
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encode_asset_intent_diff(
    *,
    asset_id: str,
    patch: dict[str, Any],
    intent_seq: int,
    intent_hash: str,
    base_hash: str,
    generated_at_ms: int,
    expected_max_silence_ms: int,
    meta: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "msg_type": ASSET_INTENT_DIFF,
        "asset_id": asset_id,
        "patch": patch,
        "intent_seq": intent_seq,
        "intent_hash": intent_hash,
        "base_hash": base_hash,
        "generated_at_ms": generated_at_ms,
        "expected_max_silence_ms": expected_max_silence_ms,
    }
    if meta is not None:
        payload["meta"] = meta
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encode_gateway_update(
    *,
    records: list[dict[str, Any]],
    meta: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "msg_type": GATEWAY_UPDATE,
        "records": records,
    }
    if meta is not None:
        payload["meta"] = meta
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encode_gateway_index(
    *,
    entity_ids: list[str],
    meta: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "msg_type": GATEWAY_INDEX,
        "entity_ids": entity_ids,
    }
    if meta is not None:
        payload["meta"] = meta
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def decode_billboard_message(raw: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    msg_type = parsed.get("msg_type")
    if msg_type not in {ASSET_INTENT, ASSET_INTENT_DIFF, GATEWAY_UPDATE, GATEWAY_INDEX}:
        return None
    if msg_type == ASSET_INTENT:
        if not _valid_intent_metadata(parsed):
            return None
    if msg_type == ASSET_INTENT_DIFF:
        if not _valid_intent_metadata(parsed):
            return None
        base_hash = parsed.get("base_hash")
        if not isinstance(base_hash, str) or not base_hash:
            return None
    return parsed


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def compute_intent_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def build_merge_diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for key in sorted(set(previous.keys()) | set(current.keys())):
        if key not in current:
            patch[key] = {DELETE_TOMBSTONE: True}
            continue
        if key not in previous:
            patch[key] = current[key]
            continue
        old_value = previous[key]
        new_value = current[key]
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            nested = build_merge_diff(old_value, new_value)
            if nested:
                patch[key] = nested
            continue
        if old_value != new_value:
            patch[key] = new_value
    return patch


def apply_merge_diff(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if _is_delete_marker(value):
            merged.pop(key, None)
            continue
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = apply_merge_diff(base_value, value)
            continue
        merged[key] = value
    return merged


def _is_delete_marker(value: Any) -> bool:
    return isinstance(value, dict) and value.get(DELETE_TOMBSTONE) is True and len(value) == 1


def _valid_intent_metadata(parsed: dict[str, Any]) -> bool:
    seq = parsed.get("intent_seq")
    if not isinstance(seq, int) or seq < 1:
        return False
    intent_hash = parsed.get("intent_hash")
    if not isinstance(intent_hash, str) or not intent_hash:
        return False
    generated_at_ms = parsed.get("generated_at_ms")
    if not isinstance(generated_at_ms, int) or generated_at_ms < 0:
        return False
    expected_max_silence_ms = parsed.get("expected_max_silence_ms")
    if not isinstance(expected_max_silence_ms, int) or expected_max_silence_ms < 1:
        return False
    return True
