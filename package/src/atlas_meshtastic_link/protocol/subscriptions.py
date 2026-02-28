"""Shared helpers for subscription payload normalization."""
from __future__ import annotations

from typing import Any

TASKS_SELF_KEY = "tasks:self"


def subscription_keys(subscriptions: Any) -> set[str]:
    keys: set[str] = set()
    if not isinstance(subscriptions, dict):
        return keys
    for kind, values in subscriptions.items():
        if not isinstance(values, list):
            continue
        kind_str = str(kind)
        for value in values:
            value_str = str(value).strip()
            if value_str:
                keys.add(f"{kind_str}:{value_str}")
    return keys
