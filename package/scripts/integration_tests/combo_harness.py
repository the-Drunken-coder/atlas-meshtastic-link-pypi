"""Shared harness for integration tests that use the combo webui (gateway + asset)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
log = logging.getLogger(__name__)


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any] | list[Any] | str | None]:
    """Perform HTTP request and return (status_code, parsed_body)."""
    data = None
    headers = {"accept": "application/json", "user-agent": _USER_AGENT}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    request = Request(url=url, data=data, method=method.upper(), headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            if not body:
                return response.status, None
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: dict[str, Any] | list[Any] | str = json.loads(body) if body else ""
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed
    except URLError as exc:
        raise RuntimeError(f"Request failed: {method} {url}: {exc.reason}") from exc


def kill_stale_port_listeners(ports: list[int], log_prefix: str = "[harness]") -> None:
    """Kill Windows processes listening on the given ports."""
    if os.name != "nt":
        return

    log.info("%s Checking stale listeners on ports: %s", log_prefix, ports)
    scan = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True,
        text=True,
        check=False,
    )
    if scan.returncode != 0:
        log.info(
            "%s netstat failed (exit=%s); skipping stale-port cleanup",
            log_prefix,
            scan.returncode,
        )
        return

    pairs = extract_pid_port_pairs(scan.stdout, set(ports))
    if not pairs:
        log.info("%s No stale listeners found", log_prefix)
        return

    for pid, port in sorted(pairs):
        log.info("%s Killing PID %s on port %s", log_prefix, pid, port)
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False)


def extract_pid_port_pairs(netstat_output: str, ports: set[int]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for raw_line in netstat_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        columns = line.split()
        if len(columns) < 4:
            continue
        local_address = columns[1]
        pid_value = columns[-1]
        if not pid_value.isdigit():
            continue
        if ":" not in local_address:
            continue
        maybe_port = local_address.rsplit(":", 1)[-1].rstrip("]")
        if not maybe_port.isdigit():
            continue
        port = int(maybe_port)
        if port not in ports:
            continue
        pid = int(pid_value)
        if pid > 0:
            pairs.add((pid, port))
    return pairs


def start_combo_webui(
    package_root: Path,
    host: str,
    gateway_port: int,
    asset_port: int,
    log_prefix: str = "[combo]",
) -> subprocess.Popen[str]:
    """Start combo_webui and return the process. Streams output with log_prefix."""
    command = [
        sys.executable,
        "-u",
        str(package_root / "scripts" / "combo_webui.py"),
        "--host",
        host,
        "--gateway-port",
        str(gateway_port),
        "--asset-port",
        str(asset_port),
        "--log-file",
        "",
    ]
    process = subprocess.Popen(
        command,
        cwd=str(package_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is not None:
        threading.Thread(
            target=_stream_output,
            args=(process.stdout, log_prefix),
            daemon=True,
            name="combo-webui-log-stream",
        ).start()
    return process


def _stream_output(pipe: Any, prefix: str) -> None:
    try:
        for line in iter(pipe.readline, ""):
            text = line.rstrip()
            if text:
                log.info("%s %s", prefix, text)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def wait_for_readiness(
    host: str,
    gateway_port: int,
    asset_port: int,
    timeout_s: float,
    *,
    entity_id: str | None = None,
    require_intent: bool = False,
) -> dict[str, Any]:
    """
    Wait until gateway and asset are running and ready.
    If require_intent and entity_id are set, also waits for gateway to have received asset intent.
    """
    gateway_url = f"http://{host}:{gateway_port}/status"
    asset_url = f"http://{host}:{asset_port}/status"
    deadline = time.monotonic() + timeout_s
    last_gateway: dict[str, Any] = {}
    last_asset: dict[str, Any] = {}

    while time.monotonic() < deadline:
        try:
            gateway_status, gateway_payload = request_json("GET", gateway_url, timeout=2.0)
        except (URLError, OSError, RuntimeError):
            gateway_status, gateway_payload = 0, None
        try:
            asset_status, asset_payload = request_json("GET", asset_url, timeout=2.0)
        except (URLError, OSError, RuntimeError):
            asset_status, asset_payload = 0, None

        if gateway_status == 200 and isinstance(gateway_payload, dict):
            last_gateway = gateway_payload
        if asset_status == 200 and isinstance(asset_payload, dict):
            last_asset = asset_payload

        gateway_running = last_gateway.get("state") == "running"
        asset_running = last_asset.get("state") == "running"

        sync_health = last_gateway.get("sync_health_by_asset") or {}
        has_sync_health = bool(sync_health)
        has_entity_sync_health = (
            entity_id is None or str(entity_id) in {str(key) for key in sync_health}
        )

        if require_intent:
            ready = gateway_running and asset_running and has_sync_health and has_entity_sync_health
        else:
            has_connected = bool(last_gateway.get("connected_assets"))
            ready = gateway_running and asset_running and (has_connected or has_sync_health)

        if ready:
            return {"gateway": last_gateway, "asset": last_asset}

        time.sleep(1.0)

    msg = "asset intent received" if require_intent else "asset discovery"
    raise TimeoutError(
        f"Timed out waiting for gateway/asset readiness and {msg} "
        f"(timeout={timeout_s:.0f}s, entity_id={entity_id}, gateway={last_gateway}, asset={last_asset})"
    )


def terminate_combo_process(process: subprocess.Popen[str], timeout: float = 10.0) -> None:
    """Terminate the combo webui process, killing if needed."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def get_package_root() -> Path:
    """Return the package root (next_gen_atlas_meshtastic_link)."""
    return Path(__file__).resolve().parents[2]


