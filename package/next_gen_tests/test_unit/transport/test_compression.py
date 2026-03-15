"""Unit tests for transport.compression."""
from __future__ import annotations

import json

from atlas_meshtastic_link.transport.compression import (
    _FIELD_ALIASES,
    _SHORT_TO_LONG,
    PREFIX_RAW,
    PREFIX_ZLIB,
    expand_keys,
    maybe_compress,
    maybe_decompress,
    shorten_keys,
)

# ---------------------------------------------------------------------------
# Compression tests
# ---------------------------------------------------------------------------


def test_compress_reduces_large_json():
    """A realistic 555-byte intent payload should compress smaller."""
    intent = {
        "type": "asset_intent",
        "asset_id": "!abcd1234",
        "callsign": "ALPHA-01",
        "lat": 38.897957,
        "lon": -77.036560,
        "alt": 10.0,
        "heading": 270,
        "speed": 0.0,
        "status": "operational",
        "mission": "patrol sector bravo",
        "timestamp": "2026-02-26T12:00:00Z",
        "extra_field_1": "padding to make this larger",
        "extra_field_2": "more padding data here",
        "extra_field_3": "even more padding data",
        "extra_field_4": "yet another padding field",
        "extra_field_5": "final padding to reach target size for realistic test",
    }
    raw = json.dumps(intent).encode("utf-8")
    assert len(raw) > 200

    result = maybe_compress(raw)
    assert result[0:1] == PREFIX_ZLIB
    assert len(result) < len(raw)


def test_compress_skips_tiny_payload():
    """Very small payloads should stay raw (prefix 0x00)."""
    tiny = b"hi"
    result = maybe_compress(tiny)
    assert result[0:1] == PREFIX_RAW
    assert result[1:] == tiny


def test_round_trip_compressed():
    """Compress → decompress round-trip for a compressible payload."""
    original = json.dumps({"key": "value" * 50}).encode("utf-8")
    compressed = maybe_compress(original)
    assert compressed[0:1] == PREFIX_ZLIB
    assert maybe_decompress(compressed) == original


def test_round_trip_uncompressed():
    """Compress → decompress round-trip for a non-compressible payload."""
    original = b"ab"
    wire = maybe_compress(original)
    assert wire[0:1] == PREFIX_RAW
    assert maybe_decompress(wire) == original


def test_decompress_rejects_unknown_prefix():
    """Unknown compression prefixes are rejected in strict mode."""
    try:
        maybe_decompress(b'{"type":"asset_intent"}')
    except ValueError as exc:
        assert "Unknown compression prefix" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown compression prefix")


def test_decompress_empty():
    """Empty bytes returns empty."""
    assert maybe_decompress(b"") == b""


# ---------------------------------------------------------------------------
# Field-name aliasing tests
# ---------------------------------------------------------------------------


def test_shorten_keys_replaces_known_fields():
    """Known long field names should be replaced with short aliases."""
    payload = json.dumps({"battery_percent": 85, "health": {"value": 100}}).encode()
    result = json.loads(shorten_keys(payload))
    assert "bp" in result
    assert "h" in result
    assert result["bp"] == 85
    assert result["h"] == {"v": 100}


def test_expand_keys_restores_original():
    """shorten → expand round-trip should preserve all data."""
    original = {
        "asset_id": "!abc",
        "components": {"health": {"battery_percent": 90}},
        "supported_tasks": ["recon"],
    }
    payload = json.dumps(original).encode()
    restored = json.loads(expand_keys(shorten_keys(payload)))
    assert restored == original


def test_shorten_keys_ignores_non_json():
    """Binary / non-JSON payloads pass through unchanged."""
    binary = b"\x00\x01\x02\xff"
    assert shorten_keys(binary) == binary


def test_shorten_keys_nested():
    """Aliasing should recurse into nested dicts and lists."""
    payload = json.dumps({
        "entities": [
            {"entity_type": "unit", "alias": "alpha"},
            {"entity_type": "base", "alias": "bravo"},
        ],
    }).encode()
    result = json.loads(shorten_keys(payload))
    assert "e" in result
    for item in result["e"]:
        assert "et" in item
        assert "a" in item
        assert "entity_type" not in item


def test_expand_keys_legacy_long_names():
    """A payload already using long field names should pass through expand unchanged."""
    original = {"battery_percent": 42, "alias": "ok"}
    payload = json.dumps(original).encode()
    # expand_keys should not mangle long names (they aren't in _SHORT_TO_LONG)
    result = json.loads(expand_keys(payload))
    assert result == original


def test_expand_keys_does_not_rewrite_short_keys_in_meta():
    """Open-ended metadata should keep short user keys unchanged."""
    payload = json.dumps({
        "mt": "atlas.intent",
        "ai": "!abc",
        "m": {
            "a": "custom-short-key",
            "e": {"a": 1},
        },
    }).encode()

    result = json.loads(expand_keys(payload))
    assert result["msg_type"] == "atlas.intent"
    assert result["asset_id"] == "!abc"
    assert result["meta"]["a"] == "custom-short-key"
    assert result["meta"]["e"] == {"a": 1}


def test_expand_keys_does_not_rewrite_short_keys_in_custom_component():
    """Unknown custom component payloads should not have keys expanded."""
    payload = json.dumps({
        "mt": "atlas.intent",
        "c": {
            "user_component": {
                "a": "short-key-should-stay",
                "e": {"a": 2},
            },
        },
    }).encode()

    result = json.loads(expand_keys(payload))
    component = result["components"]["user_component"]
    assert component["a"] == "short-key-should-stay"
    assert component["e"] == {"a": 2}


def test_alias_table_no_collisions():
    """All short aliases must be unique and not collide with any long field name."""
    # No duplicate short codes
    short_codes = list(_FIELD_ALIASES.values())
    assert len(short_codes) == len(set(short_codes)), "Duplicate short aliases found"

    # No short code is also used as a long field name
    long_names = set(_FIELD_ALIASES.keys())
    for short in short_codes:
        assert short not in long_names, f"Short alias '{short}' collides with a long field name"

    # Reverse map is consistent
    assert len(_SHORT_TO_LONG) == len(_FIELD_ALIASES)


def test_shorten_then_compress_smaller():
    """Aliased + compressed payload should be smaller than compressed-only."""
    intent = {
        "msg_type": "billboard",
        "asset_id": "!abcd1234",
        "components": {
            "communications": {"link_state": "connected"},
            "health": {"battery_percent": 78, "status": "nominal"},
            "telemetry": {
                "latitude": 38.897,
                "longitude": -77.036,
                "altitude_m": 10.0,
                "heading_deg": 270,
                "speed_m_s": 0.0,
            },
        },
        "task_catalog": {
            "supported_tasks": ["recon", "patrol"],
        },
        "subscriptions": {
            "entities": ["ent-1", "ent-2"],
            "objects": ["obj-1"],
        },
        "published_at": "2026-02-26T12:00:00Z",
        "last_update": "2026-02-26T12:00:00Z",
    }
    raw = json.dumps(intent).encode()
    compressed_only = maybe_compress(raw)

    shortened = shorten_keys(raw)
    aliased_compressed = maybe_compress(shortened)

    assert len(aliased_compressed) < len(compressed_only)
