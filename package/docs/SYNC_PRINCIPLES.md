# Sync Principles

The `atlas_meshtastic_link` package operates as a state synchronization system rather than a purely RPC model. It maintains a local world state, handles data requests autonomously, and leverages the broadcast nature of the mesh to passively build a picture of the environment without spending additional radio bandwidth.

## Opt-In by Default (Default Deny)

- The system transmits and requests nothing unless explicitly configured or instructed.
- Configuration drives the baseline behavior of the system (e.g., "broadcast my telemetry every 30 seconds").

## All Traffic on the Command Channel

- All communication between assets and the gateway happens over the shared, encrypted Command Channel.
- Every asset on the channel can see every message exchanged between any other asset and the gateway.
- This is by design: the shared channel enables opportunistic overhearing (see [OPPORTUNISTIC_OVERHEARING.md](OPPORTUNISTIC_OVERHEARING.md)), where assets passively ingest useful data from traffic they overhear without spending additional radio bandwidth.
- Filtering happens at the application layer, not the transport layer. The gateway addresses messages to specific nodes, but all nodes on the channel can decrypt and read them.
- **One exception:** Gateway discovery and provisioning happen before the asset has Command Channel credentials. Search broadcasts use the public channel, and challenge/credential exchange uses private DMs. Once provisioned, the asset joins the Command Channel and all subsequent traffic uses it. See [GATEWAY_DISCOVERY.md](GATEWAY_DISCOVERY.md).

## Subscription-Filtered Processing

- The gateway tracks what each asset has subscribed to via lease updates.
- The gateway sends diffs only for entities that at least one asset has subscribed to, avoiding unnecessary traffic.
- Assets decide locally what overheard data to ingest into their world state based on relevance.

## Idempotent State Apply

- State apply is idempotent by entity/tombstone version.
- Duplicate or reordered messages do not corrupt local state.

## Intent Snapshot + Diff Sync

- Assets always emit full `atlas.intent` snapshots periodically (heartbeat).
- When enabled, intermediate intent changes are sent as `atlas.intent.diff` patches.
- Gateway applies a diff when a base full snapshot for that asset is available; otherwise it
  ignores the diff and waits for the next full heartbeat snapshot.
- Diff tombstones use `{"__delete__": true}` to remove fields.

## TTL / Expiration

- No data in the local cache lives forever.
- All overheard data, neighbor telemetry, and tracks have a TTL.
- Stale data ages out automatically if not refreshed.
