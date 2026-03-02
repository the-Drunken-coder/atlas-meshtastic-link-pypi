from __future__ import annotations

import asyncio

import httpx

from atlas_meshtastic_link.gateway import http_bridge as bridge_module
from atlas_meshtastic_link.gateway.http_bridge import AtlasHttpBridge


class _NotFoundError(httpx.HTTPStatusError):
    def __init__(self) -> None:
        response = httpx.Response(status_code=404)
        super().__init__("not found", request=httpx.Request("PUT", "http://test"), response=response)


class _FakeClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:  # noqa: ARG002
        self.created: list[dict] = []
        self.checkins: list[tuple[str, dict]] = []
        self._existing_assets: set[str] = set()

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:  # noqa: ARG002
        return None

    async def create_entity(self, **kwargs):  # noqa: ANN003
        self.created.append(kwargs)
        self._existing_assets.add(str(kwargs["entity_id"]))
        return kwargs

    async def checkin_entity(self, entity_id: str, **kwargs):  # noqa: ANN003
        if entity_id not in self._existing_assets:
            raise _NotFoundError()
        self.checkins.append((entity_id, kwargs))
        return {"entity_id": entity_id}


def test_publish_asset_intent_creates_then_checkins(monkeypatch):
    async def _run() -> None:
        monkeypatch.setattr(bridge_module, "AtlasCommandHttpClient", _FakeClient)
        bridge = AtlasHttpBridge(base_url="https://atlascommandapi.org")
        await bridge.start()
        try:
            await bridge.publish_asset_intent(
                asset_id="asset-demo-01",
                intent={
                    "alias": "atlas-demo",
                    "subtype": "ground-station",
                    "components": {
                        "telemetry": {
                            "latitude": 40.0,
                            "longitude": -74.0,
                            "altitude_m": 10.0,
                            "speed_m_s": 0.0,
                            "heading_deg": 90.0,
                        },
                        "status": {"value": "ready"},
                    },
                },
            )
            client = bridge.client
            assert len(client.created) == 1
            assert client.created[0]["entity_id"] == "asset-demo-01"
            assert client.created[0]["alias"] == "atlas-demo"
            assert client.checkins
            checkin_asset, checkin_payload = client.checkins[-1]
            assert checkin_asset == "asset-demo-01"
            assert checkin_payload["status"] == "ready"
        finally:
            await bridge.stop()

    asyncio.run(_run())
