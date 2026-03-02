#!/usr/bin/env python3
"""Verify readiness completes without waiting for asset intent (uses connected_assets or sync_health)."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

_package_root = Path(__file__).resolve().parents[2]
if str(_package_root) not in sys.path:
    sys.path.insert(0, str(_package_root))
log = logging.getLogger(__name__)

from scripts.integration_tests.combo_harness import (
    add_common_args,
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
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    require_two_radios(log_prefix="[readiness]")
    package_root = get_package_root()

    kill_stale_port_listeners([args.gateway_port, args.asset_port], log_prefix="[readiness]")
    process = start_combo_webui(
        package_root,
        args.host,
        args.gateway_port,
        args.asset_port,
    )

    try:
        start = time.perf_counter()
        wait_for_readiness(
            args.host,
            args.gateway_port,
            args.asset_port,
            timeout_s=args.readiness_timeout_seconds,
            entity_id=None,
            require_intent=False,
        )
        elapsed = time.perf_counter() - start
        log.info("[readiness] PASS: Readiness without intent in %.1fs", elapsed)
        return 0
    except Exception as exc:
        log.info("[readiness] ERROR: %s", exc)
        return 1
    finally:
        terminate_combo_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
