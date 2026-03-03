from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from atlas_meshtastic_link.asset.runner import AssetRunner
from atlas_meshtastic_link.config.schema import AssetConfig
from atlas_meshtastic_link.protocol.billboard_wire import decode_billboard_message
from next_gen_tests.helpers.async_utils import wait_until as _wait_until


class _FakeRadio:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, str | int | None]] = []

    async def send(self, data: bytes, destination: str | int | None = None) -> None:
        self.sent.append((data, destination))


class _FakeIntentStore:
    def __init__(self, sequence: list[tuple[bool, dict]]) -> None:
        self._sequence = sequence
        self._last = sequence[-1][1]

    def changed_since_last_read(self) -> tuple[bool, dict]:
        if self._sequence:
            changed, payload = self._sequence.pop(0)
            self._last = payload
            return changed, payload
        return False, self._last


class _FakeWorldState:
    def __init__(self) -> None:
        self.meta: dict[str, float] = {}

    def set_meta(self, **payload: Any) -> None:
        self.meta.update(payload)


def _make_runner(tmp_path: Path, *, diff_enabled: bool, refresh_s: float, sequence: list[tuple[bool, dict]]) -> AssetRunner:
    stop_event = asyncio.Event()
    runner = AssetRunner(
        radio=_FakeRadio(),
        config=AssetConfig(
            entity_id="asset-1",
            intent_path=str(tmp_path / "intent.json"),
            world_state_path=str(tmp_path / "world.json"),
            intent_poll_interval_seconds=0.2,
            publish_min_interval_seconds=0.1,
            intent_refresh_interval_seconds=refresh_s,
            intent_diff_enabled=diff_enabled,
        ),
        stop_event=stop_event,
    )
    runner._intent = _FakeIntentStore(sequence)  # type: ignore[assignment]
    runner._world = _FakeWorldState()  # type: ignore[assignment]
    return runner


def test_runner_initial_publish_is_full_when_diff_enabled(tmp_path: Path):
    payload = {"asset_id": "asset-1", "subscriptions": {"entities": ["e-1"]}, "meta": {}}

    async def _run() -> None:
        runner = _make_runner(tmp_path, diff_enabled=True, refresh_s=10.0, sequence=[(False, payload)])
        task = asyncio.create_task(runner._intent_loop())
        await _wait_until(lambda: len(runner._radio.sent) >= 1)
        runner._stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        decoded = decode_billboard_message(runner._radio.sent[0][0])
        assert decoded is not None
        assert decoded["msg_type"] == "atlas.intent"
        assert decoded["intent_seq"] == 1
        assert decoded["intent_hash"]

    asyncio.run(_run())


def test_runner_change_sends_diff_when_enabled(tmp_path: Path):
    payload_v1 = {"asset_id": "asset-1", "subscriptions": {"entities": ["e-1"]}, "meta": {}}
    payload_v2 = {"asset_id": "asset-1", "subscriptions": {"entities": ["e-2"]}, "meta": {}}

    async def _run() -> None:
        runner = _make_runner(
            tmp_path,
            diff_enabled=True,
            refresh_s=10.0,
            sequence=[(False, payload_v1), (True, payload_v2)],
        )
        task = asyncio.create_task(runner._intent_loop())
        await _wait_until(lambda: len(runner._radio.sent) >= 2)
        runner._stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        decoded = [decode_billboard_message(raw) for raw, _ in runner._radio.sent]
        assert decoded[1] is not None
        assert decoded[1]["msg_type"] == "atlas.intent.diff"
        assert decoded[1]["base_hash"] == decoded[0]["intent_hash"]
        assert decoded[1]["intent_seq"] == decoded[0]["intent_seq"] + 1

    asyncio.run(_run())


def test_runner_heartbeat_sends_full_snapshot(tmp_path: Path):
    payload = {"asset_id": "asset-1", "subscriptions": {"entities": ["e-1"]}, "meta": {}}

    async def _run() -> None:
        runner = _make_runner(tmp_path, diff_enabled=True, refresh_s=0.2, sequence=[(False, payload)])
        task = asyncio.create_task(runner._intent_loop())
        await _wait_until(lambda: len(runner._radio.sent) >= 2)
        runner._stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        decoded = [decode_billboard_message(raw) for raw, _ in runner._radio.sent]
        assert len(decoded) >= 2
        assert decoded[0] is not None and decoded[0]["msg_type"] == "atlas.intent"
        assert decoded[1] is not None and decoded[1]["msg_type"] == "atlas.intent"

    asyncio.run(_run())


def test_runner_change_does_not_reset_full_heartbeat_timer(tmp_path: Path):
    payload_v1 = {"asset_id": "asset-1", "subscriptions": {"entities": ["e-1"]}, "meta": {}}
    payload_v2 = {"asset_id": "asset-1", "subscriptions": {"entities": ["e-2"]}, "meta": {}}
    refresh_s = 0.25
    min_interval_s = 0.1

    async def _run() -> None:
        runner = _make_runner(
            tmp_path,
            diff_enabled=True,
            refresh_s=refresh_s,
            sequence=[(False, payload_v1), (True, payload_v2)],
        )
        task = asyncio.create_task(runner._intent_loop())
        await _wait_until(lambda: len(runner._radio.sent) >= 3, timeout=refresh_s + min_interval_s + 2.0)
        runner._stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        decoded = [decode_billboard_message(raw) for raw, _ in runner._radio.sent]
        # Expect full startup snapshot + diff + eventual full heartbeat.
        assert len(decoded) >= 3
        assert decoded[1] is not None and decoded[1]["msg_type"] == "atlas.intent.diff"
        assert decoded[2] is not None and decoded[2]["msg_type"] == "atlas.intent"
        if len(decoded) > 3:
            for message in decoded[3:]:
                assert message is not None and message["msg_type"] == "atlas.intent"

    asyncio.run(_run())
