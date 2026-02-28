"""Payload compression and field-name aliasing for wire payloads."""
from __future__ import annotations

import json
import zlib
from typing import Any

PREFIX_RAW = b"\x00"
PREFIX_ZLIB = b"\x01"

# ---------------------------------------------------------------------------
# Field-name aliasing — shrinks JSON keys before compression.
#
# New wire-format fields MUST be added here.
# Short aliases are chosen to avoid collisions with existing protocol fields.
# ---------------------------------------------------------------------------

_FIELD_ALIASES: dict[str, str] = {
    # Billboard fields
    "msg_type": "mt",
    "asset_id": "ai",
    "components": "c",
    "communications": "cm",
    "health": "h",
    "battery_percent": "bp",
    "last_update": "lu",
    "value": "v",
    "task_catalog": "tc",
    "supported_tasks": "st",
    "telemetry": "t",
    "altitude_m": "al",
    "heading_deg": "hd",
    "latitude": "la",
    "longitude": "lo",
    "speed_m_s": "sp",
    "published_at": "pa",
    "subscriptions": "su",
    "entities": "e",
    "objects": "o",
    "tasks": "tk",
    "subtype": "sb",
    "alias": "a",
    "patch": "p",
    "records": "r",
    "kind": "k",
    "data": "d",
    "version": "vr",
    "entity_ids": "ei",
    "__delete__": "_d",
    "custom_commands": "xc",
    "command_id": "ci",
    "payload": "pl",
    "entity_type": "et",
    "meta": "m",
    "updated_by": "ub",
    # Discovery fields
    "gateway_id": "gi",
    "asset_node_id": "an",
    "challenge_code": "cc",
    "response_code": "rc",
    "session_id": "si",
    "channel_url": "cu",
    "reason": "rn",
}

_SHORT_TO_LONG: dict[str, str] = {short: long for long, short in _FIELD_ALIASES.items()}

_OPAQUE_CONTAINER_FIELDS = {"meta", "data", "payload"}
_KNOWN_COMPONENT_FIELDS = {"communications", "health", "task_catalog", "telemetry", "custom_commands"}


def _long_key(key: str) -> str:
    return _SHORT_TO_LONG.get(key, key)


def _is_opaque_container(key: str) -> bool:
    return _long_key(key) in _OPAQUE_CONTAINER_FIELDS


def _is_known_component(key: str) -> bool:
    return _long_key(key) in _KNOWN_COMPONENT_FIELDS


def _transform_keys(
    obj: Any,
    key_map: dict[str, str],
    *,
    parent_key: str | None = None,
    blocked: bool = False,
) -> Any:
    """Recursively replace dict keys using *key_map*."""
    if isinstance(obj, dict):
        transformed: dict[str, Any] = {}
        parent_long = _long_key(parent_key) if parent_key is not None else None
        for key, value in obj.items():
            mapped_key = key if blocked else key_map.get(key, key)
            child_blocked = blocked or _is_opaque_container(key) or _is_opaque_container(mapped_key)

            # components is open-ended; only known protocol component payloads are traversed for key aliasing
            if parent_long == "components" and not _is_known_component(mapped_key):
                child_blocked = True

            transformed[mapped_key] = _transform_keys(
                value,
                key_map,
                parent_key=mapped_key,
                blocked=child_blocked,
            )
        return transformed
    if isinstance(obj, list):
        return [
            _transform_keys(item, key_map, parent_key=parent_key, blocked=blocked)
            for item in obj
        ]
    return obj


def shorten_keys(payload: bytes) -> bytes:
    """Replace known long field names with short aliases.

    Returns *payload* unchanged if it is not valid JSON.
    """
    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return payload
    transformed = _transform_keys(obj, _FIELD_ALIASES)
    return json.dumps(transformed, separators=(",", ":")).encode("utf-8")


def expand_keys(data: bytes) -> bytes:
    """Restore short aliases back to long field names.

    Returns *data* unchanged if it is not valid JSON.
    """
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return data
    transformed = _transform_keys(obj, _SHORT_TO_LONG)
    return json.dumps(transformed, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------


def maybe_compress(payload: bytes) -> bytes:
    """Compress payload if it reduces size, prepend 1-byte prefix."""
    compressed = zlib.compress(payload)
    if len(compressed) < len(payload):
        return PREFIX_ZLIB + compressed
    return PREFIX_RAW + payload


_MAX_DECOMPRESSED_SIZE = 1 * 1024 * 1024  # 1 MB — generous for mesh radio payloads


def maybe_decompress(data: bytes) -> bytes:
    """Strip 1-byte prefix and decompress if needed."""
    if not data:
        return data
    flag = data[0:1]
    body = data[1:]
    if flag == PREFIX_ZLIB:
        result = zlib.decompress(body)
        if len(result) > _MAX_DECOMPRESSED_SIZE:
            raise ValueError(
                f"Decompressed payload too large: {len(result)} bytes "
                f"(max {_MAX_DECOMPRESSED_SIZE})"
            )
        return result
    if flag == PREFIX_RAW:
        return body
    raise ValueError(f"Unknown compression prefix: {flag.hex()}")
