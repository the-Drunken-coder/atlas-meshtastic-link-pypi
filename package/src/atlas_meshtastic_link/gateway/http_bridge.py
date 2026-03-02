"""AtlasHttpBridge — async HTTP client lifecycle for Atlas Command API.

Uses AtlasCommandHttpClient from the atlas-asset-client package.
Install locally via:
    pip install -e ../atlas_asset_http_client_python
Or fall back to PyPI:
    pip install atlas-asset-client
"""
from __future__ import annotations

import logging
from typing import Any

from atlas_asset_client import AtlasCommandHttpClient
from httpx import HTTPStatusError

log = logging.getLogger(__name__)


class AtlasHttpBridge:
    """Manages the AtlasCommandHttpClient lifecycle for gateway-mode API access.

    Wraps start/stop around the async context manager and exposes the
    underlying client for gateway operations to call directly.
    """

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self._base_url = base_url
        self._token = token
        self._client: AtlasCommandHttpClient | None = None

    @property
    def client(self) -> AtlasCommandHttpClient:
        """Return the active HTTP client.  Raises if not started."""
        if self._client is None:
            raise RuntimeError("AtlasHttpBridge has not been started — call start() first")
        return self._client

    async def start(self) -> None:
        """Create and open the HTTP client session."""
        log.info("[HTTP_BRIDGE] Connecting to %s", self._base_url)
        self._client = AtlasCommandHttpClient(
            base_url=self._base_url,
            token=self._token,
        )
        self._client = await self._client.__aenter__()
        log.info("[HTTP_BRIDGE] Connected")

    async def stop(self) -> None:
        """Close the HTTP client session."""
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None
            log.info("[HTTP_BRIDGE] Disconnected")

    async def get_full_dataset(self, **kwargs: Any) -> dict:
        """Convenience: fetch the full dataset from Atlas Command."""
        return await self.client.get_full_dataset(**kwargs)

    async def get_changed_since(self, since: str, **kwargs: Any) -> dict:
        """Convenience: fetch changes since a timestamp."""
        return await self.client.get_changed_since(since, **kwargs)

    async def publish_asset_intent(self, *, asset_id: str, intent: dict[str, Any]) -> dict[str, Any]:
        """Ensure the asset exists in Atlas Command and send check-in telemetry.

        Returns the checkin response dict (may contain pending tasks for the asset).
        """
        components = intent.get("components")
        if not isinstance(components, dict):
            components = {}

        telemetry = components.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
        status_component = components.get("status")
        status_value = None
        if isinstance(status_component, dict):
            status_value = status_component.get("value")

        alias = self._string_or_none(intent.get("alias")) or asset_id
        subtype = self._string_or_none(intent.get("subtype")) or "asset"
        entity_type = self._string_or_none(intent.get("entity_type")) or "asset"
        status = self._string_or_none(status_value) or self._string_or_none(telemetry.get("status"))

        checkin_kwargs: dict[str, Any] = {
            "status": status,
            "latitude": self._float_or_none(telemetry.get("latitude")),
            "longitude": self._float_or_none(telemetry.get("longitude")),
            "altitude_m": self._float_or_none(telemetry.get("altitude_m")),
            "speed_m_s": self._float_or_none(telemetry.get("speed_m_s")),
            "heading_deg": self._float_or_none(telemetry.get("heading_deg")),
        }

        log.debug("[HTTP_BRIDGE] publish_asset_intent asset_id=%s checkin=%r", asset_id, checkin_kwargs)

        try:
            response = await self.client.checkin_entity(asset_id, **checkin_kwargs)
            return response or {}
        except HTTPStatusError as exc:
            if not self._is_not_found_error(exc):
                raise

        create_payload = {
            "entity_id": asset_id,
            "entity_type": entity_type,
            "alias": alias,
            "subtype": subtype,
            "components": components,
        }
        log.debug("[HTTP_BRIDGE] Creating new entity: %r", create_payload)
        await self.client.create_entity(
            entity_id=asset_id,
            entity_type=entity_type,
            alias=alias,
            subtype=subtype,
            components=components or None,
        )
        response = await self.client.checkin_entity(asset_id, **checkin_kwargs)
        log.info("[HTTP_BRIDGE] Published new asset presence to Atlas Command: %s", asset_id)
        return response or {}

    def _is_not_found_error(self, exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        return status_code == 404

    def _float_or_none(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value))
        except (TypeError, ValueError):
            return None

    def _string_or_none(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text
