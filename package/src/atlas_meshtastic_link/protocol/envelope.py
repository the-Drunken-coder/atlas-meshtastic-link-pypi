"""MessageEnvelope — wire-format serialization using msgpack + zstd compression."""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

try:
    import msgpack
except ImportError:  # pragma: no cover
    msgpack = None  # type: ignore[assignment]

try:
    import zstandard
except ImportError:  # pragma: no cover
    zstandard = None  # type: ignore[assignment]

PREFIX_RAW = b"\x10"
PREFIX_ZSTD = b"\x11"

_MAX_DECOMPRESSED_SIZE = 1 * 1024 * 1024  # 1 MB


def encode(payload: dict[str, Any], *, compress: bool = True) -> bytes:
    """Encode a dict payload into a compact binary envelope.

    Uses msgpack for serialization and optional zstd compression.
    A 1-byte prefix distinguishes raw from compressed frames.

    Raises ``RuntimeError`` if msgpack is not installed.
    """
    if msgpack is None:
        raise RuntimeError("msgpack is required for envelope encoding — install with: pip install atlas-meshtastic-link[envelope]")
    raw = msgpack.packb(payload, use_bin_type=True)
    if not compress or zstandard is None:
        return PREFIX_RAW + raw
    compressed = zstandard.ZstdCompressor().compress(raw)
    if len(compressed) < len(raw):
        return PREFIX_ZSTD + compressed
    return PREFIX_RAW + raw


def decode(data: bytes) -> dict[str, Any]:
    """Decode a binary envelope back into a dict payload.

    Raises ``RuntimeError`` if msgpack is not installed.
    Raises ``ValueError`` on malformed input.
    """
    if msgpack is None:
        raise RuntimeError("msgpack is required for envelope decoding — install with: pip install atlas-meshtastic-link[envelope]")
    if len(data) < 2:
        raise ValueError("Envelope too short")
    prefix = data[0:1]
    body = data[1:]
    if prefix == PREFIX_ZSTD:
        if zstandard is None:
            raise RuntimeError("zstandard is required to decompress this envelope — install with: pip install atlas-meshtastic-link[envelope]")
        raw = zstandard.ZstdDecompressor().decompress(body, max_output_size=_MAX_DECOMPRESSED_SIZE)
    elif prefix == PREFIX_RAW:
        raw = body
    else:
        raise ValueError(f"Unknown envelope prefix: {prefix.hex()}")
    result = msgpack.unpackb(raw, raw=False)
    if not isinstance(result, dict):
        raise ValueError(f"Envelope payload must be a dict, got {type(result).__name__}")
    return result


def wrap(
    payload: dict[str, Any],
    *,
    compress: bool = True,
    envelope_ts_ms: int | None = None,
) -> bytes:
    """Wrap a payload dict in an envelope with a timestamp, then encode.

    This is a convenience function that adds ``_envelope_ts_ms`` metadata
    before encoding.
    """
    envelope: dict[str, Any] = dict(payload)
    envelope["_envelope_ts_ms"] = envelope_ts_ms if envelope_ts_ms is not None else int(time.time() * 1000)
    return encode(envelope, compress=compress)


def unwrap(data: bytes) -> tuple[dict[str, Any], int]:
    """Decode an envelope and return (payload, envelope_ts_ms).

    The ``_envelope_ts_ms`` key is removed from the returned payload dict.
    Returns 0 for the timestamp if the key is missing.
    """
    envelope = decode(data)
    ts = envelope.pop("_envelope_ts_ms", 0)
    if not isinstance(ts, int):
        ts = int(ts) if ts else 0
    return envelope, ts