def require_two_radios(log_prefix: str = "[harness]") -> None:
    """
    When run as a script: exit with 0 and a message if fewer than two Meshtastic radios.
    Call at start of main() so direct runs (e.g. IDE run button) fail fast with a clear message.
    """
    pkg = get_package_root()
    src = pkg / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from atlas_meshtastic_link.transport.discovery import discover_usb_ports

        ports = discover_usb_ports()
    except Exception:
        ports = []
    if len(ports) < 2:
        log.info(
            "%s Skipping: requires at least two Meshtastic USB radios (found %s). "
            "Plug in two radios and run again.",
            log_prefix,
            len(ports),
        )
        sys.exit(0)


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common integration test args: host, ports, api, entity, timeouts."""
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--gateway-port", type=int, default=8840)
    parser.add_argument("--asset-port", type=int, default=8841)
    parser.add_argument("--api-base-url", default="https://atlascommandapi.org")
    parser.add_argument("--entity-id", default="asset-1")
    parser.add_argument("--readiness-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--entity-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--task-timeout-seconds", type=float, default=120.0)


def resolve_world_state_path(
    asset_status: dict[str, Any],
    package_root: Path,
    default: str = "./world_state.json",
) -> Path:
    """Resolve world_state path from asset status; make absolute if relative."""
    path = Path(str(asset_status.get("world_state_path") or default))
    if not path.is_absolute():
        path = package_root / path
    return path


def get_entity(api_base: str, entity_id: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET entity from API; raise RuntimeError if not 200."""
    entity_url = f"{api_base.rstrip('/')}/entities/{quote(entity_id, safe='')}"
    status_code, payload = request_json("GET", entity_url, timeout=timeout)
    if status_code != 200:
        raise RuntimeError(f"GET entity failed: status={status_code}, body={payload}")
    if not isinstance(payload, dict):
        raise RuntimeError("Entity response is not a dict")
    return payload


def get_task(api_base: str, task_id: str, timeout: float = 10.0) -> dict[str, Any]:
    """GET task from API; raise RuntimeError if not 200."""
    task_url = f"{api_base.rstrip('/')}/tasks/{quote(task_id, safe='')}"
    status_code, payload = request_json("GET", task_url, timeout=timeout)
    if status_code != 200:
        raise RuntimeError(f"GET task failed: status={status_code}, body={payload}")
    if not isinstance(payload, dict):
        raise RuntimeError("Task response is not a dict")
    return payload


def acknowledge_task(api_base: str, task_id: str, timeout: float = 10.0) -> None:
    """POST task acknowledge; raise RuntimeError if not 2xx."""
    ack_url = f"{api_base.rstrip('/')}/tasks/{quote(task_id, safe='')}/acknowledge"
    status_code, _ = request_json("POST", ack_url, timeout=timeout)
    if status_code not in {200, 201, 204}:
        raise RuntimeError(f"POST acknowledge failed: status={status_code}")


def delete_task(api_base: str, task_id: str, timeout: float = 10.0) -> bool:
    """DELETE task; return True on 2xx, False on 404, raise RuntimeError for other statuses."""
    task_url = f"{api_base.rstrip('/')}/tasks/{quote(task_id, safe='')}"
    status_code, payload = request_json("DELETE", task_url, timeout=timeout)
    if status_code == 404:
        return False
    if status_code in {200, 204}:
        return True
    raise RuntimeError(f"DELETE task failed: status={status_code}, body={payload}")


def list_entity_tasks(
    api_base: str, entity_id: str, limit: int = 100, timeout: float = 10.0
) -> list[dict[str, Any]]:
    """GET tasks for entity; return list of task dicts. Returns [] if entity missing or 404."""
    url = f"{api_base.rstrip('/')}/entities/{quote(entity_id, safe='')}/tasks?limit={limit}"
    status_code, payload = request_json("GET", url, timeout=timeout)
    if status_code == 404:
        return []
    if status_code != 200:
        raise RuntimeError(f"GET entity tasks failed: status={status_code}, body={payload}")
    if not isinstance(payload, list):
        return []
    return payload


