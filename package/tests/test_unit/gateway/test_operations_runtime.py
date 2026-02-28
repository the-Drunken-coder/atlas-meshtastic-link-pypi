from __future__ import annotations

import asyncio

from atlas_meshtastic_link.config.schema import GatewayConfig
from atlas_meshtastic_link.gateway.operations.runtime import GatewayOperationsRuntime
from atlas_meshtastic_link.protocol.billboard_wire import (
    decode_billboard_message,
    encode_asset_intent,
    encode_asset_intent_diff,
)


class _FakeRadio:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, str | int | None]] = []

    async def send(self, data: bytes, destination: str | int | None = None) -> None:
        self.sent.append((data, destination))


class _FakeBridge:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._changed_since: dict = {
            "entities": [{"entity_id": "e-1", "updated_at": "2026-02-26T00:00:00Z", "subtype": "track"}],
            "tasks": [],
            "objects": [],
        }

    async def get_changed_since(self, *, since, limit_per_type=None):  # noqa: ANN001, ARG002
        return self._changed_since

    async def get_full_dataset(self) -> dict:
        return {
            "entities": self._changed_since.get("entities", []),
            "tasks": self._changed_since.get("tasks", []),
            "objects": self._changed_since.get("objects", []),
        }

    async def publish_asset_intent(self, *, asset_id: str, intent: dict) -> None:  # noqa: ANN001
        self.published.append((asset_id, intent))


def test_gateway_runtime_ingests_intent_and_broadcasts():
    async def _run() -> None:
        radio = _FakeRadio()
        bridge = _FakeBridge()
        stop_event = asyncio.Event()
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=bridge,
            config=GatewayConfig(api_poll_interval_seconds=1.0, publish_max_messages_per_second=5.0),
            stop_event=stop_event,
        )

        intent = encode_asset_intent(
            asset_id="asset-1",
            subscriptions={"entities": ["e-1"]},
            intent_seq=1,
            intent_hash="h1",
            generated_at_ms=1,
            expected_max_silence_ms=10000,
        )
        await runtime.on_radio_message(intent, "asset-1")
        assert bridge.published
        assert bridge.published[-1][0] == "asset-1"
        await runtime._poll_and_publish()
        assert radio.sent
        payload, destination = radio.sent[-1]
        assert destination == "^all"
        decoded = decode_billboard_message(payload)
        assert decoded is not None
        assert decoded.get("msg_type") == "atlas.gateway.update"

    asyncio.run(_run())


def test_gateway_runtime_applies_diff_without_versioning():
    async def _run() -> None:
        runtime = GatewayOperationsRuntime(
            radio=_FakeRadio(),
            bridge=_FakeBridge(),
            config=GatewayConfig(),
            stop_event=asyncio.Event(),
        )
        await runtime.on_radio_message(
            encode_asset_intent(
                asset_id="asset-1",
                subscriptions={"entities": ["e-1"]},
                intent_seq=1,
                intent_hash="h1",
                generated_at_ms=1,
                expected_max_silence_ms=10000,
            ),
            "node-1",
        )
        await runtime.on_radio_message(
            encode_asset_intent_diff(
                asset_id="asset-1",
                patch={"subscriptions": {"entities": ["e-2"]}},
                intent_seq=2,
                intent_hash="h2",
                base_hash="h1",
                generated_at_ms=2,
                expected_max_silence_ms=10000,
            ),
            "node-1",
        )
        assert runtime._bridge.published[-1][1]["subscriptions"]["entities"] == ["e-2"]

    asyncio.run(_run())


def test_gateway_runtime_ignores_diff_without_base():
    async def _run() -> None:
        runtime = GatewayOperationsRuntime(
            radio=_FakeRadio(),
            bridge=_FakeBridge(),
            config=GatewayConfig(),
            stop_event=asyncio.Event(),
        )
        await runtime.on_radio_message(
            encode_asset_intent_diff(
                asset_id="asset-1",
                patch={"subscriptions": {"entities": ["e-2"]}},
                intent_seq=1,
                intent_hash="h2",
                base_hash="h1",
                generated_at_ms=2,
                expected_max_silence_ms=10000,
            ),
            "node-1",
        )
        assert runtime._bridge.published == []

    asyncio.run(_run())


