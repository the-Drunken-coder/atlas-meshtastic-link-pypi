"""MessageReassembler - buckets, TTL expiry, gap detection."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    total: int
    created: float
    received: dict[int, bytes] = field(default_factory=dict)


class MessageReassembler:
    """Collects chunked segments and yields complete messages."""

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._buckets: dict[bytes, _Bucket] = {}

    @property
    def pending_count(self) -> int:
        return len(self._buckets)

    def feed(self, message_id: bytes, sequence: int, total: int, data: bytes) -> bytes | None:
        """Feed a segment. Return the reassembled payload when complete, else None."""
        now = time.monotonic()
        self.expire_stale(now=now)

        if total < 1 or sequence < 1 or sequence > total:
            return None

        bucket = self._buckets.get(message_id)
        if bucket is None:
            bucket = _Bucket(total=total, created=now)
            self._buckets[message_id] = bucket
        else:
            if now - bucket.created > self._ttl_seconds:
                bucket = _Bucket(total=total, created=now)
                self._buckets[message_id] = bucket
            elif total != bucket.total:
                if sequence == 1:
                    bucket = _Bucket(total=total, created=now)
                    self._buckets[message_id] = bucket
                else:
                    return None

        if sequence in bucket.received:
            return None

        bucket.received[sequence] = data
        if len(bucket.received) < bucket.total:
            return None

        expected = range(1, bucket.total + 1)
        if not all(idx in bucket.received for idx in expected):
            return None

        assembled = b"".join(bucket.received[idx] for idx in expected)
        self._buckets.pop(message_id, None)
        return assembled

    def expire_stale(self, *, now: float | None = None) -> list[bytes]:
        """Remove and return message IDs of buckets that have exceeded their TTL."""
        timestamp = time.monotonic() if now is None else now
        expired = [
            message_id
            for message_id, bucket in self._buckets.items()
            if timestamp - bucket.created > self._ttl_seconds
        ]
        for message_id in expired:
            self._buckets.pop(message_id, None)
        return expired

    def missing_sequences(self, message_id: bytes, *, force: bool = False) -> list[int] | None:
        """Return missing chunk sequences for a message ID, or None if unknown."""
        bucket = self._buckets.get(message_id)
        if bucket is None:
            return None

        expected_indices = set(range(1, bucket.total + 1))
        received_indices = set(bucket.received.keys())
        highest = max(received_indices) if received_indices else 0
        missing = [
            seq
            for seq in sorted(expected_indices - received_indices)
            if force or seq < highest
        ]
        return missing