def cleanup_entity_tasks(api_base: str, entity_id: str, timeout: float = 10.0) -> int:
    """Delete all tasks for entity; return count deleted. Idempotent."""
    tasks = list_entity_tasks(api_base, entity_id, limit=500, timeout=timeout)
    deleted = 0
    for t in tasks:
        tid = t.get("task_id") if isinstance(t, dict) else None
        if tid and delete_task(api_base, tid, timeout=timeout):
            deleted += 1
    return deleted


def world_state_contains_task(payload: dict[str, Any], task_id: str) -> bool:
    """Check if world_state JSON contains the given task (subscribed or passive)."""
    subscribed = payload.get("subscribed")
    if isinstance(subscribed, dict):
        tasks = subscribed.get("tasks")
        if isinstance(tasks, dict) and task_id in tasks:
            return True

    passive = payload.get("passive")
    if isinstance(passive, dict):
        gateway = passive.get("gateway")
        if isinstance(gateway, dict):
            tasks = gateway.get("tasks")
            if isinstance(tasks, dict):
                if task_id in tasks or f"tasks:{task_id}" in tasks:
                    return True
                for record in tasks.values():
                    if isinstance(record, dict) and (
                        record.get("id") == task_id or record.get("task_id") == task_id
                    ):
                        return True

    return False


