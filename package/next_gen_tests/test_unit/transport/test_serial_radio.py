"""Unit tests for transport.serial_radio."""
from __future__ import annotations

import asyncio
import json
import queue
import uuid

import pytest
from atlas_meshtastic_link.transport.chunking import (
    FLAG_ACK,
    FLAG_NACK,
    build_ack_chunk,
    build_nack_chunk,
    parse_chunk,
    parse_chunk_with_flags,
)
from atlas_meshtastic_link.transport.compression import maybe_compress, shorten_keys
from atlas_meshtastic_link.transport.serial_radio import SerialRadioAdapter
from next_gen_tests.helpers.async_utils import wait_until as _wait_until


class _FakeInterface:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, dict[str, object]]] = []

    def sendData(self, payload: bytes, **kwargs) -> None:  # noqa: N802, ANN003
        self.sent.append((payload, kwargs))


class _FakeChannelSettings:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeChannel:
    def __init__(self, index: int, role: object, name: str) -> None:
        self.index = index
        self.role = role
        self.settings = _FakeChannelSettings(name)


class _FakeLocalNode:
    def __init__(self, channels: list[object], url: str = "https://meshtastic.org/e/#test") -> None:
        self.channels = channels
        self._url = url

    def getURL(self, includeAll: bool = False) -> str:  # noqa: N802, FBT001, FBT002
        return self._url


class _FakeMyInfo:
    def __init__(self, my_node_num: int) -> None:
        self.my_node_num = my_node_num


class _FakeInterfaceWithChannels:
    def __init__(
        self,
        channels: list[object],
        url: str = "https://meshtastic.org/e/#test",
        *,
        channel_utilization: float | None = None,
    ) -> None:
        self.localNode = _FakeLocalNode(channels=channels, url=url)  # noqa: N815
        self.myInfo = _FakeMyInfo(my_node_num=42)
        self.nodesByNum: dict[int, dict] = {}
        if channel_utilization is not None:
            self.nodesByNum[42] = {
                "deviceMetrics": {"channel_utilization": channel_utilization},
            }


def _unique_port_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def test_serial_adapter_rejects_duplicate_port_claim():
    port = _unique_port_name("COM")
    first = SerialRadioAdapter(port=port, connect=False)
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            SerialRadioAdapter(port=port, connect=False)
    finally:
        asyncio.run(first.close())


def test_serial_adapter_releases_lock_on_close():
    port = _unique_port_name("COM")
    first = SerialRadioAdapter(port=port, connect=False)
    asyncio.run(first.close())

    second = SerialRadioAdapter(port=port, connect=False)
    asyncio.run(second.close())


def test_serial_send_window_reliability_completes_after_all_received():
    async def _run() -> None:
        port = _unique_port_name("COM")
        adapter = SerialRadioAdapter(
            port=port,
            connect=False,
            segment_size=4,
            reliability_method="window",
            window_round_trip_timeout_seconds=0.2,
            window_max_round_trips=3,
        )
        fake_interface = _FakeInterface()
        adapter._interface = fake_interface  # noqa: SLF001
        adapter._subscribed = True
        adapter._transmit_task = asyncio.create_task(adapter._transmit_loop())

        try:
            await adapter.send(b"abcdefghij", destination="!1234abcd")
            await _wait_until(lambda: len(fake_interface.sent) >= 4)

            assert len(fake_interface.sent) >= 4
            first_payload, kwargs = fake_interface.sent[0]
            flags, message_id, seq, total, segment = parse_chunk_with_flags(first_payload)
            assert flags == 0
            assert seq == 1
            assert total == 3
            # Compression prefix \x00 (raw) is prepended, shifting segment content
            assert segment == b"\x00abc"
            assert kwargs["destinationId"] == "!1234abcd"

            # Sender should request a bitmap after chunk burst.
            assert any(
                parse_chunk_with_flags(frame[0])[0] & FLAG_ACK
                and parse_chunk_with_flags(frame[0])[4] == b"bitmap_req"
                for frame in fake_interface.sent
            )

            packet = {
                "decoded": {"portnum": "PRIVATE_APP", "payload": build_ack_chunk(message_id, "all_received")},
                "fromId": "!1234abcd",
                "from": 123,
            }
            adapter._on_receive(packet, fake_interface)  # noqa: SLF001
            await _wait_until(lambda: adapter._spool.peek_next() is None)  # noqa: SLF001
        finally:
            await adapter.close()

    asyncio.run(_run())


