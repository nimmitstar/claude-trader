"""Discord notifier — posts trade updates via openclaw message CLI.

Called after each trading cycle. Reads notification file and sends to Discord.
Also provides send_discord() for inline notifications from any module.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

NOTIFICATION_FILE = Path(__file__).parent.parent / "trades" / ".last_notification"
CHANNEL_ID = "1495630671635415111"  # #ai-trader
DEV_CHANNEL_ID = "1495804866482667620"  # #troubleshooting


def send_discord(message: str, channel_id: str = CHANNEL_ID) -> bool:
    """Send message to Discord via openclaw. Non-blocking on failure."""
    try:
        result = subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "discord",
             "--target", channel_id,
             "--message", message],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def send_dev(message: str) -> bool:
    """Send development/troubleshooting message to #troubleshooting."""
    return send_discord(message, DEV_CHANNEL_ID)


def notify_trade(action: str, pair: str, qty: float, price: float,
                  confidence: str, rationale: str, sl: float = 0, tp: float = 0,
                  source: str = "cron") -> None:
    """Post individual trade notification."""
    emoji = "🟢" if action == "buy" else "🔴"
    sl_line = f" | SL: ${sl:,.2f}" if sl else ""
    tp_line = f" | TP: ${tp:,.2f}" if tp else ""
    msg = f"{emoji} **{action.upper()}** {qty:,.4f} {pair} @ ${price:,.2f} (conf: {confidence}){sl_line}{tp_line}\n📊 {rationale} [{source}]"
    send_discord(msg)


def notify_circuit_breaker(loss_usdt: float, capital_usdt: float) -> None:
    """Post circuit breaker alert."""
    msg = f"🛑 **CIRCUIT BREAKER TRIGGERED**\nDaily loss: ${loss_usdt:,.2f} / ${capital_usdt:,.2f} ({loss_usdt/capital_usdt*100:.1f}%)\nTrading halted until next day."
    send_discord(msg)


def notify_sl_tp(pair: str, action: str, exit_price: float, entry_price: float,
                 pnl_pct: float) -> None:
    """Post SL/TP hit notification."""
    emoji = "🛡️" if action == "sl" else "🎯"
    msg = f"{emoji} **{action.upper()} HIT** {pair} @ ${exit_price:,.2f} ({pnl_pct:+.2%} from ${entry_price:,.2f})"
    send_discord(msg)


def notify_big_move(pair: str, move_pct: float, price: float, from_price: float) -> None:
    """Post big price move alert from WebSocket."""
    emoji = "📈" if move_pct > 0 else "📉"
    msg = f"{emoji} **{pair}** {move_pct:+.2%} move (${from_price:,.2f} → ${price:,.2f}) — analyzing..."
    send_discord(msg)


def notify_review(review_text: str) -> None:
    """Post daily review."""
    send_discord(review_text)


def notify_error(error: str, context: str = "") -> None:
    """Post error alert to #troubleshooting."""
    msg = f"⚠️ **Error** {context}: {error}"
    send_dev(msg)
