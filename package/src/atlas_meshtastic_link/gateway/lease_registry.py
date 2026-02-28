"""Per-asset subscription lease management on the gateway side."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class GatewayLeaseManager:
    """Gateway-side wrapper around state.LeaseRegistry with push scheduling.

    Implementation deferred to business-logic phase.
    """

    async def process_subscription_request(self, asset_id: str, entity_id: str) -> None:
        raise NotImplementedError("GatewayLeaseManager.process_subscription_request is planned for v0.2")