def test_serial_send_window_reliability_resends_missing_chunks():
    async def _run() -> None:
        port = _unique_port_name("COM")
        adapter = SerialRadioAdapter(
            port=port,
            connect=False,
            segment_size=4,
            reliability_method="window",
            window_round_trip_timeout_seconds=0.3,
            window_max_round_trips=3,
        )
        fake_interface = _FakeInterface()
        adapter._interface = fake_interface  # noqa: SLF001
        adapter._subscribed = True
        adapter._transmit_task = asyncio.create_task(adapter._transmit_loop())

        try:
            await adapter.send(b"abcdefghij", destination="!1234abcd")
            await _wait_until(lambda: len(fake_interface.sent) >= 2)

            first_payload = fake_interface.sent[0][0]
            _flags, message_id, _seq, _total, _segment = parse_chunk_with_flags(first_payload)
            chunk_two = fake_interface.sent[1][0]

            nack_packet = {
                "decoded": {"portnum": "PRIVATE_APP", "payload": build_nack_chunk(message_id, [2])},
                "fromId": "!1234abcd",
                "from": 123,
            }
            adapter._on_receive(nack_packet, fake_interface)  # noqa: SLF001
            await _wait_until(lambda: sum(1 for p, _ in fake_interface.sent if p == chunk_two) >= 2)

            # Chunk 2 should appear at least twice (original + resend).
            occurrences = sum(1 for payload, _kwargs in fake_interface.sent if payload == chunk_two)
            assert occurrences >= 2

            ack_packet = {
                "decoded": {"portnum": "PRIVATE_APP", "payload": build_ack_chunk(message_id, "all_received")},
                "fromId": "!1234abcd",
                "from": 123,
            }
            adapter._on_receive(ack_packet, fake_interface)  # noqa: SLF001
            await _wait_until(lambda: adapter._spool.peek_next() is None)  # noqa: SLF001
        finally:
            await adapter.close()

    asyncio.run(_run())


def test_serial_send_does_not_chunk_small_payload():
    async def _run() -> None:
        port = _unique_port_name("COM")
        adapter = SerialRadioAdapter(port=port, connect=False, segment_size=64)
        fake_interface = _FakeInterface()
        adapter._interface = fake_interface  # noqa: SLF001
        adapter._subscribed = True
        adapter._transmit_task = asyncio.create_task(adapter._transmit_loop())

        try:
            await adapter.send(b"hello", destination="^all")
            await _wait_until(lambda: len(fake_interface.sent) >= 1)
        finally:
            await adapter.close()

        assert len(fake_interface.sent) == 1
        # Payload now carries a 1-byte compression prefix (0x00 = raw for small payloads)
        assert fake_interface.sent[0][0] == b"\x00hello"

    asyncio.run(_run())


def test_serial_receive_reassembles_chunked_payload_and_sends_completion_ack():
    port = _unique_port_name("COM")
    adapter = SerialRadioAdapter(port=port, connect=False, segment_size=4, reliability_method="window")
    fake_interface = _FakeInterface()
    adapter._interface = fake_interface  # noqa: SLF001

    try:
        chunks = adapter._segment_payload(maybe_compress(b"chunked-payload"))  # noqa: SLF001
        assert len(chunks) > 1

        message_id, _seq, _total, _segment = parse_chunk(chunks[0])

        for chunk_index in (1, 0):
            packet = {
                "decoded": {"portnum": "PRIVATE_APP", "payload": chunks[chunk_index]},
                "fromId": "!9ea134b0",
                "from": 123,
            }
            adapter._on_receive(packet, fake_interface)  # noqa: SLF001

        with pytest.raises(queue.Empty):
            adapter._message_queue.get_nowait()  # noqa: SLF001

        for chunk_index in range(2, len(chunks)):
            packet = {
                "decoded": {"portnum": "PRIVATE_APP", "payload": chunks[chunk_index]},
                "fromId": "!9ea134b0",
                "from": 123,
            }
            adapter._on_receive(packet, fake_interface)  # noqa: SLF001

        sender, payload = adapter._message_queue.get_nowait()  # noqa: SLF001
        assert sender == "!9ea134b0"
        assert payload == b"chunked-payload"

        assert any(
            parse_chunk_with_flags(frame[0])[0] & FLAG_ACK
            and parse_chunk_with_flags(frame[0])[1] == message_id
            and parse_chunk_with_flags(frame[0])[4] == b"all_received"
            for frame in fake_interface.sent
        )

        assert any(parse_chunk_with_flags(frame[0])[0] & FLAG_NACK for frame in fake_interface.sent)
    finally:
        asyncio.run(adapter.close())


