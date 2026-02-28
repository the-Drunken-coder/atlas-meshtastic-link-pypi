"""Optional timestamped interaction log for gateway debugging."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)


class InteractionLog:
    """Writes timestamped gateway events to a text file.

    If *path* is ``None``, all methods silently no-op so callers never
    need to guard with ``if log:`` checks.
    """

    def __init__(self, path: str | None) -> None:
        self._path = path
        self._fh: IO[str] | None = None

    def open(self) -> None:
        if self._path is None:
            return
        try:
            self._fh = Path(self._path).open("a", encoding="utf-8")
            self.record("LOG_START", f"path={self._path}")
        except OSError:
            log.warning("[INTERACTION_LOG] Could not open %s", self._path, exc_info=True)
            self._fh = None

    def record(self, event_type: str, details: str = "") -> None:
        if self._fh is None:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        try:
            self._fh.write(f"{ts} | {event_type} | {details}\n")
            self._fh.flush()
        except OSError:
            log.debug("[INTERACTION_LOG] Write failed", exc_info=True)

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self.record("LOG_END", "")
            self._fh.close()
        except OSError:
            pass
        finally:
            self._fh = None
