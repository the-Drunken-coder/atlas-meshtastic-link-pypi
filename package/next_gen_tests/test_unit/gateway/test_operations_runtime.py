from __future__ import annotations

import asyncio

from atlas_meshtastic_link.config.schema import GatewayConfig
from atlas_meshtastic_link.gateway.operations.runtime import (
    GatewayOperationsRuntime,
    _extract_version,
    _records_from_changes,
)
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
            "entities": [
                {
                    "entity_id": "e-1",
                    "entity_type": "track",
                    "subtype": "track",
                    "metadata": {"updated_at": "2026-02-26T00:00:00Z"},
                }
            ],
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

    async def publish_asset_intent(self, *, asset_id: str, intent: dict) -> dict:  # noqa: ANN001
        self.published.append((asset_id, intent))
        return {}


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
                    "metadata": {"updated_at": "2026-02-26T00:00:00Z"},
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


def test_forward_checkin_tasks_bypasses_truncation() -> None:
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
                    {"task_id": "task-1", "metadata": {"updated_at": "2026-02-26T00:00:00Z"}},
                    {"task_id": "task-2", "metadata": {"updated_at": "2026-02-26T00:00:01Z"}},
                    {"task_id": "task-3", "metadata": {"updated_at": "2026-02-26T00:00:02Z"}},
                ]
            },
        )

        assert len(radio.sent) == 1
        decoded = decode_billboard_message(radio.sent[0][0])
        assert decoded is not None
        records = decoded.get("records", [])
        assert len(records) == 3
        assert [r.get("id") for r in records] == ["task-1", "task-2", "task-3"]

    asyncio.run(_run())


def test_forward_checkin_tasks_bypasses_rate_limit() -> None:
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
        assert len(radio.sent) == 1
        decoded = decode_billboard_message(radio.sent[0][0])
        assert decoded is not None
        records = decoded.get("records", [])
        assert len(records) == 1
        assert records[0].get("id") == "task-1"

    asyncio.run(_run())


def test_extract_version_from_metadata_block():
    """Version should be extracted from metadata.updated_at (core API format)."""
    record = {"entity_id": "e-1", "metadata": {"updated_at": "2026-01-01T00:00:00Z"}}
    assert _extract_version(record) == "2026-01-01T00:00:00Z"


def test_extract_version_falls_back_to_top_level():
    """For backwards compatibility, fall back to top-level updated_at."""
    record = {"entity_id": "e-1", "updated_at": "2026-01-01T00:00:00Z"}
    assert _extract_version(record) == "2026-01-01T00:00:00Z"


def test_extract_version_prefers_metadata_over_top_level():
    """metadata.updated_at takes precedence over top-level updated_at."""
    record = {
        "entity_id": "e-1",
        "updated_at": "old",
        "metadata": {"updated_at": "new"},
    }
    assert _extract_version(record) == "new"


def test_extract_version_returns_none_when_absent():
    """Returns None when neither metadata nor top-level updated_at is present."""
    assert _extract_version({"entity_id": "e-1"}) is None


def test_records_from_changes_extracts_metadata_version():
    """_records_from_changes should use metadata.updated_at for version."""
    changes = {
        "entities": [
            {"entity_id": "e-1", "metadata": {"updated_at": "2026-01-01T00:00:00Z"}},
        ],
        "tasks": [
            {"task_id": "t-1", "metadata": {"updated_at": "2026-01-02T00:00:00Z"}},
        ],
        "objects": [
            {"object_id": "o-1", "metadata": {"updated_at": "2026-01-03T00:00:00Z"}},
        ],
    }
    records = _records_from_changes(changes)
    assert len(records) == 3
    assert records[0]["version"] == "2026-01-01T00:00:00Z"
    assert records[1]["version"] == "2026-01-02T00:00:00Z"
    assert records[2]["version"] == "2026-01-03T00:00:00Z"


def test_forward_checkin_tasks_extracts_metadata_version():
    """Checkin tasks with metadata block should have version extracted correctly."""
    async def _run() -> None:
        radio = _FakeRadio()
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=_FakeBridge(),
            config=GatewayConfig(publish_max_messages_per_second=5.0),
            stop_event=asyncio.Event(),
        )
        await runtime._forward_checkin_tasks(
            asset_id="asset-1",
            checkin_response={
                "tasks": [
                    {"task_id": "task-1", "metadata": {"updated_at": "2026-03-01T12:00:00Z"}},
                ]
            },
        )
        assert len(radio.sent) == 1
        decoded = decode_billboard_message(radio.sent[0][0])
        assert decoded is not None
        records = decoded.get("records", [])
        assert len(records) == 1
        assert records[0]["version"] == "2026-03-01T12:00:00Z"

    asyncio.run(_run())


