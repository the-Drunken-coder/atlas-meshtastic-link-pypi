"""PI test: readiness completes without waiting for asset intent."""
from __future__ import annotations

import time

import pytest
from scripts.integration_tests.combo_harness import (
    kill_stale_port_listeners,
    start_combo_webui,
    terminate_combo_process,
    wait_for_readiness,
)


@pytest.mark.pi
def test_readiness_without_intent(pi_package_root, request) -> None:
    """Readiness completes using connected_assets or sync_health, not intent."""
    gateway_port = 8840
    asset_port = 8841
    host = "127.0.0.1"

    kill_stale_port_listeners([gateway_port, asset_port], log_prefix="[pi-readiness]")
    process = start_combo_webui(pi_package_root, host, gateway_port, asset_port)

    try:
        start = time.perf_counter()
        wait_for_readiness(
            host,
            gateway_port,
            asset_port,
            timeout_s=request.config.getoption("--readiness-timeout"),
            entity_id=None,
            require_intent=False,
        )
        elapsed = time.perf_counter() - start
        timeout_s = request.config.getoption("--readiness-timeout")
        print(f"\n[pi-readiness] Completed in {elapsed:.1f}s")
        assert elapsed < timeout_s, (
            f"Readiness took {elapsed:.1f}s, exceeding timeout {timeout_s}s"
        )
    finally:
        terminate_combo_process(process)