def wait_for_task_in_world_state(
    world_state_path: Path,
    task_id: str,
    timeout_s: float,
    poll_interval_s: float = 0.25,
) -> float:
    """Poll world_state file until task appears; return perf_counter() when found."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if world_state_path.exists():
            try:
                data = json.loads(world_state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and world_state_contains_task(data, task_id):
                    return time.perf_counter()
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(poll_interval_s)

    raise TimeoutError(
        f"Task {task_id} not found in world_state {world_state_path} after {timeout_s:.0f}s"
    )


def wait_for_tasks_in_world_state(
    world_state_path: Path,
    task_ids: list[str],
    timeout_s: float,
    poll_interval_s: float = 0.25,
) -> None:
    """Poll world_state file until all task IDs appear; raise TimeoutError if any missing."""
    deadline = time.monotonic() + timeout_s
    found: set[str] = set()
    while time.monotonic() < deadline:
        if world_state_path.exists():
            try:
                data = json.loads(world_state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for tid in task_ids:
                        if tid not in found and world_state_contains_task(data, tid):
                            found.add(tid)
                    if found >= set(task_ids):
                        return
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(poll_interval_s)

    missing = set(task_ids) - found
    raise TimeoutError(
        f"Tasks {sorted(missing)} not found in world_state {world_state_path} after {timeout_s:.0f}s"
    )


def task_in_subscribed_section(world_state_data: dict[str, Any], task_id: str) -> bool:
    """Return True if task is in subscribed.tasks, False if only in passive.gateway.tasks."""
    subscribed = world_state_data.get("subscribed")
    if isinstance(subscribed, dict):
        tasks = subscribed.get("tasks")
        if isinstance(tasks, dict) and task_id in tasks:
            return True
    return False


def validate_world_state_structure(data: dict[str, Any]) -> list[str]:
    """Check presence of required keys; return list of missing keys or empty if valid."""
    required = [
        ("meta",),
        ("subscribed",),
        ("passive",),
        ("index",),
        ("subscribed", "tasks"),
        ("passive", "gateway"),
    ]
    missing: list[str] = []
    for path in required:
        obj = data
        for key in path:
            if not isinstance(obj, dict) or key not in obj:
                missing.append(".".join(path))
                break
            obj = obj[key]
    return missing


def create_task(
    api_base: str,
    entity_id: str,
    *,
    task_id_prefix: str = "test",
) -> tuple[str, float]:
    """Create a task via API; return (task_id, perf_counter start)."""
    import uuid

    task_id = f"{task_id_prefix}-{time.time_ns()}-{uuid.uuid4().hex[:6]}"
    payload = {
        "task_id": task_id,
        "entity_id": entity_id,
        "status": "pending",
    }
    task_url = f"{api_base.rstrip('/')}/tasks"
    start = time.perf_counter()
    status_code, response = request_json("POST", task_url, payload=payload, timeout=10.0)
    if status_code not in {200, 201}:
        raise RuntimeError(f"Task create failed: status={status_code}, body={response}")
    return task_id, start


def ensure_entity_exists(
    api_base: str,
    entity_id: str,
    timeout_s: float,
    *,
    log_prefix: str = "[harness]",
) -> dict[str, Any]:
    """Poll GET entity until it exists; raise TimeoutError if not found. Never creates."""
    entity_url = f"{api_base.rstrip('/')}/entities/{quote(entity_id, safe='')}"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        try:
            status_code, body = request_json("GET", entity_url, timeout=10.0)
        except RuntimeError as exc:
            # Network-level failure; treat as transient and retry.
            log.info("%s Entity request failed (transient): %s; retrying", log_prefix, exc)
            time.sleep(2.0)
            continue

        if status_code == 200:
            if not isinstance(body, dict):
                raise RuntimeError(
                    f"Unexpected entity response type: {type(body)!r}, body={body!r}"
                )
            log.info("%s Entity exists: %s", log_prefix, entity_id)
            return body

        if status_code == 404:
            log.info("%s Entity not ready yet (404); retrying", log_prefix)
            time.sleep(2.0)
            continue

        if 500 <= status_code < 600:
            log.info(
                "%s Transient server error while fetching entity %s: status=%s, body=%r; retrying",
                log_prefix,
                entity_id,
                status_code,
                body,
            )
            time.sleep(2.0)
            continue

        # Fail fast on auth/client errors (e.g., 400/401/403) to avoid masking real failures.
        raise RuntimeError(
            f"Failed to fetch entity {entity_id}: status={status_code}, body={body!r}"
        )

    raise TimeoutError(
        f"Entity {entity_id} not found after {timeout_s:.0f}s"
    )


def ensure_entity(
    api_base: str,
    entity_id: str,
    timeout_s: float,
    *,
    log_prefix: str = "[harness]",
) -> None:
    """GET entity with retry; create if 404 after timeout."""
    from urllib.parse import quote

    entity_url = f"{api_base.rstrip('/')}/entities/{quote(entity_id, safe='')}"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        status_code, _payload = request_json("GET", entity_url, timeout=5.0)
        if status_code == 200:
            log.info("%s Entity exists: %s", log_prefix, entity_id)
            return
        if status_code in {404, 500}:
            log.info("%s Entity not ready yet (status=%s); retrying", log_prefix, status_code)
        else:
            log.info("%s GET entity returned status=%s; retrying", log_prefix, status_code)
        time.sleep(2.0)

    log.info("%s Entity not found after %.0fs; creating manually", log_prefix, timeout_s)
    create_payload = {
        "entity_id": entity_id,
        "entity_type": "asset",
        "alias": entity_id,
        "subtype": "rover",
    }
    status_code, response = request_json(
        "POST",
        f"{api_base.rstrip('/')}/entities",
        payload=create_payload,
        timeout=10.0,
    )
    if status_code not in {200, 201, 409}:
        raise RuntimeError(
            f"Failed to create entity {entity_id}: status={status_code}, body={response}"
        )


def wait_for_gateway_ready(host: str, gateway_port: int, timeout_s: float) -> dict[str, Any]:
    """Wait until gateway status reports state=running."""
    gateway_url = f"http://{host}:{gateway_port}/status"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            status_code, payload = request_json("GET", gateway_url, timeout=2.0)
            if status_code == 200 and isinstance(payload, dict) and payload.get("state") == "running":
                return payload
        except (URLError, OSError, RuntimeError):
            pass
        time.sleep(1.0)
    raise TimeoutError(f"Gateway not ready after {timeout_s:.0f}s")


def start_gateway_only(
    package_root: Path,
    host: str,
    gateway_port: int,
    log_prefix: str = "[gateway]",
) -> subprocess.Popen[str]:
    """Run gateway_webui.py directly. Used for reconnect delivery test."""
    gateway_script = package_root / "scripts" / "gateway_webui.py"
    gateway_config = package_root / "scripts" / "config" / "gateway_webui.json"
    command = [
        sys.executable,
        "-u",
        str(gateway_script),
        "--host",
        host,
        "--port",
        str(gateway_port),
        "--config",
        str(gateway_config),
    ]
    process = subprocess.Popen(
        command,
        cwd=str(package_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is not None:
        threading.Thread(
            target=_stream_output,
            args=(process.stdout, log_prefix),
            daemon=True,
            name="gateway-log-stream",
        ).start()
    return process


def start_asset_only(
    package_root: Path,
    host: str,
    asset_port: int,
    log_prefix: str = "[asset]",
) -> subprocess.Popen[str]:
    """Run asset_webui.py directly. Used for reconnect delivery test."""
    asset_script = package_root / "scripts" / "asset_webui.py"
    asset_config = package_root / "scripts" / "config" / "asset_webui.json"
    command = [
        sys.executable,
        "-u",
        str(asset_script),
        "--host",
        host,
        "--port",
        str(asset_port),
        "--config",
        str(asset_config),
    ]
    process = subprocess.Popen(
        command,
        cwd=str(package_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is not None:
        threading.Thread(
            target=_stream_output,
            args=(process.stdout, log_prefix),
            daemon=True,
            name="asset-log-stream",
        ).start()
    return process
