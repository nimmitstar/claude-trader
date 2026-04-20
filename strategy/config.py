"""Shared parameter loading/saving — single source of truth."""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "params.json"


def load_params() -> dict:
    """Load current strategy parameters."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_params(params: dict) -> None:
    """Save strategy parameters."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(params, f, indent=2)
