from __future__ import annotations

from atlas_meshtastic_link.protocol.billboard_wire import (
    ASSET_INTENT,
    ASSET_INTENT_DIFF,
    GATEWAY_INDEX,
    GATEWAY_UPDATE,
    apply_merge_diff,
    build_merge_diff,
    decode_billboard_message,
    encode_asset_intent,
    encode_asset_intent_diff,
    encode_gateway_index,
    encode_gateway_update,
)


def test_encode_decode_asset_intent():
    raw = encode_asset_intent(
        asset_id="asset-1",
        subscriptions={"entities": ["e1"]},
        intent_seq=1,
        intent_hash="abc123",
        generated_at_ms=1,
        expected_max_silence_ms=10000,
        components={"telemetry": {"latitude": 1.0}},
    )
    parsed = decode_billboard_message(raw)
    assert parsed is not None
    assert parsed["msg_type"] == ASSET_INTENT
    assert parsed["asset_id"] == "asset-1"

def test_encode_asset_intent_with_tracks():
    raw = encode_asset_intent(
        asset_id="asset-1",
        subscriptions={"entities": ["e1"]},
        intent_seq=2,
        intent_hash="def456",
        generated_at_ms=2,
        expected_max_silence_ms=10000,
        components={"telemetry": {"latitude": 2.0}},
        tracks=[{"entity_id": "track-1", "subtype": "person", "components": {}}]
    )
    parsed = decode_billboard_message(raw)
    assert parsed is not None
    assert parsed["msg_type"] == ASSET_INTENT
    assert parsed["asset_id"] == "asset-1"
    assert "tracks" in parsed
    assert len(parsed["tracks"]) == 1
    assert parsed["tracks"][0]["entity_id"] == "track-1"


def test_encode_decode_gateway_update():
    raw = encode_gateway_update(records=[{"kind": "entities", "id": "e1", "data": {"x": 1}}])
    parsed = decode_billboard_message(raw)
    assert parsed is not None
    assert parsed["msg_type"] == GATEWAY_UPDATE
    assert isinstance(parsed["records"], list)


def test_encode_decode_gateway_index():
    raw = encode_gateway_index(entity_ids=["e2", "e1"])
    parsed = decode_billboard_message(raw)
    assert parsed is not None
    assert parsed["msg_type"] == GATEWAY_INDEX
    assert parsed["entity_ids"] == ["e2", "e1"]


def test_decode_rejects_legacy_versioned_message_type():
    assert decode_billboard_message(b'{"msg_type":"atlas.intent.v1"}') is None


def test_encode_decode_asset_intent_diff():
    raw = encode_asset_intent_diff(
        asset_id="asset-1",
        patch={"subscriptions": {"entities": ["e1"]}},
        intent_seq=2,
        intent_hash="def456",
        base_hash="abc123",
        generated_at_ms=2,
        expected_max_silence_ms=10000,
    )
    parsed = decode_billboard_message(raw)
    assert parsed is not None
    assert parsed["msg_type"] == ASSET_INTENT_DIFF


def test_merge_diff_round_trip_with_tombstones():
    previous = {"meta": {"a": 1, "b": 2}, "subscriptions": {"entities": ["e1"]}}
    current = {"meta": {"a": 1, "c": 3}, "subscriptions": {"entities": ["e2"]}}
    patch = build_merge_diff(previous, current)
    assert patch == {
        "meta": {"b": {"__delete__": True}, "c": 3},
        "subscriptions": {"entities": ["e2"]},
    }
    assert apply_merge_diff(previous, patch) == current


def test_decode_rejects_intent_without_required_metadata():
    assert decode_billboard_message(b'{"msg_type":"atlas.intent","asset_id":"a1"}') is None


def test_decode_rejects_diff_without_base_hash():
    assert (
        decode_billboard_message(
            b'{"msg_type":"atlas.intent.diff","asset_id":"a1","patch":{},"intent_seq":1,"intent_hash":"h","generated_at_ms":1,"expected_max_silence_ms":1000}'
        )
        is None
    )
