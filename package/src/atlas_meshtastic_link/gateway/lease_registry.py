"""Per-asset subscription lease management on the gateway side."""
from __future__ import annotations

import logging

from atlas_meshtastic_link.state.subscriptions import LeaseRegistry

log = logging.getLogger(__name__)


class GatewayLeaseManager:
    """Gateway-side wrapper around :class:`state.LeaseRegistry` with push scheduling.

    Accepts inbound subscription requests from assets, registers them as
    leased subscriptions, and provides helpers for querying active state.
    """

    def __init__(
        self,
        *,
        lease_registry: LeaseRegistry | None = None,
        default_ttl_seconds: float = 300.0,
    ) -> None:
        self._registry = lease_registry or LeaseRegistry(default_ttl_seconds=default_ttl_seconds)

    @property
    def registry(self) -> LeaseRegistry:
        """Direct access to the underlying lease registry."""
        return self._registry

    async def process_subscription_request(
        self,
        asset_id: str,
        subscription_key: str,
        *,
        ttl: float | None = None,
    ) -> None:
        """Register or renew a subscription lease for *asset_id* → *subscription_key*.

        This is the primary entry-point called when the gateway receives an
        inbound subscription request from an asset over the mesh.
        """
        if not asset_id or not subscription_key:
            log.warning("[LEASE] Ignoring subscription request with empty asset_id or subscription_key")
            return
        self._registry.register(asset_id, subscription_key, ttl=ttl)
        log.debug("[LEASE] Registered subscription %s → %s", asset_id, subscription_key)

    async def process_subscription_set(
        self,
        asset_id: str,
        subscription_keys: set[str],
        *,
        ttl: float | None = None,
    ) -> None:
        """Replace all subscriptions for *asset_id* with *subscription_keys*.

        Called when a full asset intent is received and the gateway should
        replace the entire subscription set for that asset.
        """
        if not asset_id:
            log.warning("[LEASE] Ignoring subscription set with empty asset_id")
            return
        self._registry.replace_asset_subscriptions(asset_id, subscription_keys, ttl=ttl)
        log.debug("[LEASE] Replaced subscriptions for %s (%d keys)", asset_id, len(subscription_keys))

    def active_subscriptions(self, asset_id: str) -> list[str]:
        """Return subscription keys with active leases for the given asset."""
        return self._registry.subscriptions_for(asset_id)

    def subscribers_for(self, subscription_key: str) -> list[str]:
        """Return asset IDs subscribed to the given subscription key."""
        return self._registry.assets_for_subscription(subscription_key)

    def expire(self) -> list[tuple[str, str]]:
        """Expire stale leases.  Return list of (asset_id, subscription_key) removed."""
        removed = self._registry.expire()
        if removed:
            log.debug("[LEASE] Expired %d stale subscription(s)", len(removed))
        return removed
