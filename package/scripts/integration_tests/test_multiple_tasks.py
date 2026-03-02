#!/usr/bin/env python3
"""Create multiple tasks and verify all appear in world_state within timeout."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

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
    wait_for_tasks_in_world_state,
)

DEFAULT_TASK_COUNT = 4


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--task-count", type=int, default=DEFAULT_TASK_COUNT)
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    require_two_radios(log_prefix="[multi-task]")
    package_root = get_package_root()

    kill_stale_port_listeners([args.gateway_port, args.asset_port], log_prefix="[multi-task]")
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
            log_prefix="[multi-task]",
        )

        task_ids: list[str] = []
        for i in range(args.task_count):
            task_id, _ = create_task(
                args.api_base_url,
                args.entity_id,
                task_id_prefix="multi",
            )
            task_ids.append(task_id)
            log.info("[multi-task] Created task %s/%s: %s", i + 1, args.task_count, task_id)

        wait_for_tasks_in_world_state(
            world_state_path,
            task_ids,
            timeout_s=args.task_timeout_seconds,
        )
        log.info("[multi-task] PASS: All %s tasks visible in world_state", len(task_ids))
        return 0
    except Exception as exc:
        log.info("[multi-task] ERROR: %s", exc)
        return 1
    finally:
        terminate_combo_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
