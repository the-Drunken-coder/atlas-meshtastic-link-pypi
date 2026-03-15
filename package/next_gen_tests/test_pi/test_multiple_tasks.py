"""PI test: create multiple tasks and verify all appear in world_state."""
from __future__ import annotations

import pytest
from scripts.integration_tests.combo_harness import (
    create_task,
    wait_for_tasks_in_world_state,
)


@pytest.mark.pi
def test_multiple_tasks(
    pi_ready_entity,
    pi_entity_id,
    pi_api_base,
    pi_task_cleanup,
    request,
) -> None:
    """All created tasks appear in world_state within timeout."""
    env = pi_ready_entity
    task_count = 4

    task_ids: list[str] = []
    for i in range(task_count):
        task_id, _ = create_task(
            pi_api_base, pi_entity_id, task_id_prefix="multi"
        )
        task_ids.append(task_id)
        pi_task_cleanup.register(task_id)

    wait_for_tasks_in_world_state(
        env.world_state_path,
        task_ids,
        timeout_s=request.config.getoption("--task-timeout"),
    )
