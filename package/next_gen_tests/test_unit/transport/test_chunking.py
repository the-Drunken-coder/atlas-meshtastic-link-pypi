"""Unit tests for transport.chunking - binary chunk protocol."""
from __future__ import annotations

import pytest
from atlas_meshtastic_link.transport.chunking import (
    FLAG_ACK,
    FLAG_NACK,
    HEADER_SIZE,
    build_ack_chunk,
    build_nack_chunk,
    chunk_message,
    parse_chunk,
    parse_chunk_with_flags,
    parse_nack_payload,
)


def test_header_size_is_16():
    assert HEADER_SIZE == 16


def test_chunk_message_empty_payload_returns_no_chunks():
    assert chunk_message(b"abcd1234", b"", segment_size=50) == []


def test_chunk_message_splits_and_parse_round_trip():
    payload = b"abcdefghijklmnopqrstuvwxyz"
    chunks = chunk_message(b"msg-1234", payload, segment_size=10)

    assert len(chunks) == 3

    reconstructed = bytearray()
    for expected_sequence, raw_chunk in enumerate(chunks, start=1):
        message_id, sequence, total, segment = parse_chunk(raw_chunk)
        assert message_id.startswith(b"msg-1234")
        assert sequence == expected_sequence
        assert total == 3
        reconstructed.extend(segment)

    assert bytes(reconstructed) == payload


def test_chunk_message_rejects_invalid_inputs():
    with pytest.raises(ValueError, match="segment_size"):
        chunk_message(b"id", b"hello", segment_size=0)

    with pytest.raises(TypeError, match="payload"):
        chunk_message(b"id", "hello", segment_size=10)  # type: ignore[arg-type]


def test_chunk_message_rejects_payloads_exceeding_header_sequence_range():
    payload = b"x" * 65536
    with pytest.raises(ValueError, match="sequence range"):
        chunk_message(b"id", payload, segment_size=1)


def test_parse_chunk_rejects_invalid_header():
    with pytest.raises(ValueError, match="too small"):
        parse_chunk(b"abc")

    valid = chunk_message(b"id", b"payload", segment_size=32)[0]
    tampered = b"ZZ" + valid[2:]
    with pytest.raises(ValueError, match="unsupported chunk header"):
        parse_chunk(tampered)


def test_control_chunks_round_trip():
    msg_id = b"12345678"
    ack = build_ack_chunk(msg_id, "all_received")
    flags, parsed_message_id, sequence, total, payload = parse_chunk_with_flags(ack)
    assert flags == FLAG_ACK
    assert parsed_message_id == msg_id
    assert sequence == 1
    assert total == 1
    assert payload == b"all_received"

    nack = build_nack_chunk(msg_id, [2, 5, 9])
    flags, parsed_message_id, sequence, total, payload = parse_chunk_with_flags(nack)
    assert flags == FLAG_NACK
    assert parsed_message_id == msg_id
    assert sequence == 1
    assert total == 1
    assert parse_nack_payload(payload) == [2, 5, 9]
