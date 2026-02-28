"""Radio mode profile loading."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_MODES_DIR = Path(__file__).parent


def load_mode_profile(name: str) -> dict:
    """Load a mode profile JSON by name from the modes directory.

    Returns the parsed dict.  Raises FileNotFoundError if the profile
    does not exist.
    """
    profile_path = _MODES_DIR / f"{name}.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"Mode profile not found: {profile_path}")
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"Mode profile '{name}' must be a JSON object, got {type(data).__name__}"
        )
    log.info("[CONFIG] Loaded mode profile '%s'", name)
    return data
