"""Discord notification — posts trade summaries to the ai-trader channel."""

from __future__ import annotations

import json
import os
from pathlib import Path

TRADES_DIR = Path(__file__).parent.parent / "trades"
DISCORD_CHANNEL_ID = os.getenv("TRADER_DISCORD_CHANNEL", "1495630671635415111")
NOTIFICATION_FILE = TRADES_DIR / ".last_notification"


def format_summary(portfolio_state: dict) -> str:
    """Format a trade cycle summary for Discord."""
    signals = portfolio_state.get("signals", [])
    trades = portfolio_state.get("trades_executed", [])
    breaker = portfolio_state.get("circuit_breaker", False)

    if breaker:
        return "🛑 **CIRCUIT BREAKER** — Daily loss limit (3%) reached. Trading halted."

    lines = []
    total_value = portfolio_state.get("total_value_usdt", 0)
    usdt_avail = portfolio_state.get("usdt_available", 0)
    lines.append(f"💰 Portfolio: ${total_value:,.2f} | Available: ${usdt_avail:,.2f}")

    if trades:
        lines.append("")
        for t in trades:
            emoji = "🟢" if t["action"] == "buy" else "🔴"
            pair = t["pair"]
            qty = t.get("qty", 0)
            price = t.get("price", 0)
            conf = t.get("confidence", 0)
            sl = t.get("stop_loss", 0)
            tp = t.get("take_profit", 0)
            sl_line = f" | SL: ${sl:,.2f}" if sl else ""
            tp_line = f" | TP: ${tp:,.2f}" if tp else ""
            lines.append(f"{emoji} **{pair}** {t['action'].upper()} {qty:,.4f} @ ${price:,.2f} (conf: {conf:.0%}){sl_line}{tp_line}")

    buys = sum(1 for s in signals if s.get("action") == "buy")
    sells = sum(1 for s in signals if s.get("action") == "sell")
    holds = sum(1 for s in signals if s.get("action") == "hold")
    lines.append(f"\n📊 Signals: {buys} buy | {sells} sell | {holds} hold | {len(trades)} executed")

    return "\n".join(lines)


def save_notification(summary: str) -> None:
    """Save notification for OpenClaw to pick up."""
    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    with open(NOTIFICATION_FILE, "w") as f:
        f.write(summary)
