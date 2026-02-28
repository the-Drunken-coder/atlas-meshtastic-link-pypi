"""Run gateway and asset web UIs together with combined terminal logs."""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path


def _stream_logs(prefix: str, pipe, log_fh=None) -> None:  # noqa: ANN001
    try:
        for line in iter(pipe.readline, ""):
            text = line.rstrip("\r\n")
            if text:
                stamped = f"[{prefix}] {text}"
                print(stamped)
                if log_fh is not None:
                    try:
                        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                        log_fh.write(f"{ts} {stamped}\n")
                        log_fh.flush()
                    except OSError:
                        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _terminate_process(process: subprocess.Popen[str], name: str, timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return

    print(f"[combo] stopping {name}...")
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[combo] {name} did not exit in {timeout:.1f}s; killing")
        process.kill()
        process.wait()


def _log_combo(msg: str, log_fh=None) -> None:  # noqa: ANN001
    stamped = f"[combo] {msg}"
    print(stamped)
    if log_fh is not None:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            log_fh.write(f"{ts} {stamped}\n")
            log_fh.flush()
        except OSError:
            pass


def _start_process(
    name: str,
    script_path: Path,
    host: str,
    port: int,
    reload_enabled: bool,
    config_path: Path | None = None,
    log_fh=None,  # noqa: ANN001
    cwd: Path | None = None,
) -> subprocess.Popen[str]:
    cmd = [sys.executable, "-u", str(script_path), "--host", host, "--port", str(port)]
    if config_path is not None:
        cmd.extend(["--config", str(config_path)])
    if reload_enabled:
        cmd.append("--reload")

    process = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if process.stdout is None:
        raise RuntimeError(f"Failed to capture stdout for {name}")

    thread = threading.Thread(
        target=_stream_logs,
        args=(name, process.stdout, log_fh),
        daemon=True,
        name=f"{name}-log-stream",
    )
    thread.start()
    return process


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    gateway_script = script_dir / "gateway_webui.py"
    asset_script = script_dir / "asset_webui.py"
    default_gateway_config = script_dir / "config" / "gateway_webui.json"
    default_asset_config = script_dir / "config" / "asset_webui.json"

    parser = argparse.ArgumentParser(description="Run gateway and asset web UIs together.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface for both web UIs")
    parser.add_argument("--gateway-port", type=int, default=8840, help="Gateway web UI port")
    parser.add_argument("--asset-port", type=int, default=8841, help="Asset web UI port")
    parser.add_argument(
        "--asset-start-delay-seconds",
        type=float,
        default=1.0,
        help="Delay before starting the asset web UI after gateway launch",
    )
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload for both scripts")
    parser.add_argument(
        "--gateway-config",
        default=str(default_gateway_config),
        help="Gateway web UI JSON config path",
    )
    parser.add_argument(
        "--asset-config",
        default=str(default_asset_config),
        help="Asset web UI JSON config path",
    )
    parser.add_argument(
        "--log-file",
        default="./combo_webui.log",
        help="Path for combined log file (set to empty string to disable)",
    )
    args = parser.parse_args()

    log_fh = None
    if args.log_file:
        try:
            log_path = Path(args.log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = log_path.open("w", encoding="utf-8")
        except OSError as exc:
            print(f"[combo] WARNING: could not open log file {args.log_file}: {exc}")

    package_root = script_dir.parent
    try:
        return _run(args, gateway_script, asset_script, package_root, log_fh)
    finally:
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass


def _run(args, gateway_script: Path, asset_script: Path, package_root: Path, log_fh) -> int:  # noqa: ANN001
    gateway_process = _start_process(
        "gateway",
        gateway_script,
        args.host,
        args.gateway_port,
        args.reload,
        Path(args.gateway_config),
        log_fh,
        cwd=package_root,
    )
    asset_start_delay_seconds = max(0.0, float(args.asset_start_delay_seconds))
    if asset_start_delay_seconds > 0:
        _log_combo(f"delaying asset start by {asset_start_delay_seconds:.1f}s", log_fh)
        time.sleep(asset_start_delay_seconds)
        gateway_code = gateway_process.poll()
        if gateway_code is not None:
            _log_combo(f"gateway exited with code {gateway_code} before asset start", log_fh)
            return gateway_code
    asset_process = _start_process(
        "asset",
        asset_script,
        args.host,
        args.asset_port,
        args.reload,
        Path(args.asset_config),
        log_fh,
        cwd=package_root,
    )

    _log_combo(f"gateway: http://{args.host}:{args.gateway_port}", log_fh)
    _log_combo(f"asset:   http://{args.host}:{args.asset_port}", log_fh)
    if log_fh is not None:
        _log_combo(f"logging to {args.log_file}", log_fh)
    _log_combo("press Ctrl+C to stop both", log_fh)

    try:
        while True:
            gateway_code = gateway_process.poll()
            asset_code = asset_process.poll()

            if gateway_code is not None:
                _log_combo(f"gateway exited with code {gateway_code}", log_fh)
                _terminate_process(asset_process, "asset")
                return gateway_code

            if asset_code is not None:
                _log_combo(f"asset exited with code {asset_code}", log_fh)
                _terminate_process(gateway_process, "gateway")
                return asset_code

            time.sleep(0.25)
    except KeyboardInterrupt:
        _log_combo("Ctrl+C received", log_fh)
        _terminate_process(gateway_process, "gateway")
        _terminate_process(asset_process, "asset")
        return 130
    finally:
        _terminate_process(gateway_process, "gateway")
        _terminate_process(asset_process, "asset")


if __name__ == "__main__":
    raise SystemExit(main())
