#!/usr/bin/env python3
"""Create task, wait for asset to receive, acknowledge via API, verify status in API."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_package_root = Path(__file__).resolve().parents[2]
if str(_package_root) not in sys.path:
    sys.path.insert(0, str(_package_root))

from scripts.integration_tests.combo_harness import (
    acknowledge_task,
    add_common_args,
    create_task,
    ensure_entity,
    get_package_root,
    get_task,
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_two_radios(log_prefix="[status]")
    package_root = get_package_root()

    kill_stale_port_listeners([args.gateway_port, args.asset_port], log_prefix="[status]")
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
            entity_id=args.entity_id,
            require_intent=True,
        )
        world_state_path = resolve_world_state_path(
            status_snapshots.get("asset", {}), package_root
        )

        ensure_entity(
            args.api_base_url,
            args.entity_id,
            timeout_s=args.entity_timeout_seconds,
            log_prefix="[status]",
        )

        task_id, _ = create_task(
            args.api_base_url,
            args.entity_id,
            task_id_prefix="status",
        )
        print(f"[status] Created task {task_id}; waiting for world_state")
        wait_for_task_in_world_state(
            world_state_path,
            task_id,
            timeout_s=args.task_timeout_seconds,
        )

        acknowledge_task(args.api_base_url, task_id)

        payload = get_task(args.api_base_url, task_id)
        status_val = payload.get("status")
        if status_val != "acknowledged":
            raise RuntimeError(f"Expected status=acknowledged, got {status_val}")

        print("[status] PASS: Task status acknowledged in API")
        return 0
    except Exception as exc:
        print(f"[status] ERROR: {exc}")
        return 1
    finally:
        terminate_combo_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
