"""Message operation registry for gateway-side business traffic."""
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

Handler = Callable[[dict[str, Any], str], Any]


class OperationRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, message_type: str, handler: Handler) -> None:
        self._handlers[message_type] = handler

    async def dispatch(self, message_type: str, payload: dict[str, Any], sender: str) -> bool:
        handler = self._handlers.get(message_type)
        if handler is None:
            return False
        result = handler(payload, sender)
        if inspect.isawaitable(result):
            await result
        return True

