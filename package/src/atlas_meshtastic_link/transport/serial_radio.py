"""SerialRadioAdapter - wraps meshtastic SerialInterface behind RadioInterface."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import queue
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atlas_meshtastic_link.transport.chunking import (
    FLAG_ACK,
    FLAG_NACK,
    build_ack_chunk,
    build_nack_chunk,
    chunk_message,
    parse_chunk_with_flags,
    parse_nack_payload,
)
from atlas_meshtastic_link.transport.compression import (
    PREFIX_ZLIB,
    expand_keys,
    maybe_compress,
    maybe_decompress,
    shorten_keys,
)
from atlas_meshtastic_link.transport.reassembly import MessageReassembler
from atlas_meshtastic_link.transport.spool import OutboundSpool

log = logging.getLogger(__name__)

_CTRL_BITMAP_REQ = "bitmap_req"
_CTRL_ALL_RECEIVED = "all_received"


@dataclass
class _OutboundChunkState:
    destination: str
    total: int
    chunks: dict[int, bytes]
    created: float
    acked: bool = False
    ack_event: threading.Event = field(default_factory=threading.Event)


class SerialRadioAdapter:
    """Adapter that wraps a meshtastic SerialInterface to satisfy RadioInterface."""

    def __init__(self, port: str, **kwargs) -> None:  # noqa: ANN003
        self._port = port
        self._segment_size = max(1, int(kwargs.pop("segment_size", 200)))
        self._reassembly_ttl_seconds = max(1.0, float(kwargs.pop("reassembly_ttl_seconds", 30.0)))
        method = str(kwargs.pop("reliability_method", "window") or "window").strip().lower()
        if method not in {"window", "none"}:
            log.warning("[SERIAL] Unknown reliability method '%s'; defaulting to window", method)
            method = "window"
        self._reliability_method = method
        self._window_round_trip_timeout_seconds = max(
            0.2, float(kwargs.pop("window_round_trip_timeout_seconds", 1.0))
        )
        self._window_max_round_trips = max(1, int(kwargs.pop("window_max_round_trips", 6)))
        self._window_max_nack_entries = max(1, min(255, int(kwargs.pop("window_max_nack_entries", 16))))
        self._window_nack_interval_seconds = max(
            0.1, float(kwargs.pop("window_nack_interval_seconds", 0.75))
        )
        self._outbound_cache_ttl_seconds = max(2.0, float(kwargs.pop("outbound_cache_ttl_seconds", 120.0)))
        self._completed_cache_ttl_seconds = max(2.0, float(kwargs.pop("completed_cache_ttl_seconds", 120.0)))
        self._message_dedupe_window_seconds = max(
            0.1, float(kwargs.pop("message_dedupe_window_seconds", 2.0))
        )
        self._kwargs = kwargs
        self._lock_file = None
        self._interface = None
        self._message_queue: queue.Queue[tuple[str, bytes]] = queue.Queue()
        self._spool = OutboundSpool(kwargs.pop("spool_path", None))
        self._spool_event = asyncio.Event()
        self._transmit_task: asyncio.Task | None = None
        self._subscribed = False
        self._numeric_to_user_id: dict[str, str] = {}
        self._recent_messages: dict[tuple[str, int], float] = {}
        self._message_lock = threading.Lock()
        self._reassembly_lock = threading.Lock()
        self._reassemblers: dict[str, MessageReassembler] = {}
        self._outbound_lock = threading.Lock()
        self._outbound_chunks: dict[tuple[str, bytes], _OutboundChunkState] = {}
        self._nack_state: dict[tuple[str, bytes], tuple[set[int], float]] = {}
        self._completed_messages: dict[tuple[str, bytes], float] = {}
        self._lock_path = _lock_path_for_port(port)

        connect = bool(kwargs.pop("connect", True))
        self._acquire_port_lock()
        log.info("[SERIAL] Claimed exclusive lock for %s", self._port)

        try:
            if connect:
                self._interface = self._open_interface(self._port, **kwargs)
                self._subscribe_receive_events()
                try:
                    loop = asyncio.get_running_loop()
                    self._transmit_task = loop.create_task(self._transmit_loop(), name="atlas_serial_transmit")
                except RuntimeError:
                    # No running event loop at construction time; the transmit loop
                    # will be started lazily on the first call to send().
                    pass
        except (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError):
            self._release_port_lock()
            raise

    async def send(self, data: bytes, destination: str | int | None = None) -> None:
        if self._interface is None:
            raise RuntimeError("Serial interface is not available")

        payload = data if isinstance(data, bytes) else bytes(data)
        destination_id = self._normalize_destination(destination)

        # Enqueue the payload. The background _transmit_loop will process it.
        self._spool.enqueue(destination_id, payload)
        self._spool_event.set()

        # Lazily start the transmit loop if it was not started at construction time.
        if self._transmit_task is None or self._transmit_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._transmit_task = loop.create_task(self._transmit_loop(), name="atlas_serial_transmit")
            except RuntimeError:
                pass

    async def _transmit_loop(self) -> None:
        """Background task that takes messages from the spool and sends them."""
        while self._interface is not None and getattr(self, "_subscribed", False):
            try:
                item = self._spool.peek_next()
                if item is None:
                    await self._spool_event.wait()
                    self._spool_event.clear()
                    continue

                msg_id_db, destination_id, payload, attempts = item

                shortened = shorten_keys(payload)
                wire_payload = maybe_compress(shortened)
                segments = self._segment_payload(wire_payload)

                log.debug(
                    "[SERIAL][WIRE][TX] db_id=%d destination=%s payload_len=%d wire_len=%d compressed=%s payload=%s",
                    msg_id_db,
                    destination_id,
                    len(payload),
                    len(wire_payload),
                    wire_payload[0:1] == PREFIX_ZLIB,
                    _payload_for_log(payload),
                )

                if len(segments) == 1:
                    log.info("[SERIAL] Sending %d wire bytes to %s", len(wire_payload), destination_id)
                    await asyncio.to_thread(self._send_frame, segments[0], destination=destination_id)
                    self._spool.pop(msg_id_db)
                    continue

                log.info(
                    "[SERIAL] Sending %d wire bytes to %s (%d chunks, reliability=%s)",
                    len(wire_payload),
                    destination_id,
                    len(segments),
                    self._reliability_method,
                )

                message_id = self._message_id_from_chunk(segments[0])
                await self._send_data_chunks(segments, destination=destination_id)
                self._cache_outbound_chunks(destination=destination_id, message_id=message_id, chunks=segments)

                if destination_id == "^all" or self._reliability_method != "window":
                    self._spool.pop(msg_id_db)
                    continue

                delivered = await self._wait_for_window_ack(destination=destination_id, message_id=message_id)
                if delivered:
                    self._spool.pop(msg_id_db)
                else:
                    self._spool.increment_attempt(msg_id_db)
                    if attempts + 1 >= 3:
                        log.warning("[SERIAL] Message %s to %s failed after %d attempts, dropping", message_id.hex(), destination_id, attempts + 1)
                        self._spool.pop(msg_id_db)
                    else:
                        log.warning("[SERIAL] Message %s to %s failed, backing off before retry (attempt %d)", message_id.hex(), destination_id, attempts + 1)
                        await asyncio.sleep(1.5)
            except asyncio.CancelledError:
                break
            except (ConnectionError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
                log.error("[SERIAL] Error in transmit loop: %s", exc)
                await asyncio.sleep(1.0)

    async def receive(self) -> tuple[bytes, str]:
        while True:
            try:
                # queue.get() waits up to 0.5s in a worker thread; intended for single-consumer usage.
                sender, payload = await asyncio.to_thread(self._message_queue.get, True, 0.5)
                if sender.isdigit():
                    sender = self._convert_numeric_to_user_id(sender) or sender
                log.debug(
                    "[SERIAL][WIRE][RX] sender=%s payload_len=%d payload=%s",
                    sender,
                    len(payload),
                    _payload_for_log(payload),
                )
                return payload, sender
            except queue.Empty:
                await asyncio.sleep(0)

    async def close(self) -> None:
        if self._subscribed:
            try:
                from pubsub import pub
                from pubsub.core.topicexc import TopicNameError

                pub.unsubscribe(self._on_receive, "meshtastic.receive")
            except (AttributeError, ImportError, KeyError, RuntimeError, TopicNameError):
                pass
            self._subscribed = False

        if self._transmit_task is not None:
            self._transmit_task.cancel()
            try:
                await asyncio.gather(self._transmit_task, return_exceptions=True)
            except asyncio.CancelledError:
                pass
            self._transmit_task = None
            
        self._spool.close()

        if self._interface is not None and hasattr(self._interface, "close"):
            try:
                self._interface.close()
            except (AttributeError, OSError, RuntimeError) as exc:
                log.warning("[SERIAL] Error closing serial interface on %s: %s", self._port, exc)
            self._interface = None

        self._release_port_lock()

    async def get_channel_url(self) -> str | None:
        if self._interface is None:
            return None
        local_node = getattr(self._interface, "localNode", None)
        if local_node is None or not hasattr(local_node, "getURL"):
            return None
        return str(local_node.getURL(includeAll=False))

    async def get_channel_usage_summary(self) -> str | None:
        if self._interface is None:
            return None

        local_node = getattr(self._interface, "localNode", None)
        if local_node is None:
            return None

        primary_url: str | None = None
        get_url = getattr(local_node, "getURL", None)
        if callable(get_url):
            try:
                primary_url = str(get_url(includeAll=False))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                primary_url = None

        channels_obj = getattr(local_node, "channels", None)
        if channels_obj is None:
            get_channels = getattr(local_node, "getChannels", None)
            if callable(get_channels):
                try:
                    channels_obj = get_channels()
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    channels_obj = None

        parsed = _parse_channel_entries(channels_obj)
        if not parsed:
            base = f"port={self._port} primary={primary_url}" if primary_url else f"port={self._port}"
            base += " channels=unknown"
            chutil = _get_channel_utilization(self._interface)
            if chutil is not None:
                base += f" chutil={chutil:.1f}%"
            return base

        active = [entry for entry in parsed if entry["role"].lower() != "disabled"]
        slots = ", ".join(
            f"#{entry['index']}:{entry['name']}({entry['role']})"
            for entry in active
        ) or "none"
        primary = primary_url or "none"
        summary = f"port={self._port} primary={primary} active_channels={len(active)} slots=[{slots}]"
        chutil = _get_channel_utilization(self._interface)
        if chutil is not None:
            summary += f" chutil={chutil:.1f}%"
        return summary

    async def set_channel_url(self, channel_url: str) -> None:
        if self._interface is None:
            raise RuntimeError("Serial interface is not available")
        local_node = getattr(self._interface, "localNode", None)
        if local_node is None or not hasattr(local_node, "setURL"):
            raise RuntimeError("This meshtastic interface does not support setURL()")
        local_node.setURL(channel_url, addOnly=False)

    async def get_node_id(self) -> str | None:
        if self._interface is None:
            return None
        if not hasattr(self._interface, "getMyNodeInfo"):
            return None
        info = self._interface.getMyNodeInfo() or {}
        if not isinstance(info, dict):
            return None
        user = info.get("user")
        if not isinstance(user, dict):
            return None
        node_id = user.get("id")
        if node_id is None:
            return None
        return str(node_id)

    def _open_interface(self, port: str, **kwargs):  # noqa: ANN202, ANN003
        try:
            from meshtastic import serial_interface
        except ImportError as exc:
            raise RuntimeError(
                "meshtastic-python is required for serial radio transport; install meshtastic extras"
            ) from exc

        interface = serial_interface.SerialInterface(port, **kwargs)
        wait_for_config = getattr(interface, "waitForConfig", None)
        if callable(wait_for_config):
            try:
                wait_for_config()
            except (OSError, RuntimeError, TimeoutError) as exc:
                log.warning("[SERIAL] waitForConfig() failed on %s: %s", port, exc)
        return interface

    def _subscribe_receive_events(self) -> None:
        try:
            from pubsub import pub

            pub.subscribe(self._on_receive, "meshtastic.receive")
            self._subscribed = True
            log.debug("[SERIAL] Subscribed to meshtastic.receive")
        except ImportError:
            log.warning("[SERIAL] pypubsub not available; receive callbacks disabled")

    def _on_receive(self, packet: dict[str, Any], interface: Any) -> None:
        try:
            if interface is not self._interface:
                return

            decoded = packet.get("decoded")
            if not decoded:
                return

            portnum = decoded.get("portnum")
            if portnum not in ("PRIVATE_APP", 80):
                return

            sender = packet.get("fromId")
            numeric_id = packet.get("from")
            sender_str = self._resolve_sender(sender, numeric_id)
            if not sender_str:
                return

            payload = decoded.get("payload", b"")
            if not payload:
                return
            if not isinstance(payload, bytes):
                payload = str(payload).encode("utf-8")
            log.debug(
                "[SERIAL][WIRE][RX-FRAME] sender=%s frame_len=%d frame=%s",
                sender_str,
                len(payload),
                _payload_for_log(payload),
            )

            payload = self._decode_inbound_payload(sender_str, payload)
            if payload is None:
                return

            payload = maybe_decompress(payload)
            payload = expand_keys(payload)

            message_key = (sender_str, hashlib.sha256(payload).digest())
            now = time.monotonic()
            with self._message_lock:
                expiry = self._recent_messages.get(message_key)
                if expiry is not None and expiry > now:
                    return
                self._recent_messages[message_key] = now + self._message_dedupe_window_seconds
                if len(self._recent_messages) > 1000:
                    cutoff = now
                    self._recent_messages = {
                        key: ttl for key, ttl in self._recent_messages.items() if ttl > cutoff
                    }

            self._message_queue.put((sender_str, payload))
        except (KeyError, RuntimeError, TypeError, ValueError, OSError) as exc:
            log.debug("[SERIAL] Failed to process received packet: %s", exc)

    def _segment_payload(self, payload: bytes) -> list[bytes]:
        if len(payload) <= self._segment_size:
            return [payload]

        # 8 random bytes keep the per-message chunk ID compact and collision-safe enough for local meshes.
        message_id = os.urandom(8)
        return chunk_message(message_id, payload, segment_size=self._segment_size)

    def _message_id_from_chunk(self, chunk: bytes) -> bytes:
        _flags, message_id, _sequence, _total, _payload = parse_chunk_with_flags(chunk)
        return message_id

    async def _send_data_chunks(self, chunks: list[bytes], *, destination: str) -> None:
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            flags, message_id, sequence, _total, segment = parse_chunk_with_flags(chunk)
            log.debug(
                "[SERIAL][WIRE][TX-CHUNK] destination=%s idx=%d/%d msg_id=%s seq=%d flags=%s chunk_len=%d segment_len=%d segment=%s",
                destination,
                index,
                total,
                message_id.hex(),
                sequence,
                _chunk_flags_for_log(flags),
                len(chunk),
                len(segment),
                _payload_for_log(segment),
            )
            await asyncio.to_thread(self._send_frame, chunk, destination=destination)

    def _cache_outbound_chunks(self, *, destination: str, message_id: bytes, chunks: list[bytes]) -> None:
        with self._outbound_lock:
            chunk_map: dict[int, bytes] = {}
            for chunk in chunks:
                _flags, _msg_id, sequence, _total, _segment = parse_chunk_with_flags(chunk)
                chunk_map[sequence] = chunk
            self._outbound_chunks[(destination, message_id)] = _OutboundChunkState(
                destination=destination,
                total=len(chunks),
                chunks=chunk_map,
                created=time.monotonic(),
            )
            self._prune_outbound_locked()

    async def _wait_for_window_ack(self, *, destination: str, message_id: bytes) -> bool:
        for attempt in range(1, self._window_max_round_trips + 1):
            await asyncio.to_thread(
                self._send_frame,
                build_ack_chunk(message_id, _CTRL_BITMAP_REQ),
                destination=destination,
            )
            log.debug(
                "[SERIAL] Sent %s request for %s to %s (attempt %d/%d)",
                _CTRL_BITMAP_REQ,
                message_id.hex(),
                destination,
                attempt,
                self._window_max_round_trips,
            )

            with self._outbound_lock:
                state = self._outbound_chunks.get((destination, message_id))
            if state is None:
                return True

            signaled = await asyncio.to_thread(
                state.ack_event.wait,
                self._window_round_trip_timeout_seconds,
            )
            if signaled:
                with self._outbound_lock:
                    current = self._outbound_chunks.get((destination, message_id))
                    if current is None or current.acked:
                        self._outbound_chunks.pop((destination, message_id), None)
                        return True
                return True

        with self._outbound_lock:
            self._outbound_chunks.pop((destination, message_id), None)
        return False

    def _send_frame(self, payload: bytes, *, destination: str) -> None:
        if self._interface is None:
            return
        try:
            flags, message_id, sequence, total, segment = parse_chunk_with_flags(payload)
            log.debug(
                "[SERIAL][WIRE][TX-FRAME] destination=%s msg_id=%s seq=%d/%d flags=%s segment_len=%d segment=%s",
                destination,
                message_id.hex(),
                sequence,
                total,
                _chunk_flags_for_log(flags),
                len(segment),
                _payload_for_log(segment),
            )
        except ValueError:
            log.debug(
                "[SERIAL][WIRE][TX-FRAME] destination=%s raw_len=%d raw=%s",
                destination,
                len(payload),
                _payload_for_log(payload),
            )
        self._interface.sendData(
            payload,
            destinationId=destination,
            wantAck=True,
            portNum=_private_app_portnum(),
        )

    def _decode_inbound_payload(self, sender: str, payload: bytes) -> bytes | None:
        try:
            flags, message_id, sequence, total, segment = parse_chunk_with_flags(payload)
        except ValueError:
            return payload
        log.debug(
            "[SERIAL][WIRE][RX-CHUNK] sender=%s msg_id=%s seq=%d/%d flags=%s segment_len=%d segment=%s",
            sender,
            message_id.hex(),
            sequence,
            total,
            _chunk_flags_for_log(flags),
            len(segment),
            _payload_for_log(segment),
        )

        if flags & FLAG_ACK:
            self._handle_ack_control(sender, message_id, segment)
            return None

        if flags & FLAG_NACK:
            self._handle_nack_control(sender, message_id, segment)
            return None

        with self._reassembly_lock:
            reassembler = self._reassemblers.get(sender)
            if reassembler is None:
                reassembler = MessageReassembler(ttl_seconds=self._reassembly_ttl_seconds)
                self._reassemblers[sender] = reassembler

            complete = reassembler.feed(message_id, sequence, total, segment)
            if complete is None:
                if self._reliability_method == "window":
                    missing = reassembler.missing_sequences(message_id, force=False) or []
                    if self._should_send_nack(sender, message_id, missing):
                        self._send_frame(
                            build_nack_chunk(message_id, missing[: self._window_max_nack_entries]),
                            destination=sender,
                        )
                self._prune_idle_reassemblers_locked()
                return None

            self._prune_idle_reassemblers_locked()

        with self._outbound_lock:
            self._completed_messages[(sender, message_id)] = time.monotonic() + self._completed_cache_ttl_seconds
            self._prune_outbound_locked()

        if self._reliability_method == "window" and total > 1:
            self._send_frame(
                build_ack_chunk(message_id, _CTRL_ALL_RECEIVED),
                destination=sender,
            )
        log.debug(
            "[SERIAL][WIRE][RX-REASSEMBLED] sender=%s msg_id=%s total_chunks=%d payload_len=%d payload=%s",
            sender,
            message_id.hex(),
            total,
            len(complete),
            _payload_for_log(complete),
        )
        return complete

    def _handle_ack_control(self, sender: str, message_id: bytes, payload: bytes) -> None:
        marker = payload.decode("utf-8", errors="replace").strip() if payload else ""
        log.debug(
            "[SERIAL][WIRE][RX-ACK] sender=%s msg_id=%s marker=%s payload=%s",
            sender,
            message_id.hex(),
            marker or "<empty>",
            _payload_for_log(payload),
        )
        if marker == _CTRL_BITMAP_REQ:
            self._handle_bitmap_request(sender, message_id)
            return
        if marker in {"", _CTRL_ALL_RECEIVED}:
            with self._outbound_lock:
                state = self._outbound_chunks.get((sender, message_id))
                if state is None:
                    return
                state.acked = True
                state.ack_event.set()
                self._outbound_chunks.pop((sender, message_id), None)

    def _handle_nack_control(self, sender: str, message_id: bytes, payload: bytes) -> None:
        missing = parse_nack_payload(payload)
        log.debug(
            "[SERIAL][WIRE][RX-NACK] sender=%s msg_id=%s missing=%s payload=%s",
            sender,
            message_id.hex(),
            missing,
            _payload_for_log(payload),
        )
        if not missing:
            return

        with self._outbound_lock:
            state = self._outbound_chunks.get((sender, message_id))
            if state is None:
                return
            for sequence in missing:
                chunk = state.chunks.get(sequence)
                if chunk is None:
                    continue
                log.debug(
                    "[SERIAL] Resending missing chunk %d/%d for %s to %s",
                    sequence,
                    state.total,
                    message_id.hex(),
                    sender,
                )
                self._send_frame(chunk, destination=sender)

        self._send_frame(
            build_ack_chunk(message_id, _CTRL_BITMAP_REQ),
            destination=sender,
        )

    def _handle_bitmap_request(self, sender: str, message_id: bytes) -> None:
        with self._reassembly_lock:
            reassembler = self._reassemblers.get(sender)
            missing = None if reassembler is None else reassembler.missing_sequences(message_id, force=True)

        if missing is None:
            with self._outbound_lock:
                expiry = self._completed_messages.get((sender, message_id), 0.0)
                is_completed = expiry > time.monotonic()
            if not is_completed:
                return
            self._send_frame(
                build_ack_chunk(message_id, _CTRL_ALL_RECEIVED),
                destination=sender,
            )
            log.debug(
                "[SERIAL][WIRE][TX-ACK] destination=%s msg_id=%s marker=%s",
                sender,
                message_id.hex(),
                _CTRL_ALL_RECEIVED,
            )
            return

        if not missing:
            self._send_frame(
                build_ack_chunk(message_id, _CTRL_ALL_RECEIVED),
                destination=sender,
            )
            log.debug(
                "[SERIAL][WIRE][TX-ACK] destination=%s msg_id=%s marker=%s",
                sender,
                message_id.hex(),
                _CTRL_ALL_RECEIVED,
            )
            return

        self._send_frame(
            build_nack_chunk(message_id, missing[: self._window_max_nack_entries]),
            destination=sender,
        )
        log.debug(
            "[SERIAL][WIRE][TX-NACK] destination=%s msg_id=%s missing=%s",
            sender,
            message_id.hex(),
            missing[: self._window_max_nack_entries],
        )

    def _should_send_nack(self, sender: str, message_id: bytes, missing: list[int]) -> bool:
        if not missing:
            return False
        now = time.monotonic()
        missing_set = set(missing)
        state_key = (sender, message_id)
        with self._outbound_lock:
            previous = self._nack_state.get(state_key)
            if previous is None:
                self._nack_state[state_key] = (missing_set, now)
                return True
            previous_missing, previous_timestamp = previous
            if previous_missing != missing_set or (now - previous_timestamp) >= self._window_nack_interval_seconds:
                self._nack_state[state_key] = (missing_set, now)
                return True
            return False

    def _prune_outbound_locked(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, state in self._outbound_chunks.items()
            if (now - state.created) > self._outbound_cache_ttl_seconds
        ]
        for key in expired:
            self._outbound_chunks.pop(key, None)

        self._nack_state = {
            key: value
            for key, value in self._nack_state.items()
            if (now - value[1]) <= self._outbound_cache_ttl_seconds
        }
        self._completed_messages = {
            key: expiry for key, expiry in self._completed_messages.items() if expiry > now
        }

    def _prune_idle_reassemblers_locked(self) -> None:
        stale_senders: list[str] = []
        for sender, reassembler in self._reassemblers.items():
            reassembler.expire_stale()
            if reassembler.pending_count == 0:
                stale_senders.append(sender)

        for sender in stale_senders:
            self._reassemblers.pop(sender, None)

    def _resolve_sender(self, sender: Any, numeric_id: Any) -> str | None:
        if sender:
            sender_str = str(sender)
            if numeric_id is not None:
                self._numeric_to_user_id[str(numeric_id)] = sender_str
            return sender_str

        if numeric_id is None:
            return None

        return self._convert_numeric_to_user_id(str(numeric_id))

    def _normalize_destination(self, destination: str | int | None) -> str:
        if destination is None:
            return "^all"

        dest = str(destination)
        if dest.isdigit():
            converted = self._convert_numeric_to_user_id(dest)
            return converted or dest

        if not dest.startswith("!") and dest != "^all":
            dest = "!" + dest
        return dest

    def _convert_numeric_to_user_id(self, numeric_id: str) -> str | None:
        if numeric_id in self._numeric_to_user_id:
            return self._numeric_to_user_id[numeric_id]

        try:
            numeric_id_int = int(numeric_id)
        except ValueError:
            return None

        if self._interface is not None and hasattr(self._interface, "_getOrCreateByNum"):
            try:
                node_info = self._interface._getOrCreateByNum(numeric_id_int)
                if isinstance(node_info, dict):
                    user_info = node_info.get("user")
                    if isinstance(user_info, dict) and user_info.get("id"):
                        user_id = str(user_info["id"])
                        self._numeric_to_user_id[numeric_id] = user_id
                        return user_id
                if hasattr(node_info, "user") and getattr(node_info.user, "id", None):
                    user_id = str(node_info.user.id)
                    self._numeric_to_user_id[numeric_id] = user_id
                    return user_id
            except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
                pass

        fallback = f"!{numeric_id_int:08x}"
        self._numeric_to_user_id[numeric_id] = fallback
        return fallback

    def _acquire_port_lock(self) -> None:
        lock_file = open(self._lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_file.close()
            raise RuntimeError(f"Serial port {self._port} is already in use by another process.") from exc
        except ImportError:
            lock_file.close()
            raise
        self._lock_file = lock_file

    def _release_port_lock(self) -> None:
        if self._lock_file is None:
            return

        try:
            if os.name == "nt":
                import msvcrt

                self._lock_file.seek(0)
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock_file.close()
            self._lock_file = None
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            log.info("[SERIAL] Released exclusive lock for %s", self._port)


def _lock_path_for_port(port: str) -> Path:
    safe_port = re.sub(r"[^A-Za-z0-9_.-]", "_", port)
    return Path(tempfile.gettempdir()) / f"atlas_meshtastic_link_{safe_port}.lock"


def _private_app_portnum() -> int:
    try:
        from meshtastic import portnums_pb2

        return int(portnums_pb2.PRIVATE_APP)
    except (ImportError, AttributeError, TypeError, ValueError):
        return 80


def _get_channel_utilization(interface: Any) -> float | None:
    """Read channel_utilization (chutil) from local node telemetry if available.

    Meshtastic stores this in deviceMetrics or localStats when telemetry is received.
    Returns None if not yet available (device may not have sent telemetry yet).

    Reliability caveats — treat this as a coarse health signal, not a precise
    real-time metric:

    * **Stale reads**: The value only updates when the firmware pushes new
      telemetry (roughly every 30-60 s depending on device config).  Between
      updates the same value is returned repeatedly.
    * **Abrupt resets**: The firmware uses a fixed measurement window.  When
      it rolls over, chutil can jump from a non-zero value to 0.0% instantly
      even though traffic tapered off gradually.
    * **Per-device measurement**: Two radios in the same RF environment may
      report different values because each device measures independently.
    """
    if interface is None:
        return None
    nodes_by_num = getattr(interface, "nodesByNum", None)
    if not isinstance(nodes_by_num, dict):
        return None
    my_node_num = None
    my_info = getattr(interface, "myInfo", None)
    if my_info is not None:
        my_node_num = getattr(my_info, "my_node_num", None)
    if my_node_num is None:
        local_node = getattr(interface, "localNode", None)
        if local_node is not None:
            my_node_num = getattr(local_node, "nodeNum", None)
    if my_node_num is None:
        return None
    node = nodes_by_num.get(my_node_num)
    if not isinstance(node, dict):
        return None
    for key in ("deviceMetrics", "localStats"):
        metrics = node.get(key)
        if not isinstance(metrics, dict):
            continue
        val = metrics.get("channel_utilization") or metrics.get("channelUtilization")
        if val is not None and isinstance(val, (int, float)):
            return float(val)
    return None


def _parse_channel_entries(channels_obj: Any) -> list[dict[str, Any]]:
    if channels_obj is None:
        return []

    raw_entries: list[Any]
    if isinstance(channels_obj, dict):
        raw_entries = list(channels_obj.values())
    elif isinstance(channels_obj, (list, tuple)):
        raw_entries = list(channels_obj)
    else:
        try:
            raw_entries = list(channels_obj)
        except TypeError:
            return []

    parsed: list[dict[str, Any]] = []
    for entry in raw_entries:
        index = _field(entry, "index")
        role = _field(entry, "role")
        name = _field(entry, "name")
        if name is None:
            settings = _field(entry, "settings")
            name = _field(settings, "name")

        role_str = _normalize_role(role)
        if index is None:
            index = len(parsed)
        parsed.append(
            {
                "index": int(index) if isinstance(index, int | float) else index,
                "role": role_str,
                "name": str(name) if name else "unnamed",
            }
        )
    return parsed


def _field(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    if hasattr(value, key):
        return getattr(value, key)
    return None


def _normalize_role(role: Any) -> str:
    if role is None:
        return "unknown"
    if isinstance(role, bool):
        return str(role).lower()
    if isinstance(role, int):
        if role == 0:
            return "disabled"
        return str(role)
    text = str(role).strip()
    if not text:
        return "unknown"
    upper = text.upper()
    if "DISABLED" in upper:
        return "disabled"
    if "." in text:
        text = text.split(".")[-1]
    return text.lower()


def _chunk_flags_for_log(flags: int) -> str:
    names: list[str] = []
    if flags & FLAG_ACK:
        names.append("ACK")
    if flags & FLAG_NACK:
        names.append("NACK")
    if not names:
        names.append("DATA")
    return "|".join(names)


def _payload_for_log(payload: bytes) -> str:
    if not payload:
        return "<empty>"
    try:
        text = payload.decode("utf-8")
        return f"text:{text}"
    except UnicodeDecodeError:
        return f"hex:{payload.hex()}"
