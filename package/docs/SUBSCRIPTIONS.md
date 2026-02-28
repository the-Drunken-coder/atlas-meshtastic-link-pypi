# Subscriptions

## Dynamic, Ephemeral Subscriptions

Beyond static config, user code can dynamically ask the link package to fetch specific data (e.g., "Subscribe to Track ID 123" or "Fetch Geofeature 456").

- **Ephemeral:** Dynamic subscriptions live only in memory. If the asset reboots, subscriptions are cleared and user code is responsible for re-requesting the data it needs.
- The Gateway tracks these subscriptions and pushes updates down the mesh when the requested data changes.

## Subscription Leases

- Assets publish subscription lease updates to the gateway declaring what entities they want.
- The gateway maintains a per-node lease registry with TTL-based expiration.
- Only entities matching a node's active subscriptions are replicated to that node.

## Subscription TTL

- Subscriptions expire if not refreshed within the configured TTL window.
- If an asset reboots and does not re-subscribe, the gateway stops pushing updates after the TTL expires.
