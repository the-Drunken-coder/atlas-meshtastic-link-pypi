"""Unit tests for scripts.integration_tests.combo_harness."""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

from scripts.integration_tests.combo_harness import (
    add_common_args,
    cleanup_entity_tasks,
    delete_task,
    extract_pid_port_pairs,
    list_entity_tasks,
    resolve_world_state_path,
    task_in_subscribed_section,
    validate_world_state_structure,
    wait_for_readiness,
    world_state_contains_task,
)


def test_extract_pid_port_pairs_filters_to_target_ports() -> None:
    netstat_output = """
  TCP    0.0.0.0:8840           0.0.0.0:0              LISTENING       1234
  TCP    127.0.0.1:8841         0.0.0.0:0              LISTENING       2234
  UDP    0.0.0.0:9999           *:*                                    9999
"""
    pairs = extract_pid_port_pairs(netstat_output, {8840, 8841})
    assert pairs == {(1234, 8840), (2234, 8841)}


def test_world_state_contains_task_across_sections() -> None:
    subscribed_payload = {"subscribed": {"tasks": {"task-1": {"id": "task-1"}}}}
    assert world_state_contains_task(subscribed_payload, "task-1") is True

    passive_payload = {
        "passive": {
            "gateway": {
                "tasks": {
                    "tasks:task-2": {
                        "id": "task-2",
                    }
                }
            }
        }
    }
    assert world_state_contains_task(passive_payload, "task-2") is True

    assert world_state_contains_task({}, "task-x") is False


def test_task_in_subscribed_section() -> None:
    subscribed_data = {"subscribed": {"tasks": {"task-1": {"id": "task-1"}}}}
    assert task_in_subscribed_section(subscribed_data, "task-1") is True

    passive_only = {"passive": {"gateway": {"tasks": {"task-2": {"id": "task-2"}}}}}
    assert task_in_subscribed_section(passive_only, "task-2") is False

    assert task_in_subscribed_section({}, "task-x") is False


def test_validate_world_state_structure() -> None:
    valid = {
        "meta": {},
        "subscribed": {"tasks": {}},
        "passive": {"gateway": {}},
        "index": {},
    }
    assert validate_world_state_structure(valid) == []

    missing_meta = {"subscribed": {"tasks": {}}, "passive": {"gateway": {}}, "index": {}}
    assert "meta" in validate_world_state_structure(missing_meta)

    missing_subscribed_tasks = {"meta": {}, "subscribed": {}, "passive": {"gateway": {}}, "index": {}}
    assert "subscribed.tasks" in validate_world_state_structure(missing_subscribed_tasks)


def test_add_common_args() -> None:
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    args = parser.parse_args(
        ["--gateway-port", "9000", "--asset-port", "9001", "--entity-id", "asset-99"]
    )
    assert args.host == "127.0.0.1"
    assert args.gateway_port == 9000
    assert args.asset_port == 9001
    assert args.entity_id == "asset-99"
    assert args.readiness_timeout_seconds == 180.0
    assert args.entity_timeout_seconds == 60.0
    assert args.task_timeout_seconds == 120.0


def test_resolve_world_state_path() -> None:
    pkg = Path("/fake/package")
    # default when missing
    got = resolve_world_state_path({}, pkg)
    assert got == pkg / "world_state.json"
    # explicit relative path from status
    got = resolve_world_state_path({"world_state_path": "./data/ws.json"}, pkg)
    assert got == pkg / "data/ws.json"
    # absolute path preserved
    abs_path = "/abs/path/ws.json"
    got = resolve_world_state_path({"world_state_path": abs_path}, pkg)
    assert got == Path(abs_path)


def test_delete_task_returns_true_on_success() -> None:
    with patch(
        "scripts.integration_tests.combo_harness.request_json",
        return_value=(204, None),
    ):
        assert delete_task("https://api.example", "task-1") is True


def test_delete_task_returns_false_on_404() -> None:
    with patch(
        "scripts.integration_tests.combo_harness.request_json",
        return_value=(404, {"error": "not found"}),
    ):
        assert delete_task("https://api.example", "task-1") is False


def test_list_entity_tasks_returns_empty_on_404() -> None:
    with patch(
        "scripts.integration_tests.combo_harness.request_json",
        return_value=(404, None),
    ):
        assert list_entity_tasks("https://api.example", "asset-1") == []


def test_list_entity_tasks_returns_tasks_on_200() -> None:
    tasks = [{"task_id": "t1"}, {"task_id": "t2"}]
    with patch(
        "scripts.integration_tests.combo_harness.request_json",
        return_value=(200, tasks),
    ):
        assert list_entity_tasks("https://api.example", "asset-1") == tasks


def test_cleanup_entity_tasks_deletes_all() -> None:
    with patch(
        "scripts.integration_tests.combo_harness.list_entity_tasks",
        return_value=[{"task_id": "t1"}, {"task_id": "t2"}],
    ), patch(
        "scripts.integration_tests.combo_harness.delete_task",
        side_effect=[True, True],
    ):
        n = cleanup_entity_tasks("https://api.example", "asset-1")
        assert n == 2


def test_wait_for_readiness_requires_matching_entity_sync_health_when_intent_required() -> None:
    def _request_json(_method: str, url: str, payload=None, timeout: float = 10.0):  # noqa: ARG001
        if url.endswith(":8840/status"):
            return 200, {"state": "running", "sync_health_by_asset": {"asset-1": {"state": "healthy"}}}
        return 200, {"state": "running"}

    with patch(
        "scripts.integration_tests.combo_harness.request_json",
        side_effect=_request_json,
    ), patch("scripts.integration_tests.combo_harness.time.sleep"):
        try:
            wait_for_readiness(
                "127.0.0.1",
                8840,
                8841,
                timeout_s=1.0,
                entity_id="different-id",
                require_intent=True,
            )
            assert False, "expected TimeoutError"
        except TimeoutError:
            pass


def test_wait_for_readiness_accepts_none_entity_id_when_intent_required() -> None:
    responses = [
        (200, {"state": "running", "sync_health_by_asset": {"asset-1": {"state": "healthy"}}}),
        (200, {"state": "running"}),
    ]
    with patch(
        "scripts.integration_tests.combo_harness.request_json",
        side_effect=responses,
    ), patch("scripts.integration_tests.combo_harness.time.sleep"):
        result = wait_for_readiness(
            "127.0.0.1",
            8840,
            8841,
            timeout_s=1.0,
            entity_id=None,
            require_intent=True,
        )
    assert result["gateway"]["state"] == "running"
    assert result["asset"]["state"] == "running"


def test_wait_for_readiness_accepts_matching_entity_id_when_intent_required() -> None:
    responses = [
        (200, {"state": "running", "sync_health_by_asset": {"asset-7": {"state": "healthy"}}}),
        (200, {"state": "running"}),
    ]
    with patch(
        "scripts.integration_tests.combo_harness.request_json",
        side_effect=responses,
    ), patch("scripts.integration_tests.combo_harness.time.sleep"):
        result = wait_for_readiness(
            "127.0.0.1",
            8840,
            8841,
            timeout_s=1.0,
            entity_id="asset-7",
            require_intent=True,
        )
    assert result["gateway"]["state"] == "running"
    assert result["asset"]["state"] == "running"
