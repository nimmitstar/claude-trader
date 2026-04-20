"""Risk management — position sizing, exposure caps, SL/TP."""

from __future__ import annotations

from dataclasses import dataclass

from strategy.opus import load_params

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


def check_risk(
    positions: list[dict],
    new_order: Order,
    total_value: float,
    available_usdt: float,
) -> dict:
    """Check if a new order passes risk guardrails."""
    params = load_params()
    stop_loss_pct = params.get("stop_loss_pct", DEFAULT_STOP_LOSS_PCT * 100) / 100.0
    take_profit_pct = params.get("take_profit_pct", DEFAULT_TAKE_PROFIT_PCT * 100) / 100.0
    max_exposure_pct = params.get("max_exposure_pct", DEFAULT_MAX_EXPOSURE_PCT * 100) / 100.0
    max_position_pct = params.get("max_position_pct", DEFAULT_MAX_POSITION_PCT * 100) / 100.0

    # Use active_capital as risk base if set, otherwise full portfolio
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
    if new_order.side == "buy" and order_value > risk_base * max_position_pct * 1.001:  # 0.1% tolerance for floating point
        return {
            "allowed": False,
            "reason": f"exceeds {max_position_pct*100:.0f}% position cap: {order_value:.2f} > {risk_base * max_position_pct:.2f}",
            "stop_loss": 0,
            "take_profit": 0,
        }

    # Check total exposure (only count new positions, not pre-existing holdings)
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
    elif new_order.side == "sell":
        # Reduce exposure for sells
        current_exposure = max(0, current_exposure - order_value)

    # Calculate SL/TP
    if new_order.side == "buy":
        stop_loss = new_order.price * (1 - stop_loss_pct)
        take_profit = new_order.price * (1 + take_profit_pct)
    else:
        stop_loss = new_order.price * (1 + stop_loss_pct)
        take_profit = new_order.price * (1 - take_profit_pct)

    return {
        "allowed": True,
        "reason": "ok",
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
    }


def calculate_position_size(available_usdt: float) -> float:
    """Max USDT to allocate for a single trade."""
    params = load_params()
    return available_usdt * params.get("max_position_pct", DEFAULT_MAX_POSITION_PCT * 100) / 100.0
