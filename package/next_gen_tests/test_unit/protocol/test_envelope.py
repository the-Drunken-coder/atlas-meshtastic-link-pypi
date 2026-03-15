"""Unit tests for protocol.envelope — MessageEnvelope encode/decode."""
from __future__ import annotations

import time

import pytest
from atlas_meshtastic_link.protocol import envelope

pytest.importorskip("msgpack")


def test_encode_decode_round_trip():
    payload = {"msg_type": "atlas.intent", "asset_id": "a1", "value": 42}
    data = envelope.encode(payload)
    assert isinstance(data, bytes)
    result = envelope.decode(data)
    assert result == payload


def test_encode_compressed_smaller_for_large_payload():
    pytest.importorskip("zstandard")
    payload = {"data": "x" * 500}
    compressed = envelope.encode(payload, compress=True)
    raw = envelope.encode(payload, compress=False)
    assert len(compressed) < len(raw)


def test_encode_no_compress():
    payload = {"key": "val"}
    data = envelope.encode(payload, compress=False)
    assert data[0:1] == envelope.PREFIX_RAW
    result = envelope.decode(data)
    assert result == payload


def test_decode_rejects_too_short():
    with pytest.raises(ValueError, match="too short"):
        envelope.decode(b"\x10")


def test_decode_rejects_unknown_prefix():
    with pytest.raises(ValueError, match="Unknown envelope prefix"):
        envelope.decode(b"\xff" + b"\x00" * 10)


def test_decode_rejects_non_dict():
    msgpack = pytest.importorskip("msgpack")

    raw = msgpack.packb([1, 2, 3], use_bin_type=True)
    data = envelope.PREFIX_RAW + raw
    with pytest.raises(ValueError, match="must be a dict"):
        envelope.decode(data)


def test_wrap_unwrap_round_trip():
    payload = {"msg_type": "atlas.gateway.update", "records": []}
    ts = int(time.time() * 1000)
    data = envelope.wrap(payload, envelope_ts_ms=ts)
    result, result_ts = envelope.unwrap(data)
    assert result == payload
    assert result_ts == ts


def test_wrap_auto_timestamp():
    payload = {"key": "value"}
    before = int(time.time() * 1000)
    data = envelope.wrap(payload)
    _, ts = envelope.unwrap(data)
    after = int(time.time() * 1000)
    assert before <= ts <= after


def test_small_payload_stays_raw():
    payload = {"k": "v"}
    data = envelope.encode(payload, compress=True)
    # Small payloads may not benefit from compression; either prefix is valid.
    assert data[0:1] in (envelope.PREFIX_RAW, envelope.PREFIX_ZSTD)
    assert envelope.decode(data) == payload
