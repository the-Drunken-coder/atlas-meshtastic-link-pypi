# atlas-meshtastic-link — TODO

## v0.2 Stubs (NotImplementedError today)

- [ ] **MessageEnvelope encode/decode** (`protocol/envelope.py`)
      Wire-format serialization using msgpack + zstd compression.

- [ ] **PersistentSpool** (`protocol/spool.py`)
      Disk-backed durable outbound message queue (enqueue, peek, dequeue, __len__).

- [ ] **RequestDeduper** (`protocol/dedup.py`)
      Duplicate request detection and expiry (is_duplicate, mark_seen, expire).

- [ ] **GatewayLeaseManager.process_subscription_request** (`gateway/lease_registry.py`)
      Handle inbound subscription lease requests from assets.

## CI / Publish Pipeline

- [ ] Add `PYPI_API_TOKEN` and `OPENROUTER_API_KEY` secrets to `atlas-meshtastic-link-pypi` repo — **done**
- [ ] First publish to PyPI (trigger monorepo workflow after merge to main)
- [ ] Scope PyPI token down to project after first successful publish

## Testing

- [ ] Run full PI test suite on hardware (2 radios) to validate conftest fixture refactor
- [ ] Confirm unit tests pass in CI after merge (`connection-packages-pytests.yml`)

## Housekeeping

- [ ] Merge `atlas-meshtastic-link-v2-rewrite` branch to main
- [ ] Decide whether to add `mypy src/` to the monorepo publish workflow (asset-client has it, bridge does not)