def test_poll_and_publish_preserves_last_since_on_rate_limit() -> None:
    """When token bucket is exhausted, _last_since must NOT advance."""
    async def _run() -> None:
        radio = _FakeRadio()
        bridge = _FakeBridge()
        bridge._changed_since = {
            "entities": [
                {
                    "entity_id": "e-1",
                    "entity_type": "track",
                    "subtype": "track",
                    "metadata": {"updated_at": "2026-02-26T00:00:00Z"},
                }
            ],
            "tasks": [],
            "objects": [],
            "timestamp": "2026-02-26T00:00:01Z",
        }
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=bridge,
            config=GatewayConfig(publish_max_messages_per_second=1.0),
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
            "asset-1",
        )

        original_last_since = runtime._last_since
        # Exhaust the token bucket
        runtime._sent_this_window = 999
        runtime._send_window_started = __import__("time").monotonic()

        await runtime._poll_and_publish()

        # _last_since must remain unchanged — no data was actually broadcast
        assert runtime._last_since == original_last_since
        # Verify no gateway.update was sent (entity index broadcasts are independent)
        from atlas_meshtastic_link.protocol.billboard_wire import decode_billboard_message as _decode
        update_sends = [
            s for s in radio.sent
            if (_decode(s[0]) or {}).get("msg_type") == "atlas.gateway.update"
            and (_decode(s[0]) or {}).get("meta", {}).get("reason") != "subscription_init"
        ]
        assert len(update_sends) == 0

    asyncio.run(_run())


def test_extract_version_with_non_dict_metadata():
    """When metadata is not a dict, fall back to top-level updated_at."""
    record = {
        "entity_id": "e-1",
        "metadata": "not-a-dict",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    assert _extract_version(record) == "2026-01-01T00:00:00Z"


def test_extract_version_with_non_dict_metadata_no_fallback():
    """When metadata is not a dict and no top-level updated_at, return None."""
    record = {"entity_id": "e-1", "metadata": "not-a-dict"}
    assert _extract_version(record) is None


def test_forward_checkin_tasks_caps_at_hard_limit() -> None:
    """When checkin returns more than the hard cap, records are truncated."""
    async def _run() -> None:
        radio = _FakeRadio()
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=_FakeBridge(),
            config=GatewayConfig(publish_max_messages_per_second=5.0),
            stop_event=asyncio.Event(),
        )
        tasks = [{"task_id": f"task-{i}"} for i in range(60)]
        await runtime._forward_checkin_tasks(
            asset_id="asset-1",
            checkin_response={"tasks": tasks},
        )
        assert len(radio.sent) == 1
        decoded = decode_billboard_message(radio.sent[0][0])
        assert decoded is not None
        records = decoded.get("records", [])
        assert len(records) == 50  # hard cap

    asyncio.run(_run())


def test_gateway_runtime_truncation_sends_oldest_versions_first() -> None:
    async def _run() -> None:
        radio = _FakeRadio()
        bridge = _FakeBridge()
        bridge._changed_since = {
            "entities": [],
            "tasks": [
                {"task_id": "task-3", "entity_id": "asset-1", "metadata": {"updated_at": "2026-02-26T00:00:03Z"}},
                {"task_id": "task-2", "entity_id": "asset-1", "metadata": {"updated_at": "2026-02-26T00:00:02Z"}},
                {"task_id": "task-1", "entity_id": "asset-1", "metadata": {"updated_at": "2026-02-26T00:00:01Z"}},
            ],
            "objects": [],
            "timestamp": "2026-02-26T00:00:04Z",
        }
        runtime = GatewayOperationsRuntime(
            radio=radio,
            bridge=bridge,
            config=GatewayConfig(publish_max_messages_per_second=2.0),
            stop_event=asyncio.Event(),
        )
        await runtime.on_radio_message(
            encode_asset_intent(
                asset_id="asset-1",
                subscriptions={"tasks": ["self"]},
                intent_seq=1,
                intent_hash="h1",
                generated_at_ms=1,
                expected_max_silence_ms=10000,
            ),
            "asset-1",
        )
        await runtime._poll_and_publish()

        decoded = decode_billboard_message(radio.sent[-1][0])
        assert decoded is not None
        assert [r.get("id") for r in decoded.get("records", [])] == ["task-1", "task-2"]
        assert runtime._last_since == "2026-02-26T00:00:02Z"

    asyncio.run(_run())
