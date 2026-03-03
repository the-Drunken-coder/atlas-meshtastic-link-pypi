"""Shared async test utilities."""
from __future__ import annotations

import asyncio
from typing import Callable


async def wait_until(condition: Callable[[], bool], *, timeout: float = 2.0, interval: float = 0.02) -> None:
    """Poll ``condition()`` until it returns True or raise AssertionError on timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("Timed out waiting for condition")
        await asyncio.sleep(interval)
