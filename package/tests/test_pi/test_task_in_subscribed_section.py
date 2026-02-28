"""PI test: tasks:self subscription stores tasks in subscribed.tasks."""
from __future__ import annotations

import json

import pytest

from scripts.integration_tests.combo_harness import (
    create_task,
    task_in_subscribed_section,
    wait_for_task_in_world_state,
)


@pytest.mark.pi
def test_task_in_subscribed_section(
    pi_ready_entity,
    pi_entity_id,
    pi_api_base,
    pi_task_cleanup,
    request,
) -> None:
    """Task is in subscribed.tasks when using tasks:self."""
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
    assert task_in_subscribed_section(data, task_id), (
        f"Task {task_id} not in subscribed.tasks (tasks:self should store there)"
    )

    passive_tasks = (data.get("passive") or {}).get("gateway", {}).get("tasks", {})
    in_passive = (
        task_id in passive_tasks
        or f"tasks:{task_id}" in passive_tasks
        or any(
            isinstance(rec, dict)
            and (rec.get("id") == task_id or rec.get("task_id") == task_id)
            for rec in passive_tasks.values()
        )
    )
    assert not in_passive, (
        f"Task {task_id} should NOT be in passive.gateway.tasks when using tasks:self"
    )
