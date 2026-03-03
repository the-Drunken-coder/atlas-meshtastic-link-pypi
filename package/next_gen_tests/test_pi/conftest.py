"""PI test fixtures: two-radio skip, API cleanup, sequential execution."""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path
from typing import Any, Generator

import pytest

# Ensure package root is on path for scripts.integration_tests
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from atlas_meshtastic_link.transport.discovery import discover_usb_ports
from scripts.integration_tests.combo_harness import (
    cleanup_entity_tasks,
    delete_task,
    ensure_entity,
    get_atlas_api_url,
    get_package_root,
    kill_stale_port_listeners,
    resolve_world_state_path,
    start_combo_webui,
    terminate_combo_process,
    wait_for_readiness,
)


@dataclasses.dataclass
class ComboEnv:
    """Container for a running combo-webui environment."""

    process: subprocess.Popen
    status_snapshots: dict[str, Any]
    world_state_path: Path
    host: str = "127.0.0.1"
    gateway_port: int = 8840
    asset_port: int = 8841


def _discover_ports_safe() -> list:
    try:
        return discover_usb_ports()
    except Exception:
        return []


def pytest_addoption(parser):
    """Add PI test timeout options."""
    parser.addoption(
        "--readiness-timeout",
        type=float,
        default=180.0,
        help="Readiness wait timeout (seconds)",
    )
    parser.addoption(
        "--entity-timeout",
        type=float,
        default=60.0,
        help="Entity ensure timeout (seconds)",
    )
    parser.addoption(
        "--task-timeout",
        type=float,
        default=120.0,
        help="Task delivery wait timeout (seconds)",
    )
    parser.addoption(
        "--gateway-timeout",
        type=float,
        default=120.0,
        help="Gateway ready wait timeout (seconds)",
    )


@pytest.fixture(scope="session")
def pi_entity_id() -> str:
    """Entity ID used by PI tests."""
    return "asset-1"


@pytest.fixture(scope="session")
def pi_api_base() -> str:
    """API base URL for PI tests (localhost by default, override with ATLAS_COMMAND_API_URL)."""
    return get_atlas_api_url()


@pytest.fixture(scope="session", autouse=True)
def pi_session_cleanup(pi_api_base: str, pi_entity_id: str) -> None:
    """Before any PI test runs, delete existing tasks for entity that could interfere."""
    if len(_discover_ports_safe()) < 2:
        return
    try:
        n = cleanup_entity_tasks(pi_api_base, pi_entity_id)
        if n > 0:
            print(f"\n[pi] Session cleanup: deleted {n} existing tasks for {pi_entity_id}")
    except Exception as e:
        print(f"\n[pi] Session cleanup warning: {e}")


@pytest.fixture()
def pi_task_cleanup(pi_api_base: str):
    """Fixture that tracks created task IDs and deletes them after the test."""
    created: list[str] = []

    class Tracker:
        def register(self, task_id: str) -> None:
            created.append(task_id)

    yield Tracker()

    for task_id in created:
        try:
            if delete_task(pi_api_base, task_id):
                print(f"[pi] Cleaned up task {task_id}")
        except Exception as e:
            print(f"[pi] Cleanup warning for {task_id}: {e}")


@pytest.fixture()
def pi_package_root() -> Path:
    """Package root for PI tests."""
    return get_package_root()


@pytest.fixture()
def pi_combo(
    pi_package_root: Path,
    pi_api_base: str,
    pi_entity_id: str,
    request,
) -> Generator[ComboEnv, None, None]:
    """Start combo-webui, wait for readiness, resolve world_state, teardown."""
    gateway_port = 8840
    asset_port = 8841
    host = "127.0.0.1"

    kill_stale_port_listeners([gateway_port, asset_port], log_prefix="[pi-combo]")
    process = start_combo_webui(
        pi_package_root,
        host,
        gateway_port,
        asset_port,
        api_base_url=pi_api_base,
    )

    try:
        status_snapshots = wait_for_readiness(
            host,
            gateway_port,
            asset_port,
            timeout_s=request.config.getoption("--readiness-timeout"),
            entity_id=pi_entity_id,
            require_intent=True,
        )
        world_state_path = resolve_world_state_path(
            status_snapshots.get("asset", {}), pi_package_root
        )
        yield ComboEnv(
            process=process,
            status_snapshots=status_snapshots,
            world_state_path=world_state_path,
            host=host,
            gateway_port=gateway_port,
            asset_port=asset_port,
        )
    finally:
        terminate_combo_process(process)


@pytest.fixture()
def pi_ready_entity(
    pi_combo: ComboEnv,
    pi_api_base: str,
    pi_entity_id: str,
    request,
) -> ComboEnv:
    """pi_combo + ensure_entity. Returns the same ComboEnv."""
    ensure_entity(
        pi_api_base,
        pi_entity_id,
        timeout_s=request.config.getoption("--entity-timeout"),
        log_prefix="[pi-combo]",
    )
    return pi_combo
