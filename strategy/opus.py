"""Opus self-tuning — Claude Opus reviews trades and auto-applies parameter changes.

Flow:
1. After trades execute, collect trade context (signal, market data, result)
2. Send to Claude Opus for review
3. Opus suggests parameter adjustments
4. Auto-apply to strategy/config files
5. Log all changes for audit trail
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from strategy.config import load_params, save_params

TRADES_DIR = Path(__file__).parent.parent / "trades"
CONFIG_FILE = Path(__file__).parent.parent / "strategy" / "params.json"
AUDIT_FILE = TRADES_DIR / "opus-audit.jsonl"
ENGINE_FILE = Path(__file__).parent.parent / "strategy" / "engine.py"

# Default parameters (source of truth)
DEFAULT_PARAMS = {
    "ma_weight": 0.40,
    "rsi_weight": 0.35,
    "vol_weight": 0.25,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "stop_loss_pct": 3.0,
    "take_profit_pct": 6.0,
    "max_position_pct": 20.0,
    "max_exposure_pct": 60.0,
    "cooldown_hours": 4,
    "min_signals": 2,
    "ma_fast": 10,
    "ma_slow": 30,
    "rsi_period": 14,
    "volume_window": 20,
}


# load_params and save_params imported from strategy.config


def _apply_params_to_engine(params: dict) -> None:
    """Update engine.py constants from params dict."""
    if not ENGINE_FILE.exists():
        return

    content = ENGINE_FILE.read_text()

    replacements = {
        "MA_WEIGHT": params.get("ma_weight", 0.40),
        "RSI_WEIGHT": params.get("rsi_weight", 0.35),
        "VOL_WEIGHT": params.get("vol_weight", 0.25),
        "RSI_OVERBOUGHT": params.get("rsi_overbought", 70),
        "RSI_OVERSOLD": params.get("rsi_oversold", 30),
        "COOLDOWN_HOURS": params.get("cooldown_hours", 4),
        "MIN_SIGNALS": params.get("min_signals", 2),
    }

    for const_name, value in replacements.items():
        pattern = rf"^{const_name}\s*=\s*.+$"
        replacement = f"{const_name} = {value}"
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

    ENGINE_FILE.write_text(content)


def build_review_prompt(trade: dict, market_context: dict) -> str:
    """Build the prompt for Claude Opus review."""
    params = load_params()

    return f"""You are reviewing a crypto trade for a swing trading strategy on Binance Testnet. Your job: evaluate the trade quality and suggest parameter improvements.

CURRENT STRATEGY PARAMETERS:
{json.dumps(params, indent=2)}

TRADE EXECUTED:
{json.dumps(trade, indent=2)}

MARKET CONTEXT (at time of trade):
{json.dumps(market_context, indent=2)}

RESPOND IN THIS EXACT JSON FORMAT (no markdown, no explanation outside JSON):
{{
  "trade_quality": "good|acceptable|poor",
  "signal_sound": true|false,
  "confidence_accurate": true|false,
  "rationale": "1-2 sentence assessment",
  "parameter_changes": [
    {{
      "param": "param_name",
      "old_value": <current_value>,
      "new_value": <suggested_value>,
      "reason": "why this change"
    }}
  ],
  "strategy_notes": "any broader strategy observation"
}}

Rules:
- Only suggest parameter changes if the current params clearly caused a bad trade
- Keep changes small and incremental (e.g., RSI 70→65, not 70→40)
- Don't change more than 3 params per review
- If the trade was good or acceptable with no issues, return empty parameter_changes array
- Be conservative — testnet is for learning, not wild experimentation"""


def parse_opus_response(response_text: str) -> dict | None:
    """Parse Opus JSON response, handling markdown wrapping."""
    text = response_text.strip()
    # Remove markdown code blocks if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def apply_opus_changes(review: dict) -> dict:
    """Apply Opus-suggested parameter changes.

    Returns summary of what changed.
    """
    if not review or "parameter_changes" not in review:
        return {"applied": 0, "changes": []}

    params = load_params()
    changes = []

    for change in review.get("parameter_changes", []):
        param = change.get("param")
        new_value = change.get("new_value")
        old_value = params.get(param)
        reason = change.get("reason", "")

        if param is None or new_value is None:
            continue
        if old_value is None:
            continue

        # Sanity bounds
        bounds = {
            "ma_weight": (0.1, 0.6),
            "rsi_weight": (0.1, 0.6),
            "vol_weight": (0.05, 0.5),
            "rsi_overbought": (60, 85),
            "rsi_oversold": (15, 40),
            "stop_loss_pct": (1.0, 10.0),
            "take_profit_pct": (2.0, 20.0),
            "max_position_pct": (5.0, 50.0),
            "max_exposure_pct": (20.0, 90.0),
            "cooldown_hours": (1, 24),
            "min_signals": (1, 3),
            "ma_fast": (5, 20),
            "ma_slow": (20, 60),
            "rsi_period": (7, 21),
            "volume_window": (10, 50),
        }

        if param in bounds:
            lo, hi = bounds[param]
            if not (lo <= new_value <= hi):
                changes.append({
                    "param": param,
                    "old": old_value,
                    "new": None,
                    "reason": f"REJECTED: {new_value} outside bounds [{lo}, {hi}]",
                    "opus_reason": reason,
                })
                continue

        # Weights must sum to ~1.0
        if param in ("ma_weight", "rsi_weight", "vol_weight"):
            other_weights = {
                "ma_weight": ("rsi_weight", "vol_weight"),
                "rsi_weight": ("ma_weight", "vol_weight"),
                "vol_weight": ("ma_weight", "rsi_weight"),
            }
            w1, w2 = other_weights[param]
            remaining = params[w1] + params[w2] - (new_value - old_value)
            if remaining < 0.3:
                changes.append({
                    "param": param,
                    "old": old_value,
                    "new": None,
                    "reason": "REJECTED: weight change leaves other weights too small",
                    "opus_reason": reason,
                })
                continue

        # Apply
        params[param] = new_value
        changes.append({
            "param": param,
            "old": old_value,
            "new": new_value,
            "reason": reason,
        })

    if changes:
        # Normalize weights to sum to 1.0
        weight_keys = ["ma_weight", "rsi_weight", "vol_weight"]
        weight_sum = sum(params.get(k, 0) for k in weight_keys)
        if weight_sum > 0:
            for k in weight_keys:
                params[k] = round(params.get(k, 0) / weight_sum, 4)

        # Enforce ma_slow >= ma_fast * 2
        if params.get("ma_fast") and params.get("ma_slow"):
            if params["ma_slow"] < params["ma_fast"] * 2:
                params["ma_slow"] = params["ma_fast"] * 2

        save_params(params)
        # Audit log
        audit_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trade_quality": review.get("trade_quality"),
            "changes": changes,
            "new_params": params,
        }
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(audit_entry) + "\n")

    return {"applied": len([c for c in changes if c["new"] is not None]), "changes": changes}


def get_audit_trail(limit: int = 10) -> list[dict]:
    """Read recent audit entries."""
    if not AUDIT_FILE.exists():
        return []
    entries = []
    with open(AUDIT_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries[-limit:]
