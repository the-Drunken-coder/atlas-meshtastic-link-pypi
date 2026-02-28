#!/usr/bin/env python3
"""Verify telemetry propagates from asset intent to Atlas API via gateway checkin."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_package_root = Path(__file__).resolve().parents[2]
if str(_package_root) not in sys.path:
    sys.path.insert(0, str(_package_root))

from scripts.integration_tests.combo_harness import (
    add_common_args,
    ensure_entity,
    get_entity,
    get_package_root,
    kill_stale_port_listeners,
    require_two_radios,
    start_combo_webui,
    terminate_combo_process,
    wait_for_readiness,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_two_radios(log_prefix="[telemetry]")
    package_root = get_package_root()

    kill_stale_port_listeners([args.gateway_port, args.asset_port], log_prefix="[telemetry]")
    process = start_combo_webui(
        package_root,
        args.host,
        args.gateway_port,
        args.asset_port,
    )

    try:
        wait_for_readiness(
            args.host,
            args.gateway_port,
            args.asset_port,
            timeout_s=args.readiness_timeout_seconds,
            entity_id=args.entity_id,
            require_intent=True,
        )
        ensure_entity(
            args.api_base_url,
            args.entity_id,
            timeout_s=args.entity_timeout_seconds,
            log_prefix="[telemetry]",
        )

        payload = get_entity(args.api_base_url, args.entity_id)
        components = payload.get("components")
        if not isinstance(components, dict):
            raise RuntimeError("Entity has no components")

        telemetry = components.get("telemetry")
        if not isinstance(telemetry, dict):
            raise RuntimeError("Entity has no components.telemetry")

        print("[telemetry] PASS: Entity has telemetry from checkin")
        return 0
    except Exception as exc:
        print(f"[telemetry] ERROR: {exc}")
        return 1
    finally:
        terminate_combo_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
