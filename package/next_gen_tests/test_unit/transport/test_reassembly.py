"""Unit tests for transport.reassembly - MessageReassembler."""
from __future__ import annotations

import time

from atlas_meshtastic_link.transport.reassembly import MessageReassembler


def test_reassembler_instantiation():
    r = MessageReassembler(ttl_seconds=10.0)
    assert r._ttl_seconds == 10.0


def test_reassembler_reassembles_out_of_order_chunks():
    r = MessageReassembler(ttl_seconds=10.0)
    msg_id = b"id-12345"

    assert r.feed(msg_id, 2, 3, b"bb") is None
    assert r.feed(msg_id, 1, 3, b"aa") is None
    assert r.feed(msg_id, 3, 3, b"cc") == b"aabbcc"


def test_reassembler_ignores_duplicates():
    r = MessageReassembler(ttl_seconds=10.0)
    msg_id = b"id-12345"

    assert r.feed(msg_id, 1, 2, b"a") is None
    assert r.feed(msg_id, 1, 2, b"a") is None
    assert r.feed(msg_id, 2, 2, b"b") == b"ab"


def test_reassembler_ignores_non_first_total_mismatch():
    r = MessageReassembler(ttl_seconds=10.0)
    msg_id = b"id-12345"

    assert r.feed(msg_id, 1, 2, b"a") is None
    assert r.feed(msg_id, 2, 3, b"b") is None
    assert r.feed(msg_id, 2, 2, b"b") == b"ab"


def test_reassembler_resets_on_new_sequence_one_with_different_total():
    r = MessageReassembler(ttl_seconds=10.0)
    msg_id = b"id-12345"

    assert r.feed(msg_id, 1, 2, b"old") is None
    assert r.feed(msg_id, 1, 1, b"new") == b"new"


def test_reassembler_expire_stale():
    r = MessageReassembler(ttl_seconds=0.01)
    msg_id = b"id-12345"

    assert r.feed(msg_id, 1, 2, b"a") is None
    time.sleep(0.02)
    expired = r.expire_stale()
    assert msg_id in expired
    assert r.pending_count == 0


def test_reassembler_missing_sequences_force_and_non_force():
    r = MessageReassembler(ttl_seconds=10.0)
    msg_id = b"id-12345"

    assert r.feed(msg_id, 3, 5, b"c") is None
    assert r.missing_sequences(msg_id, force=False) == [1, 2]
    assert r.missing_sequences(msg_id, force=True) == [1, 2, 4, 5]
