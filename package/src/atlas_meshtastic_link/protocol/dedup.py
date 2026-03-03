"""RequestDeduper — duplicate request detection with TTL-based expiry."""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class RequestDeduper:
    """In-memory duplicate request detector with automatic expiry.

    Each message ID is tracked with a monotonic timestamp. Entries older than
    *ttl_seconds* are removed either lazily when checked via
    :meth:`is_duplicate` or in bulk when :meth:`expire` is called.
    """

    def __init__(self, *, ttl_seconds: float = 60.0) -> None:
        self._ttl = max(0.1, float(ttl_seconds))
        self._seen: dict[str, float] = {}

    def is_duplicate(self, msg_id: str) -> bool:
        """Return ``True`` if *msg_id* has been seen and has not yet expired."""
        ts = self._seen.get(msg_id)
        if ts is None:
            return False
        if time.monotonic() - ts > self._ttl:
            self._seen.pop(msg_id, None)
            return False
        return True

    def mark_seen(self, msg_id: str) -> None:
        """Record *msg_id* as seen at the current time."""
        self._seen[msg_id] = time.monotonic()

    def expire(self) -> int:
        """Remove entries older than TTL.  Return the number of entries removed."""
        now = time.monotonic()
        stale = [mid for mid, ts in self._seen.items() if now - ts > self._ttl]
        for mid in stale:
            self._seen.pop(mid, None)
        return len(stale)

    def __len__(self) -> int:
        """Return the number of tracked (possibly stale) entries."""
        return len(self._seen)

    def clear(self) -> None:
        """Remove all tracked entries."""
        self._seen.clear()
