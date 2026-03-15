"""PI test: measure Atlas command round-trip latency from API task create to world_state."""
from __future__ import annotations

import pytest
from scripts.integration_tests.combo_harness import (
    create_task,
    wait_for_task_in_world_state,
)


@pytest.mark.pi
def test_command_latency(
    pi_ready_entity,
    pi_entity_id,
    pi_api_base,
    pi_task_cleanup,
    request,
) -> None:
    """Measure latency from task create to world_state update."""
    env = pi_ready_entity

    task_id, started_at = create_task(
        pi_api_base, pi_entity_id, task_id_prefix="latency"
    )
    pi_task_cleanup.register(task_id)

    arrived_at = wait_for_task_in_world_state(
        env.world_state_path,
        task_id,
        timeout_s=request.config.getoption("--task-timeout"),
    )

    latency_ms = (arrived_at - started_at) * 1000.0
    print(f"\n[pi-latency] Task {task_id} latency_ms={latency_ms:.2f}")
    assert 0 <= latency_ms < 30_000, f"Latency {latency_ms:.0f}ms outside expected range"
