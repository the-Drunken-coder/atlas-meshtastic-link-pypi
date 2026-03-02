#!/usr/bin/env python3
"""Modify asset subscription at runtime; verify gateway pushes newly subscribed task."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
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
    terminate_combo_process,
    wait_for_readiness,
    wait_for_task_in_world_state,
)

INTENT_POLL_WAIT_S = 3.0
log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_two_radios(log_prefix="[subscription]")
    package_root = get_package_root()
    original_intent: dict | None = None
    intent_path: Path | None = None

    kill_stale_port_listeners([args.gateway_port, args.asset_port], log_prefix="[subscription]")
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
        asset_status = status_snapshots.get("asset", {})
        world_state_path = resolve_world_state_path(asset_status, package_root)
        intent_path = Path(str(asset_status.get("asset_intent_path") or ""))
        if intent_path and not intent_path.is_absolute():
            intent_path = package_root / intent_path

        if not intent_path or not intent_path.exists():
            raise RuntimeError(f"Asset intent path not found: {intent_path}")

        original_intent = json.loads(intent_path.read_text(encoding="utf-8"))

        ensure_entity(
            args.api_base_url,
            args.entity_id,
            timeout_s=args.entity_timeout_seconds,
            log_prefix="[subscription]",
        )

        task_id, _ = create_task(
            args.api_base_url,
            args.entity_id,
            task_id_prefix="sub",
        )
        log.info("[subscription] Created task %s", task_id)

        subs = original_intent.get("subscriptions") or {}
        tasks_list = list(subs.get("tasks") or [])
        if task_id not in tasks_list:
            tasks_list.append(task_id)
        modified = {**original_intent, "subscriptions": {**subs, "tasks": tasks_list}}
        intent_path.write_text(json.dumps(modified, indent=2), encoding="utf-8")
        log.info("[subscription] Added %s to subscriptions.tasks", task_id)

        time.sleep(INTENT_POLL_WAIT_S)

        wait_for_task_in_world_state(
            world_state_path,
            task_id,
            timeout_s=args.task_timeout_seconds,
        )

        log.info("[subscription] PASS: Task delivered after subscription change")
        return 0
    except Exception as exc:
        log.error("[subscription] ERROR: %s", exc)
        return 1
    finally:
        if original_intent is not None and intent_path is not None and intent_path.exists():
            try:
                intent_path.write_text(json.dumps(original_intent, indent=2), encoding="utf-8")
            except Exception:
                pass
        terminate_combo_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
