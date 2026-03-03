"""Unit tests for protocol.dedup — RequestDeduper."""
from __future__ import annotations

import time
from unittest.mock import patch

from atlas_meshtastic_link.protocol.dedup import RequestDeduper


def test_mark_seen_and_is_duplicate():
    d = RequestDeduper(ttl_seconds=10.0)
    assert not d.is_duplicate("msg-1")
    d.mark_seen("msg-1")
    assert d.is_duplicate("msg-1")


def test_unseen_is_not_duplicate():
    d = RequestDeduper()
    assert not d.is_duplicate("never-seen")


def test_expire_removes_stale_entries():
    d = RequestDeduper(ttl_seconds=0.1)
    d.mark_seen("msg-1")
    d.mark_seen("msg-2")
    assert len(d) == 2
    # Simulate time passing beyond TTL.
    with patch("atlas_meshtastic_link.protocol.dedup.time") as mock_time:
        mock_time.monotonic.return_value = time.monotonic() + 1.0
        removed = d.expire()
    assert removed == 2
    assert len(d) == 0


def test_expire_keeps_fresh_entries():
    d = RequestDeduper(ttl_seconds=60.0)
    d.mark_seen("msg-1")
    removed = d.expire()
    assert removed == 0
    assert len(d) == 1


def test_is_duplicate_auto_expires_stale():
    d = RequestDeduper(ttl_seconds=0.1)
    d.mark_seen("msg-1")
    with patch("atlas_meshtastic_link.protocol.dedup.time") as mock_time:
        mock_time.monotonic.return_value = time.monotonic() + 1.0
        assert not d.is_duplicate("msg-1")
    assert len(d) == 0


def test_clear():
    d = RequestDeduper()
    d.mark_seen("a")
    d.mark_seen("b")
    assert len(d) == 2
    d.clear()
    assert len(d) == 0
    assert not d.is_duplicate("a")


def test_len_tracks_entries():
    d = RequestDeduper()
    assert len(d) == 0
    d.mark_seen("x")
    assert len(d) == 1
    d.mark_seen("y")
    assert len(d) == 2
