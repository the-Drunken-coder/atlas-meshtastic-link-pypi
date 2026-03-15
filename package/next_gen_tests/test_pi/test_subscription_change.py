"""PI test: modify subscription at runtime; task delivered after change."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from scripts.integration_tests.combo_harness import (
    create_task,
    ensure_entity,
    kill_stale_port_listeners,
    resolve_world_state_path,
    start_combo_webui,
    terminate_combo_process,
    wait_for_readiness,
    wait_for_task_in_world_state,
)

INTENT_POLL_WAIT_S = 3.0


@pytest.mark.pi
def test_subscription_change(
    pi_package_root,
    pi_entity_id,
    pi_api_base,
    pi_task_cleanup,
    request,
) -> None:
    """Add task to subscriptions at runtime; verify gateway pushes it."""
    gateway_port = 8840
    asset_port = 8841
    host = "127.0.0.1"
    original_intent: dict | None = None
    intent_path: Path | None = None

    kill_stale_port_listeners([gateway_port, asset_port], log_prefix="[pi-subscription]")
    process = start_combo_webui(pi_package_root, host, gateway_port, asset_port)

    try:
        status_snapshots = wait_for_readiness(
            host,
            gateway_port,
            asset_port,
            timeout_s=request.config.getoption("--readiness-timeout"),
            entity_id=pi_entity_id,
            require_intent=True,
        )
        asset_status = status_snapshots.get("asset", {})
        world_state_path = resolve_world_state_path(asset_status, pi_package_root)
        raw_intent_path = asset_status.get("asset_intent_path")
        if not raw_intent_path:
            raise RuntimeError("Asset status has no asset_intent_path")
        intent_path = Path(str(raw_intent_path))
        if not intent_path.is_absolute():
            intent_path = pi_package_root / intent_path

        if not intent_path.exists():
            raise RuntimeError(f"Asset intent path not found: {intent_path}")

        original_intent = json.loads(intent_path.read_text(encoding="utf-8"))

        ensure_entity(
            pi_api_base,
            pi_entity_id,
            timeout_s=request.config.getoption("--entity-timeout"),
            log_prefix="[pi-subscription]",
        )

        task_id, _ = create_task(
            pi_api_base, pi_entity_id, task_id_prefix="sub"
        )
        pi_task_cleanup.register(task_id)

        subs = original_intent.get("subscriptions") or {}
        tasks_list = list(subs.get("tasks") or [])
        if task_id not in tasks_list:
            tasks_list.append(task_id)
        modified = {**original_intent, "subscriptions": {**subs, "tasks": tasks_list}}
        intent_path.write_text(json.dumps(modified, indent=2), encoding="utf-8")

        time.sleep(INTENT_POLL_WAIT_S)

        wait_for_task_in_world_state(
            world_state_path,
            task_id,
            timeout_s=request.config.getoption("--task-timeout"),
        )
    finally:
        if original_intent is not None and intent_path is not None and intent_path.exists():
            try:
                intent_path.write_text(
                    json.dumps(original_intent, indent=2), encoding="utf-8"
                )
            except Exception as exc:
                print(f"[pi-subscription] Failed to restore intent: {exc}")
        terminate_combo_process(process)
