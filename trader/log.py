"""Trade logging — JSONL trade log + portfolio state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TRADES_DIR = Path(__file__).parent.parent / "trades"


def _ensure_dir() -> None:
    TRADES_DIR.mkdir(parents=True, exist_ok=True)


def log_trade(entry: dict) -> None:
    """Append a trade entry to today's log file."""
    _ensure_dir()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = TRADES_DIR / f"trade-log-{date_str}.jsonl"

    if "timestamp" not in entry:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()

    with open(log_path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def save_portfolio_state(state: dict) -> None:
    """Save current portfolio state."""
    _ensure_dir()
    path = TRADES_DIR / "portfolio-state.json"
    state["updated"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def load_portfolio_state() -> dict | None:
    """Load last saved portfolio state."""
    path = TRADES_DIR / "portfolio-state.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)
