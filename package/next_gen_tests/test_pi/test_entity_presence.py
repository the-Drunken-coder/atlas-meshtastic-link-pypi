"""PI test: verify entity exists in Atlas API after asset publishes intent."""
from __future__ import annotations

import pytest
from scripts.integration_tests.combo_harness import (
    ensure_entity_exists,
)


@pytest.mark.pi
def test_entity_presence(
    pi_combo,
    pi_entity_id,
    pi_api_base,
    request,
) -> None:
    """Entity exists in Atlas API after asset intent."""
    entity = ensure_entity_exists(
        pi_api_base,
        pi_entity_id,
        timeout_s=request.config.getoption("--entity-timeout"),
        log_prefix="[pi-entity]",
    )
    assert entity.get("entity_id"), f"Entity missing entity_id: {entity}"