def test_gateway_runtime_applies_diff_and_subsequent_full():
    async def _run() -> None:
        runtime = GatewayOperationsRuntime(
            radio=_FakeRadio(),
            bridge=_FakeBridge(),
            config=GatewayConfig(),
            stop_event=asyncio.Event(),
        )
        await runtime.on_radio_message(
            encode_asset_intent(
                asset_id="asset-1",
                subscriptions={"entities": ["e-1"]},
                intent_seq=1,
                intent_hash="h1",
                generated_at_ms=1,
                expected_max_silence_ms=10000,
            ),
            "node-1",
        )
        published_after_full = len(runtime._bridge.published)
        await runtime.on_radio_message(
            encode_asset_intent_diff(
                asset_id="asset-1",
                patch={"subscriptions": {"entities": ["e-2"]}},
                intent_seq=2,
                intent_hash="h2",
                base_hash="h1",
                generated_at_ms=2,
                expected_max_silence_ms=10000,
            ),
            "node-1",
        )
        assert len(runtime._bridge.published) == published_after_full + 1
        assert runtime._bridge.published[-1][1]["subscriptions"]["entities"] == ["e-2"]
        await runtime.on_radio_message(
            encode_asset_intent(
                asset_id="asset-1",
                subscriptions={"entities": ["e-3"]},
                intent_seq=3,
                intent_hash="h3",
                generated_at_ms=3,
                expected_max_silence_ms=10000,
            ),
            "node-1",
        )
        assert runtime._bridge.published[-1][1]["subscriptions"]["entities"] == ["e-3"]


def test_update_entity_index_tracks_adds_and_removes():
    async def _run() -> None:
        radio = _FakeRadio()
        bridge = _FakeBridge()
        stop_event = asyncio.Event()
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=bridge,
            config=GatewayConfig(
                index_broadcast_interval_seconds=30.0,
                index_diff_min_interval_seconds=5.0,
            ),
            stop_event=stop_event,
        )

        await runtime._update_entity_index({"entities": [{"entity_id": "e-1"}, {"entity_id": "e-2"}]})
        assert runtime._known_entity_ids == {"e-1", "e-2"}
        assert radio.sent
        first_payload, first_destination = radio.sent[-1]
        assert first_destination == "^all"
        decoded_first = decode_billboard_message(first_payload)
        assert decoded_first is not None
        assert decoded_first.get("msg_type") == "atlas.gateway.index"
        assert decoded_first.get("entity_ids") == ["e-1", "e-2"]

        runtime._last_index_diff_broadcast = 0.0
        await runtime._update_entity_index({"deleted_entities": [{"entity_id": "e-1"}]})
        assert runtime._known_entity_ids == {"e-2"}
        decoded_second = decode_billboard_message(radio.sent[-1][0])
        assert decoded_second is not None
        assert decoded_second.get("entity_ids") == ["e-2"]

    asyncio.run(_run())


def test_gateway_runtime_rejects_diff_on_base_hash_mismatch():
    async def _run() -> None:
        runtime = GatewayOperationsRuntime(
            radio=_FakeRadio(),
            bridge=_FakeBridge(),
            config=GatewayConfig(sync_stale_after_seconds=10.0),
            stop_event=asyncio.Event(),
        )
        await runtime.on_radio_message(
            encode_asset_intent(
                asset_id="asset-1",
                subscriptions={"entities": ["e-1"]},
                intent_seq=1,
                intent_hash="h1",
                generated_at_ms=1,
                expected_max_silence_ms=10000,
            ),
            "node-1",
        )
        published_after_full = len(runtime._bridge.published)
        await runtime.on_radio_message(
            encode_asset_intent_diff(
                asset_id="asset-1",
                patch={"subscriptions": {"entities": ["e-2"]}},
                intent_seq=2,
                intent_hash="h2",
                base_hash="wrong-base",
                generated_at_ms=2,
                expected_max_silence_ms=10000,
            ),
            "node-1",
        )
        assert len(runtime._bridge.published) == published_after_full
        health = runtime._sync_health_payload()["asset-1"]
        assert health["state"] == "DEGRADED"
        assert "base_hash_mismatch" in health["last_reason"]

    asyncio.run(_run())