def test_serial_channel_usage_summary_reports_active_channels():
    port = _unique_port_name("COM")
    adapter = SerialRadioAdapter(port=port, connect=False)
    adapter._interface = _FakeInterfaceWithChannels(  # noqa: SLF001
        channels=[
            _FakeChannel(index=0, role="PRIMARY", name="ATLAS_CMD"),
            _FakeChannel(index=1, role=0, name="DISABLED"),
            _FakeChannel(index=2, role="SECONDARY", name="OPS"),
        ],
        url="https://meshtastic.org/e/#CgcTEST",
    )

    try:
        summary = asyncio.run(adapter.get_channel_usage_summary())
    finally:
        asyncio.run(adapter.close())

    assert summary is not None
    assert "port=" in summary
    assert "primary=https://meshtastic.org/e/#CgcTEST" in summary
    assert "active_channels=2" in summary
    assert "#0:ATLAS_CMD(primary)" in summary
    assert "#2:OPS(secondary)" in summary
    assert "DISABLED" not in summary


def test_serial_channel_usage_summary_includes_chutil_when_available():
    port = _unique_port_name("COM")
    adapter = SerialRadioAdapter(port=port, connect=False)
    adapter._interface = _FakeInterfaceWithChannels(  # noqa: SLF001
        channels=[
            _FakeChannel(index=0, role="PRIMARY", name="ATLAS_CMD"),
        ],
        url="https://meshtastic.org/e/#CgcTEST",
        channel_utilization=2.5,
    )

    try:
        summary = asyncio.run(adapter.get_channel_usage_summary())
    finally:
        asyncio.run(adapter.close())

    assert summary is not None
    assert "chutil=2.5%" in summary


def test_send_receive_with_compression():
    """Full round-trip: send() aliases+compresses, _on_receive() decompresses+expands, payload matches."""
    async def _run() -> None:
        port = _unique_port_name("COM")
        adapter = SerialRadioAdapter(port=port, connect=False, segment_size=200)
        fake_interface = _FakeInterface()
        adapter._interface = fake_interface  # noqa: SLF001
        adapter._subscribed = True
        adapter._transmit_task = asyncio.create_task(adapter._transmit_loop())

        original = json.dumps({"type": "asset_intent", "data": "x" * 300}).encode("utf-8")

        try:
            await adapter.send(original, destination="^all")
            await _wait_until(lambda: len(fake_interface.sent) >= 1)

            # send() should have shortened keys then compressed (payload is large enough)
            shortened = shorten_keys(original)
            wire = maybe_compress(shortened)
            assert len(wire) < len(original)

            # Simulate receiving the sent frame(s) — feed them back through _on_receive
            for frame_payload, _kwargs in fake_interface.sent:
                # Skip ACK/NACK control frames
                try:
                    flags, _mid, _seq, _total, _seg = parse_chunk_with_flags(frame_payload)
                    if flags & (FLAG_ACK | FLAG_NACK):
                        continue
                except ValueError:
                    pass

                packet = {
                    "decoded": {"portnum": "PRIVATE_APP", "payload": frame_payload},
                    "fromId": "!peer1234",
                    "from": 456,
                }
                adapter._on_receive(packet, fake_interface)  # noqa: SLF001

            sender, received = adapter._message_queue.get_nowait()  # noqa: SLF001
            assert sender == "!peer1234"
            # Aliasing round-trip uses compact JSON separators, so compare parsed dicts
            assert json.loads(received) == json.loads(original)
        finally:
            await adapter.close()

    asyncio.run(_run())
