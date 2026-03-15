#!/usr/bin/env python3
"""Measure Atlas command round-trip latency from API task create to local world_state."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running as script: python scripts/integration_tests/test_command_latency.py
_package_root = Path(__file__).resolve().parents[2]
if str(_package_root) not in sys.path:
    sys.path.insert(0, str(_package_root))
log = logging.getLogger(__name__)

from scripts.integration_tests.combo_harness import (
    add_common_args,
    create_task,
    ensure_entity,
    get_package_root,
    kill_stale_port_listeners,
    require_two_radios,
    resolve_world_state_path,
    start_combo_webui,
    terminate_combo_process,
    wait_for_readiness,
    wait_for_task_in_world_state,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--no-require-intent",
        action="store_true",
        help="Skip waiting for asset intent (default: wait for intent)",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    require_two_radios(log_prefix="[latency]")
    package_root = get_package_root()

    kill_stale_port_listeners([args.gateway_port, args.asset_port], log_prefix="[latency]")
    process = start_combo_webui(
        package_root,
        args.host,
        args.gateway_port,
        args.asset_port,
    )

    try:
        status_snapshots = wait_for_readiness(
            args.host,
            args.gateway_port,
            args.asset_port,
            timeout_s=args.readiness_timeout_seconds,
            entity_id=args.entity_id if not args.no_require_intent else None,
            require_intent=not args.no_require_intent,
        )
        world_state_path = resolve_world_state_path(
            status_snapshots.get("asset", {}), package_root
        )

        ensure_entity(
            args.api_base_url,
            args.entity_id,
            timeout_s=args.entity_timeout_seconds,
            log_prefix="[latency]",
        )

        task_id, started_at = create_task(
            args.api_base_url,
            args.entity_id,
            task_id_prefix="latency",
        )
        log.info(
            "[latency] Created task %s; waiting for world_state update at %s",
            task_id,
            world_state_path,
        )
        arrived_at = wait_for_task_in_world_state(
            world_state_path,
            task_id,
            timeout_s=args.task_timeout_seconds,
        )

        latency_ms = (arrived_at - started_at) * 1000.0
        log.info("[latency] Task %s visible in world_state. latency_ms=%.2f", task_id, latency_ms)
        return 0
    except Exception as exc:
        log.info("[latency] ERROR: %s", exc)
        return 1
    finally:
        terminate_combo_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