def test_gateway_runtime_broadcasts_task_for_tasks_self_subscription():
    """When asset has tasks:self, tasks with matching entity_id are broadcast."""
    async def _run() -> None:
        radio = _FakeRadio()
        bridge = _FakeBridge()
        bridge._changed_since = {
            "entities": [],
            "tasks": [
                {
                    "task_id": "task-1",
                    "entity_id": "asset-1",
                    "status": "pending",
                    "updated_at": "2026-02-26T00:00:00Z",
                }
            ],
            "objects": [],
            "timestamp": "2026-02-26T00:00:01Z",
        }
        stop_event = asyncio.Event()
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=bridge,
            config=GatewayConfig(api_poll_interval_seconds=1.0, publish_max_messages_per_second=5.0),
            stop_event=stop_event,
        )

        intent = encode_asset_intent(
            asset_id="asset-1",
            subscriptions={"tasks": ["self"]},
            intent_seq=1,
            intent_hash="h1",
            generated_at_ms=1,
            expected_max_silence_ms=10000,
        )
        await runtime.on_radio_message(intent, "asset-1")
        await runtime._poll_and_publish()

        assert radio.sent
        payload, destination = radio.sent[-1]
        assert destination == "^all"
        decoded = decode_billboard_message(payload)
        assert decoded is not None
        assert decoded.get("msg_type") == "atlas.gateway.update"
        records = decoded.get("records", [])
        task_records = [r for r in records if r.get("kind") == "tasks"]
        assert len(task_records) == 1
        assert task_records[0].get("id") == "task-1"
        assert task_records[0].get("data", {}).get("entity_id") == "asset-1"

    asyncio.run(_run())


def test_gateway_runtime_marks_sync_stale_after_threshold():
    async def _run() -> None:
        runtime = GatewayOperationsRuntime(
            radio=_FakeRadio(),
            bridge=_FakeBridge(),
            config=GatewayConfig(sync_stale_after_seconds=0.01, sync_health_summary_interval_seconds=5.0),
            stop_event=asyncio.Event(),
        )
        await runtime.on_radio_message(
            encode_asset_intent(
                asset_id="asset-1",
                subscriptions={"entities": ["e-1"]},
                intent_seq=1,
                intent_hash="h1",
                generated_at_ms=1,
                expected_max_silence_ms=10,
            ),
            "node-1",
        )
        await asyncio.sleep(0.02)
        runtime._check_sync_staleness()
        health = runtime._sync_health_payload()["asset-1"]
        assert health["state"] == "DEGRADED"
        assert "stale_apply_timeout" in health["last_reason"]

    asyncio.run(_run())


def test_forward_checkin_tasks_applies_truncation() -> None:
    async def _run() -> None:
        radio = _FakeRadio()
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=_FakeBridge(),
            config=GatewayConfig(publish_max_messages_per_second=2.0),
            stop_event=asyncio.Event(),
        )
        await runtime._forward_checkin_tasks(
            asset_id="asset-1",
            checkin_response={
                "tasks": [
                    {"task_id": "task-1", "updated_at": "2026-02-26T00:00:00Z"},
                    {"task_id": "task-2", "updated_at": "2026-02-26T00:00:01Z"},
                    {"task_id": "task-3", "updated_at": "2026-02-26T00:00:02Z"},
                ]
            },
        )

        assert len(radio.sent) == 1
        decoded = decode_billboard_message(radio.sent[0][0])
        assert decoded is not None
        records = decoded.get("records", [])
        assert len(records) == 2
        assert [r.get("id") for r in records] == ["task-1", "task-2"]

    asyncio.run(_run())


def test_forward_checkin_tasks_skips_when_rate_limited() -> None:
    async def _run() -> None:
        radio = _FakeRadio()
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=_FakeBridge(),
            config=GatewayConfig(publish_max_messages_per_second=1.0),
            stop_event=asyncio.Event(),
        )
        runtime._sent_this_window = 1
        await runtime._forward_checkin_tasks(
            asset_id="asset-1",
            checkin_response={"tasks": [{"task_id": "task-1"}]},
        )
        assert radio.sent == []

    asyncio.run(_run())
