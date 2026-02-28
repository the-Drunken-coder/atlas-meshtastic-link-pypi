"""LeaseRegistry — TTL-based subscription tracking."""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


class LeaseRegistry:
    """Tracks active subscriptions with expiring leases.

    Implementation deferred to business-logic phase.
    """

    def __init__(self, default_ttl_seconds: float = 300.0) -> None:
        self._default_ttl = max(0.1, float(default_ttl_seconds))
        self._leases: dict[str, dict[str, float]] = {}

    def register(self, asset_id: str, entity_id: str, ttl: float | None = None) -> None:
        """Register or renew a subscription lease."""
        if not asset_id or not entity_id:
            return
        expires_at = time.monotonic() + (self._default_ttl if ttl is None else max(0.1, float(ttl)))
        self._leases.setdefault(asset_id, {})[entity_id] = expires_at

    def is_active(self, asset_id: str, entity_id: str) -> bool:
        """Check whether a subscription lease is still valid."""
        expires_at = self._leases.get(asset_id, {}).get(entity_id)
        if expires_at is None:
            return False
        return expires_at > time.monotonic()

    def expire(self) -> list[tuple[str, str]]:
        """Remove expired leases. Return list of (asset_id, entity_id) removed."""
        now = time.monotonic()
        removed: list[tuple[str, str]] = []
        empty_assets: list[str] = []
        for asset_id, entities in self._leases.items():
            stale = [entity_id for entity_id, expires_at in entities.items() if expires_at <= now]
            for entity_id in stale:
                entities.pop(entity_id, None)
                removed.append((asset_id, entity_id))
            if not entities:
                empty_assets.append(asset_id)
        for asset_id in empty_assets:
            self._leases.pop(asset_id, None)
        return removed

    def subscriptions_for(self, asset_id: str) -> list[str]:
        """Return entity IDs with active leases for the given asset."""
        self.expire()
        return sorted(self._leases.get(asset_id, {}).keys())

    def assets_for_subscription(self, entity_id: str) -> list[str]:
        self.expire()
        result: list[str] = []
        for asset_id, entries in self._leases.items():
            if entity_id in entries:
                result.append(asset_id)
        return sorted(result)

    def replace_asset_subscriptions(
        self,
        asset_id: str,
        subscriptions: set[str],
        *,
        ttl: float | None = None,
    ) -> None:
        if not asset_id:
            return
        self._leases[asset_id] = {}
        for subscription in subscriptions:
            self.register(asset_id, subscription, ttl=ttl)
