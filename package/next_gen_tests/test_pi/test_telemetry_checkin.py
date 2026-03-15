"""PI test: verify telemetry propagates from asset intent to Atlas API via gateway checkin."""
from __future__ import annotations

import time

import pytest
from scripts.integration_tests.combo_harness import (
    get_entity,
)


@pytest.mark.pi
def test_telemetry_checkin(
    pi_ready_entity,
    pi_entity_id,
    pi_api_base,
) -> None:
    """Entity has components.telemetry from checkin."""
    telemetry_timeout = 30.0
    deadline = time.monotonic() + telemetry_timeout
    telemetry = None
    while time.monotonic() < deadline:
        payload = get_entity(pi_api_base, pi_entity_id)
        components = payload.get("components")
        if isinstance(components, dict):
            telemetry = components.get("telemetry")
            if isinstance(telemetry, dict):
                break
        time.sleep(2.0)
    assert isinstance(telemetry, dict), (
        f"Entity has no components.telemetry after {telemetry_timeout}s"
    )
