"""SQLite-backed spool for durable outbound message queuing."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


class OutboundSpool:
    """A durable FIFO queue for outbound mesh messages, backed by SQLite.
    
    This class is thread-safe.
    """

    def __init__(self, path: str | Path | None) -> None:
        self._path = Path(path) if path else None
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._open()

    def _open(self) -> None:
        db_path = str(self._path) if self._path is not None else ":memory:"
        
        # Connect with isolation_level=None for autocommit
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA auto_vacuum=FULL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                destination TEXT NOT NULL,
                payload BLOB NOT NULL,
                created_at REAL NOT NULL,
                attempts INTEGER DEFAULT 0
            )
            """
        )

    def enqueue(self, destination: str, payload: bytes) -> None:
        """Add a message to the end of the spool."""
        if self._conn is None:
            return

        now = time.time()
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO messages (destination, payload, created_at) VALUES (?, ?, ?)",
                    (destination, payload, now),
                )
            except sqlite3.Error as exc:
                log.error("[SPOOL] Failed to enqueue message to %s: %s", destination, exc)

    def enqueue_batch(self, messages: list[tuple[str, bytes]]) -> None:
        """Add multiple messages to the spool in a single explicit transaction."""
        if not messages or self._conn is None:
            return

        now = time.time()
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.executemany(
                    "INSERT INTO messages (destination, payload, created_at) VALUES (?, ?, ?)",
                    [(dest, payload, now) for dest, payload in messages],
                )
                self._conn.execute("COMMIT")
            except sqlite3.Error as exc:
                self._conn.execute("ROLLBACK")
                log.error("[SPOOL] Failed/rolled back batch enqueue: %s", exc)

    def peek_next(self) -> tuple[int, str, bytes, int] | None:
        """Return the next message from the spool (id, destination, payload, attempts), or None.
        
        Messages are ordered by creation time then insertion order (FIFO).
        """
        if self._conn is None:
            return None

        with self._lock:
            try:
                cursor = self._conn.execute(
                    "SELECT id, destination, payload, attempts FROM messages "
                    "ORDER BY created_at ASC, id ASC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    return (row[0], row[1], bytes(row[2]), row[3])
                return None
            except sqlite3.Error as exc:
                log.error("[SPOOL] Failed to peek next message: %s", exc)
                return None

    def pop(self, message_id: int) -> None:
        """Remove a message from the spool once it has been sent or discarded."""
        if self._conn is None:
            return

        with self._lock:
            try:
                self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            except sqlite3.Error as exc:
                log.error("[SPOOL] Failed to pop message %d: %s", message_id, exc)

    def increment_attempt(self, message_id: int) -> None:
        """Increment the attempt counter for a message."""
        if self._conn is None:
            return

        with self._lock:
            try:
                self._conn.execute("UPDATE messages SET attempts = attempts + 1 WHERE id = ?", (message_id,))
            except sqlite3.Error as exc:
                log.error("[SPOOL] Failed to increment attempt for message %d: %s", message_id, exc)

    def clear(self) -> None:
        """Remove all messages from the spool."""
        if self._conn is None:
            return
            
        with self._lock:
            try:
                self._conn.execute("DELETE FROM messages")
            except sqlite3.Error as exc:
                log.error("[SPOOL] Failed to clear spool: %s", exc)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            with self._lock:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                finally:
                    self._conn = None
