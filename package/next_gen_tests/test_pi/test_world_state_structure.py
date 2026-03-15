"""PI test: world_state.json has expected structure."""
from __future__ import annotations

import json

import pytest
from scripts.integration_tests.combo_harness import (
    create_task,
    validate_world_state_structure,
    wait_for_task_in_world_state,
)


@pytest.mark.pi
def test_world_state_structure(
    pi_ready_entity,
    pi_entity_id,
    pi_api_base,
    pi_task_cleanup,
    request,
) -> None:
    """World state has meta, subscribed, passive, index."""
    env = pi_ready_entity

    task_id, _ = create_task(
        pi_api_base, pi_entity_id, task_id_prefix="structure"
    )
    pi_task_cleanup.register(task_id)

    wait_for_task_in_world_state(
        env.world_state_path,
        task_id,
        timeout_s=request.config.getoption("--task-timeout"),
    )

    data = json.loads(env.world_state_path.read_text(encoding="utf-8"))
    missing = validate_world_state_structure(data)
    assert not missing, f"World state missing keys: {missing}"
