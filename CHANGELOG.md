# Changelog

## [1.0.5] - 2026-03-02

- Replaced broad `except Exception` clauses with specific exception types (`ConnectionError`, `OSError`, `RuntimeError`, `ValueError`, etc.) in link, router, asset runner, and radio modules to prevent masking of unexpected failures
- Narrowed HTTP exception handling in gateway bridge and operations runtime to distinguish `httpx.HTTPStatusError` and `httpx.HTTPError` for finer-grained retry and error reporting
- Added structured logging infrastructure to `combo_webui.py` and all integration test scripts, replacing ad-hoc output with configurable loggers
- Hardened serial radio adapter against missing event loops at construction time by lazily initializing the transmit loop on first send instead of at instantiation
- Expanded unit test coverage for gateway operations runtime and HTTP bridge, adding tests for HTTP error scenarios and runtime edge cases

## [1.0.4] - 2026-03-01

- Added `OutboundSpool.enqueue_batch()` method for atomic batch insertion of multiple messages, using explicit SQLite transactions with rollback on error
- Enabled `PRAGMA auto_vacuum=FULL` on the spool database to automatically reclaim disk space from deleted messages
- Moved `time` import to module level in `transport/spool.py` to reduce import overhead during high-frequency enqueue operations

## [1.0.3] - 2026-03-01

- Synced source from ATLAS monorepo (commit `3a75619`)
- Replaced placeholder comment in `state/world_state.py` with descriptive docstring for `_normalize_world_state`, clarifying the function's intended role in structural normalization of world-state payloads
- Updated package version to 1.0.3

## [1.0.2] - 2026-03-01
- Updated package source (synced from upstream monorepo)
- Modified package/src/atlas_meshtastic_link.egg-info/PKG-INFO
- Modified package/src/atlas_meshtastic_link/state/world_state.py

## [1.0.1] - 2026-03-01
- Synced changes from the ATLAS monorepo.
- Version bump to 1.0.1.
- Internal updates and maintenance.

## [1.0.0] - 2026-02-28
- Synced changes from the ATLAS monorepo.
- Version bump to 1.0.0.
- Internal updates and maintenance.

