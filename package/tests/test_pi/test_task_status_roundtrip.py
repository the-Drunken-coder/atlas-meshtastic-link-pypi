"""PI test: task acknowledge roundtrip via API."""
from __future__ import annotations

import pytest

from scripts.integration_tests.combo_harness import (
    acknowledge_task,
    create_task,
    get_task,
    wait_for_task_in_world_state,
)


@pytest.mark.pi
def test_task_status_roundtrip(
    pi_ready_entity,
    pi_entity_id,
    pi_api_base,
    pi_task_cleanup,
    request,
) -> None:
    """Create task, receive on asset, acknowledge via API, verify status."""
    env = pi_ready_entity

    task_id, _ = create_task(
        pi_api_base, pi_entity_id, task_id_prefix="status"
    )
    pi_task_cleanup.register(task_id)

    wait_for_task_in_world_state(
        env.world_state_path,
        task_id,
        timeout_s=request.config.getoption("--task-timeout"),
    )

    acknowledge_task(pi_api_base, task_id)

    payload = get_task(pi_api_base, task_id)
    assert payload.get("status") == "acknowledged"
    assert payload.get("task_id") == task_id, f"task_id mismatch: {payload.get('task_id')}"
    assert payload.get("entity_id") == pi_entity_id, f"entity_id mismatch: {payload.get('entity_id')}"
