"""PI test: task delivered when asset starts after task creation."""
from __future__ import annotations

import pytest

from scripts.integration_tests.combo_harness import (
    create_task,
    ensure_entity,
    kill_stale_port_listeners,
    resolve_world_state_path,
    start_asset_only,
    start_gateway_only,
    terminate_combo_process,
    wait_for_gateway_ready,
    wait_for_task_in_world_state,
)


@pytest.mark.pi
def test_reconnect_delivery(
    pi_package_root,
    pi_entity_id,
    pi_api_base,
    pi_task_cleanup,
    request,
) -> None:
    """Create task while asset offline; start asset; task delivered on reconnect."""
    gateway_port = 8840
    asset_port = 8841
    host = "127.0.0.1"

    kill_stale_port_listeners([gateway_port, asset_port], log_prefix="[pi-reconnect]")
    gateway_process = start_gateway_only(pi_package_root, host, gateway_port)

    try:
        wait_for_gateway_ready(
            host,
            gateway_port,
            timeout_s=request.config.getoption("--gateway-timeout"),
        )
        ensure_entity(
            pi_api_base,
            pi_entity_id,
            timeout_s=request.config.getoption("--entity-timeout"),
            log_prefix="[pi-reconnect]",
        )

        task_id, _ = create_task(
            pi_api_base, pi_entity_id, task_id_prefix="reconnect"
        )
        pi_task_cleanup.register(task_id)

        asset_process = start_asset_only(pi_package_root, host, asset_port)
        try:
            world_state_path = resolve_world_state_path({}, pi_package_root)
            wait_for_task_in_world_state(
                world_state_path,
                task_id,
                timeout_s=request.config.getoption("--task-timeout"),
            )
        finally:
            terminate_combo_process(asset_process)
    finally:
        terminate_combo_process(gateway_process)
