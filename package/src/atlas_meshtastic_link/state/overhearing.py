"""OverhearingFilter — passive ingest routing for mesh traffic."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class OverhearingFilter:
    """Decides which overheard messages are worth ingesting locally.

    Implementation deferred to business-logic phase.
    """

    def __init__(self) -> None:
        self._subscribed_keys: set[str] = set()

    def set_subscriptions(self, keys: set[str]) -> None:
        self._subscribed_keys = set(keys)

    def should_ingest(self, msg_type: str, entity_id: str) -> bool:
        """Return True if this overheard message is relevant to local state."""
        if not msg_type or not entity_id:
            return False
        # Only passive-ingest records not already covered by active subscriptions.
        return entity_id not in self._subscribed_keys
