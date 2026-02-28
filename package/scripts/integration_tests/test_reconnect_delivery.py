#!/usr/bin/env python3
"""Create task while asset offline; start asset; verify task is delivered on reconnect."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_package_root = Path(__file__).resolve().parents[2]
if str(_package_root) not in sys.path:
    sys.path.insert(0, str(_package_root))

from scripts.integration_tests.combo_harness import (
    add_common_args,
    create_task,
    ensure_entity,
    get_package_root,
    kill_stale_port_listeners,
    require_two_radios,
    start_asset_only,
    start_gateway_only,
    terminate_combo_process,
    wait_for_gateway_ready,
    wait_for_task_in_world_state,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--gateway-timeout-seconds", type=float, default=120.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_two_radios(log_prefix="[reconnect]")
    package_root = get_package_root()

    kill_stale_port_listeners([args.gateway_port, args.asset_port], log_prefix="[reconnect]")
    gateway_process = start_gateway_only(package_root, args.host, args.gateway_port)

    try:
        wait_for_gateway_ready(args.host, args.gateway_port, timeout_s=args.gateway_timeout_seconds)
        ensure_entity(
            args.api_base_url,
            args.entity_id,
            timeout_s=args.entity_timeout_seconds,
            log_prefix="[reconnect]",
        )

        task_id, _ = create_task(
            args.api_base_url,
            args.entity_id,
            task_id_prefix="reconnect",
        )
        print(f"[reconnect] Created task {task_id} before asset start")

        asset_process = start_asset_only(package_root, args.host, args.asset_port)
        try:
            world_state_path = package_root / "world_state.json"
            wait_for_task_in_world_state(
                world_state_path,
                task_id,
                timeout_s=args.task_timeout_seconds,
            )
            print("[reconnect] PASS: Task delivered after asset reconnect")
            return 0
        finally:
            terminate_combo_process(asset_process)
    except Exception as exc:
        print(f"[reconnect] ERROR: {exc}")
        return 1
    finally:
        terminate_combo_process(gateway_process)


if __name__ == "__main__":
    raise SystemExit(main())
