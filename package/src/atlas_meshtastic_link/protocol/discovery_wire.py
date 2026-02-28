"""Wire helpers for gateway discovery/provisioning messages."""
from __future__ import annotations

import json
from typing import Any

DISCOVERY_SEARCH = "atlas.discovery.search"
GATEWAY_PRESENT = "atlas.discovery.gateway_present"
PROVISION_REQUEST = "atlas.discovery.provision_request"
CHALLENGE = "atlas.discovery.challenge"
CHALLENGE_RESPONSE = "atlas.discovery.challenge_response"
PROVISION_CREDENTIALS = "atlas.discovery.provision_credentials"
PROVISION_REJECTED = "atlas.discovery.provision_rejected"
PROVISION_COMPLETE = "atlas.discovery.provision_complete"


def encode_discovery_message(op: str, **fields: Any) -> bytes:
    """Encode a provisioning message for radio transport."""
    payload = {"op": op, **fields}
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def decode_discovery_message(raw: bytes) -> dict[str, Any] | None:
    """Decode a provisioning message payload. Return None for invalid payloads."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    op = parsed.get("op")
    if not isinstance(op, str):
        return None
    return parsed


def optional_session_id(value: Any) -> str | None:
    """Normalize optional session IDs carried in discovery messages."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned
