#!/usr/bin/env python3
"""Verify tasks:self subscription stores tasks in subscribed.tasks, not passive.gateway.tasks."""
from __future__ import annotations

import argparse
import json
import logging
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
    resolve_world_state_path,
    start_combo_webui,
    task_in_subscribed_section,
    terminate_combo_process,
    wait_for_readiness,
    wait_for_task_in_world_state,
)

log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_two_radios(log_prefix="[subscribed]")
    package_root = get_package_root()

    kill_stale_port_listeners([args.gateway_port, args.asset_port], log_prefix="[subscribed]")
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
            log_prefix="[subscribed]",
        )

        task_id, _ = create_task(
            args.api_base_url,
            args.entity_id,
            task_id_prefix="subscribed",
        )
        log.info("[subscribed] Created task %s; waiting for world_state", task_id)
        wait_for_task_in_world_state(
            world_state_path,
            task_id,
            timeout_s=args.task_timeout_seconds,
        )

        data = json.loads(world_state_path.read_text(encoding="utf-8"))
        if not task_in_subscribed_section(data, task_id):
            raise RuntimeError(
                f"Task {task_id} not in subscribed.tasks (tasks:self subscription should store there)"
            )

        log.info("[subscribed] PASS: Task in subscribed.tasks")
        return 0
    except Exception as exc:
        log.error("[subscribed] ERROR: %s", exc)
        return 1
    finally:
        terminate_combo_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
