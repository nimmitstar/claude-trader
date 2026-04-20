"""Risk management — position sizing, exposure caps, ATR-based SL/TP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

from strategy.config import load_params

DEFAULT_STOP_LOSS_PCT = 0.03
DEFAULT_TAKE_PROFIT_PCT = 0.06
DEFAULT_MAX_EXPOSURE_PCT = 0.60
DEFAULT_MAX_POSITION_PCT = 0.20


@dataclass
class Order:
    pair: str
    side: str  # "buy" or "sell"
    qty: float
    price: float
    atr: float = 0.0  # optional: ATR for dynamic SL/TP


def calculate_sl_tp(entry_price: float, atr: float, side: str = 'buy') -> tuple[float, float]:
    """Dynamic ATR-based stop loss and take profit.

    SL = entry ± ATR * 1.5 (capped at 4%)
    TP = entry ± ATR * 3.0 (2:1 reward/risk)
    """
    params = load_params()
    sl_mult = params.get("atr_sl_mult", 1.5)
    tp_mult = params.get("atr_tp_mult", 3.0)
    max_sl_pct = params.get("max_sl_pct", 0.04)

    if atr > 0:
        sl_distance = min(entry_price * max_sl_pct, atr * sl_mult)
        tp_distance = atr * tp_mult
    else:
        # Fallback to fixed percentages
        sl_pct = params.get("stop_loss_pct", DEFAULT_STOP_LOSS_PCT * 100) / 100.0
        tp_pct = params.get("take_profit_pct", DEFAULT_TAKE_PROFIT_PCT * 100) / 100.0
        sl_distance = entry_price * sl_pct
        tp_distance = entry_price * tp_pct

    if side == 'buy':
        return entry_price - sl_distance, entry_price + tp_distance
    else:
        return entry_price + sl_distance, entry_price - tp_distance


def check_daily_circuit_breaker(trades_dir: str | Path) -> bool:
    """Check if daily realized losses exceed circuit breaker limit."""
    trades_dir = Path(trades_dir)
    today = date.today().isoformat()
    log_file = trades_dir / f"trade-log-{today}.jsonl"
    if not log_file.exists():
        return False
    params = load_params()
    capital = params.get("active_capital_usdt", 100000)
    cb_pct = params.get("circuit_breaker_daily_pct", 0.03)
    limit = capital * cb_pct
    buy_info: dict[str, dict] = {}
    realized_losses = 0.0
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            trade = json.loads(line)
            pair = trade.get("pair", "")
            action = trade.get("action", "")
            if action == "buy":
                info = buy_info.setdefault(pair, {"total_cost": 0.0, "total_qty": 0.0})
                info["total_cost"] += trade.get("price", 0) * trade.get("qty", 0)
                info["total_qty"] += trade.get("qty", 0)
            elif action == "sell":
                info = buy_info.get(pair)
                if info and info["total_qty"] > 0:
                    avg_price = info["total_cost"] / info["total_qty"]
                    sell_price = trade.get("price", 0)
                    sell_qty = trade.get("qty", 0)
                    if sell_price < avg_price:
                        realized_losses += (avg_price - sell_price) * sell_qty
                    info["total_cost"] -= avg_price * sell_qty
                    info["total_qty"] -= sell_qty
    return realized_losses > limit


def check_risk(
    positions: list[dict],
    new_order: Order,
    total_value: float,
    available_usdt: float,
) -> dict:
    """Check if a new order passes risk guardrails."""
    params = load_params()
    max_exposure_pct = params.get("max_exposure_pct", DEFAULT_MAX_EXPOSURE_PCT * 100) / 100.0

    # Volatility-adjusted position cap
    vol_groups = params.get("volatility_groups", {})
    pair_upper = new_order.pair.upper()

    if any(pair_upper in v for v in vol_groups.get("low", [])):
        max_position_pct = params.get("max_position_pct_low_vol", 5.0) / 100.0
    elif any(pair_upper in v for v in vol_groups.get("medium", [])):
        max_position_pct = params.get("max_position_pct_med_vol", 4.0) / 100.0
    elif any(pair_upper in v for v in vol_groups.get("high", [])):
        max_position_pct = params.get("max_position_pct_high_vol", 3.0) / 100.0
    else:
        max_position_pct = params.get("max_position_pct", DEFAULT_MAX_POSITION_PCT * 100) / 100.0

    active_capital = params.get("active_capital_usdt", 0)
    risk_base = active_capital if active_capital > 0 else total_value
    order_value = new_order.qty * new_order.price

    # Check USDT available
    if new_order.side == "buy" and order_value > available_usdt:
        return {
            "allowed": False,
            "reason": f"insufficient USDT: need {order_value:.2f}, have {available_usdt:.2f}",
            "stop_loss": 0,
            "take_profit": 0,
        }

    # Check single position size
    if new_order.side == "buy" and order_value > risk_base * max_position_pct * 1.001:
        return {
            "allowed": False,
            "reason": f"exceeds {max_position_pct*100:.0f}% position cap: {order_value:.2f} > {risk_base * max_position_pct:.2f}",
            "stop_loss": 0,
            "take_profit": 0,
        }

    # Check total exposure
    new_positions_exposure = sum(p.get("value_usdt", 0) for p in positions if p.get("is_new", False))
    if new_order.side == "buy":
        new_exposure = new_positions_exposure + order_value
        if new_exposure > risk_base * max_exposure_pct:
            return {
                "allowed": False,
                "reason": (
                    f"exceeds {max_exposure_pct*100:.0f}% exposure cap: "
                    f"{new_exposure:.2f} > {total_value * max_exposure_pct:.2f}"
                ),
                "stop_loss": 0,
                "take_profit": 0,
            }

    # ATR-based SL/TP if ATR available, else fixed
    if new_order.atr > 0:
        sl, tp = calculate_sl_tp(new_order.price, new_order.atr, new_order.side)
    else:
        stop_loss_pct = params.get("stop_loss_pct", DEFAULT_STOP_LOSS_PCT * 100) / 100.0
        take_profit_pct = params.get("take_profit_pct", DEFAULT_TAKE_PROFIT_PCT * 100) / 100.0
        if new_order.side == "buy":
            sl = new_order.price * (1 - stop_loss_pct)
            tp = new_order.price * (1 + take_profit_pct)
        else:
            sl = new_order.price * (1 + stop_loss_pct)
            tp = new_order.price * (1 - take_profit_pct)

    return {
        "allowed": True,
        "reason": "ok",
        "stop_loss": round(sl, 2),
        "take_profit": round(tp, 2),
    }


def calculate_position_size(available_usdt: float, pair: str = "") -> float:
    """Max USDT to allocate for a single trade (volatility-adjusted)."""
    params = load_params()
    vol_groups = params.get("volatility_groups", {})
    pair_upper = pair.upper()

    if any(pair_upper in v for v in vol_groups.get("low", [])):
        pct = params.get("max_position_pct_low_vol", 5.0)
    elif any(pair_upper in v for v in vol_groups.get("medium", [])):
        pct = params.get("max_position_pct_med_vol", 4.0)
    elif any(pair_upper in v for v in vol_groups.get("high", [])):
        pct = params.get("max_position_pct_high_vol", 3.0)
    else:
        pct = params.get("max_position_pct", DEFAULT_MAX_POSITION_PCT * 100)

    return available_usdt * pct / 100.0
