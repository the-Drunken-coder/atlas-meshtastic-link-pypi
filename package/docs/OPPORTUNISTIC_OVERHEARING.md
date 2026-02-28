# Opportunistic Overhearing

## Concept

All asset-gateway traffic flows over the shared, encrypted Command Channel. Because every asset on the channel can decrypt every message, assets can passively ingest useful data from traffic not addressed to them — at zero additional radio cost.

## Meshtastic Encryption Nuance

Overhearing depends on how the message was encrypted:

- **Shared channel PSK traffic:** All nodes with the channel key can decrypt payloads. This is the Command Channel model — overhearing works.
- **PKC direct messaging:** Only the intended recipient can decrypt. Other nodes relay but cannot read content. Overhearing does not apply to PKC DMs.

All replication and subscription traffic must use the shared Command Channel PSK path so that overhearing is always possible.

## How It Works

1. Gateway sends a state update addressed to Asset A over the Command Channel.
2. Asset B is on the same channel and receives the same radio packet.
3. Asset B decrypts the message and inspects the payload.
4. If the payload contains data relevant to Asset B's world state (e.g., a track it cares about, a neighbor's telemetry), Asset B ingests it into its local cache.
5. No additional radio bandwidth was consumed.

## Design Rules

- No extra RF bandwidth is consumed by opportunistic ingest.
- Duplicate ingest is safe because state apply is idempotent by version.
- The gateway does not need to send the same data N times to N assets — one message on the channel can serve multiple consumers.
- If an asset overhears data it hasn't explicitly subscribed to, it may still ingest it. Subscriptions control what the gateway proactively sends, not what an asset is allowed to process.
