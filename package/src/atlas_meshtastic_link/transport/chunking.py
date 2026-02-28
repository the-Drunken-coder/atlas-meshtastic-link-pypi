"""Binary chunking protocol - compact 16-byte header and helpers."""
from __future__ import annotations

import math
import struct

MAGIC = b"AL"
VERSION = 1
FLAGS_NONE = 0
FLAG_ACK = 0x01
FLAG_NACK = 0x02
HEADER_STRUCT = struct.Struct("!2sBB8sHH")
HEADER_SIZE = HEADER_STRUCT.size


def _normalize_message_id(message_id: bytes) -> bytes:
    if not isinstance(message_id, (bytes, bytearray)):
        raise TypeError("message_id must be bytes")
    raw = bytes(message_id)
    if not raw:
        raise ValueError("message_id must not be empty")
    return raw[:8].ljust(8, b"\x00")


def chunk_message(message_id: bytes, payload: bytes, segment_size: int) -> list[bytes]:
    """Split *payload* into segments, each prefixed with a 16-byte header."""
    if segment_size <= 0:
        raise ValueError("segment_size must be > 0")
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")
    if not payload:
        return []

    message_id_bytes = _normalize_message_id(message_id)
    total = math.ceil(len(payload) / segment_size)
    if total > 0xFFFF:
        raise ValueError("payload too large for chunk header sequence range")
    chunks: list[bytes] = []
    for index in range(total):
        seq = index + 1
        segment = payload[index * segment_size : (index + 1) * segment_size]
        header = HEADER_STRUCT.pack(MAGIC, VERSION, FLAGS_NONE, message_id_bytes, seq, total)
        chunks.append(header + segment)
    return chunks


def build_control_chunk(message_id: bytes, *, flags: int, payload: bytes = b"") -> bytes:
    """Build a single control frame for ACK/NACK-style reliability metadata."""
    message_id_bytes = _normalize_message_id(message_id)
    header = HEADER_STRUCT.pack(MAGIC, VERSION, flags, message_id_bytes, 1, 1)
    return header + payload


def build_ack_chunk(message_id: bytes, marker: str) -> bytes:
    """Build an ACK control frame with a short marker payload."""
    return build_control_chunk(message_id, flags=FLAG_ACK, payload=marker.encode("utf-8"))


def build_nack_chunk(message_id: bytes, missing_sequences: list[int]) -> bytes:
    """Build a NACK control frame listing missing sequence numbers."""
    seqs = [min(max(1, int(seq)), 0xFFFF) for seq in missing_sequences][:255]
    payload = bytes([len(seqs)]) + b"".join(struct.pack("!H", seq) for seq in seqs)
    return build_control_chunk(message_id, flags=FLAG_NACK, payload=payload)


def parse_nack_payload(payload: bytes) -> list[int]:
    """Parse NACK payload into missing sequence numbers."""
    if not payload:
        return []
    count = payload[0]
    seqs: list[int] = []
    for index in range(count):
        start = 1 + index * 2
        end = start + 2
        if end > len(payload):
            break
        seqs.append(struct.unpack("!H", payload[start:end])[0])
    return seqs


def parse_chunk_with_flags(raw: bytes) -> tuple[int, bytes, int, int, bytes]:
    """Parse a raw frame into (flags, message_id, sequence, total, segment_data)."""
    if len(raw) < HEADER_SIZE:
        raise ValueError("chunk too small to parse header")

    magic, version, flags, message_id, sequence, total = HEADER_STRUCT.unpack(raw[:HEADER_SIZE])
    if magic != MAGIC or version != VERSION:
        raise ValueError("unsupported chunk header")
    if total < 1 or sequence < 1 or sequence > total:
        raise ValueError("invalid sequence metadata in chunk header")

    return flags, message_id, sequence, total, raw[HEADER_SIZE:]


def parse_chunk(raw: bytes) -> tuple[bytes, int, int, bytes]:
    """Parse a raw chunk into (message_id, sequence, total, segment_data)."""
    _flags, message_id, sequence, total, segment = parse_chunk_with_flags(raw)
    return message_id, sequence, total, segment
