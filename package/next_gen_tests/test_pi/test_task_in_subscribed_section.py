"""PI test: tasks:self subscription stores tasks in the flat tasks dict."""
from __future__ import annotations

import json

import pytest
from scripts.integration_tests.combo_harness import (
    create_task,
    task_in_world_state_dict,
    wait_for_task_in_world_state,
)


@pytest.mark.pi
def test_task_in_world_state(
    pi_ready_entity,
    pi_entity_id,
    pi_api_base,
    pi_task_cleanup,
    request,
) -> None:
    """Task appears in the flat tasks dict when using tasks:self."""
    env = pi_ready_entity

    task_id, _ = create_task(
        pi_api_base, pi_entity_id, task_id_prefix="subscribed"
    )
    pi_task_cleanup.register(task_id)

    wait_for_task_in_world_state(
        env.world_state_path,
        task_id,
        timeout_s=request.config.getoption("--task-timeout"),
    )

    data = json.loads(env.world_state_path.read_text(encoding="utf-8"))
    assert task_in_world_state_dict(data, task_id), (
        f"Task {task_id} not in top-level tasks dict"
    )
