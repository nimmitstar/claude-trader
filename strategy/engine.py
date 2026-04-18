"""Strategy engine — runs indicators, scores signals, decides action."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

import json
from pathlib import Path

from strategy.indicators import ma_crossover, rsi, volume_confirm
from strategy.kronos_signal import get_kronos_signal

COOLDOWN_FILE = Path(__file__).parent.parent / "trades" / "cooldown.json"
PARAMS_FILE = Path(__file__).parent / "params.json"


def load_params() -> dict:
    """Load strategy parameters from params.json."""
    if PARAMS_FILE.exists():
        with open(PARAMS_FILE) as f:
            return json.load(f)
    # Fallback defaults
    return {
        "ma_weight": 0.30,
        "rsi_weight": 0.25,
        "vol_weight": 0.20,
        "kronos_weight": 0.25,
        "rsi_overbought": 70,
        "rsi_oversold": 30,
        "cooldown_hours": 1,
        "min_signals": 2,
        "ma_fast": 5,
        "ma_slow": 15,
        "rsi_period": 7,
        "volume_window": 20,
        "kronos_model": "kronos-small",
        "kronos_timeout_seconds": 30,
    }


class StrategyEngine:
    """Runs technical analysis and produces trade signals.

    Uses 4 indicators:
    - MA Crossover (momentum)
    - RSI (overbought/oversold)
    - Volume confirmation
    - Kronos ML forecast (price prediction)
    """

    def __init__(self, params: dict | None = None) -> None:
        """Initialize engine with optional params override."""
        self.params = params or load_params()
        self.last_entry: dict[str, datetime] = {}
        self._load_cooldown()

        # Initialize Kronos (lazy load on first forecast)
        self.kronos = get_kronos_signal(
            timeout=self.params.get("kronos_timeout_seconds", 30),
        )

    def analyze(
        self,
        pair: str,
        bars: list[dict],
        available_usdt: float,
    ) -> dict:
        """Analyze bars and return signal.

        Args:
            pair: e.g. "BTCUSDT"
            bars: list of OHLCV dicts from binance CLI
            available_usdt: USDT available for new positions

        Returns:
            dict with action, confidence, signal_details, suggested_qty
        """
        p = self.params

        if not bars or len(bars) < 50:
            return {
                "pair": pair,
                "action": "hold",
                "confidence": 0.0,
                "signal_details": {
                    "ma": "insufficient_data",
                    "rsi": "insufficient_data",
                    "volume": "insufficient_data",
                    "kronos": "insufficient_data",
                },
                "suggested_qty": 0.0,
                "suggested_usdt": 0.0,
                "current_price": 0.0,
                "rsi": 0.0,
                "ma_signal": "hold",
                "raw_score": 0.0,
                "rationale": "insufficient_data",
            }

        df = pd.DataFrame(bars)
        closes = df["close"]
        volumes = df["volume"]
        price_changes = closes.diff()

        # --- Run indicators (using params) ---
        ma_fast = p.get("ma_fast", 5)
        ma_slow = p.get("ma_slow", 15)
        rsi_period = p.get("rsi_period", 7)

        ma_signal = ma_crossover(closes, fast=ma_fast, slow=ma_slow).iloc[-1]
        rsi_val = rsi(closes, period=rsi_period).iloc[-1]
        vol_confirmed = volume_confirm(price_changes, volumes).iloc[-1]
        kronos_result = self.kronos.forecast(bars, pair)

        # --- Score each indicator ---
        ma_score = 0.0
        rsi_score = 0.0
        vol_score = 0.0
        kronos_score = 0.0
        signal_details: dict[str, str] = {}

        # MA crossover
        if ma_signal == "buy":
            ma_score = 1.0
            signal_details["ma"] = f"bullish_cross({ma_fast}/{ma_slow})"
        elif ma_signal == "sell":
            ma_score = -1.0
            signal_details["ma"] = f"bearish_cross({ma_fast}/{ma_slow})"
        else:
            signal_details["ma"] = "no_crossover"

        # RSI momentum
        rsi_overbought = p.get("rsi_overbought", 70)
        rsi_oversold = p.get("rsi_oversold", 30)

        if pd.isna(rsi_val):
            rsi_score = 0.0
            signal_details["rsi"] = "insufficient_data"
        elif rsi_val < rsi_oversold:
            rsi_score = 1.0
            signal_details["rsi"] = f"oversold({rsi_val:.1f})"
        elif rsi_val > rsi_overbought:
            rsi_score = -1.0
            signal_details["rsi"] = f"overbought({rsi_val:.1f})"
        elif 40 <= rsi_val <= 60:
            rsi_score = 0.0
            signal_details["rsi"] = f"neutral({rsi_val:.1f})"
        elif rsi_val < 40:
            rsi_score = 0.3
            signal_details["rsi"] = f"weak_bearish({rsi_val:.1f})"
        else:
            rsi_score = 0.3
            signal_details["rsi"] = f"weak_bullish({rsi_val:.1f})"

        # Volume
        if vol_confirmed:
            if price_changes.iloc[-1] > 0:
                vol_score = 1.0
                signal_details["volume"] = "high_volume_bullish"
            else:
                vol_score = -1.0
                signal_details["volume"] = "high_volume_bearish"
        else:
            signal_details["volume"] = "low_volume"

        # Kronos ML forecast
        kronos_dir = kronos_result.get("direction", "neutral")
        kronos_conf = kronos_result.get("confidence", 0.0)

        if kronos_dir == "bullish":
            kronos_score = kronos_conf  # Scale by confidence
            signal_details["kronos"] = kronos_result.get("forecast_reasoning", "bullish")
        elif kronos_dir == "bearish":
            kronos_score = -kronos_conf
            signal_details["kronos"] = kronos_result.get("forecast_reasoning", "bearish")
        else:
            kronos_score = 0.0
            signal_details["kronos"] = kronos_result.get("forecast_reasoning", "neutral")

        # --- Weighted score (4 indicators now) ---
        ma_weight = p.get("ma_weight", 0.30)
        rsi_weight = p.get("rsi_weight", 0.25)
        vol_weight = p.get("vol_weight", 0.20)
        kronos_weight = p.get("kronos_weight", 0.25)

        raw_score = (
            ma_score * ma_weight
            + rsi_score * rsi_weight
            + vol_score * vol_weight
            + kronos_score * kronos_weight
        )

        # --- Count aligned signals (4 indicators) ---
        scores = [ma_score, rsi_score, vol_score, kronos_score]
        bullish_count = sum(1 for s in scores if s > 0)
        bearish_count = sum(1 for s in scores if s < 0)

        # --- Determine action ---
        min_signals = p.get("min_signals", 2)
        cooldown_hours = p.get("cooldown_hours", 1)

        action = "hold"
        rationale = "insufficient signals"
        confidence = abs(raw_score)

        if bullish_count >= min_signals and raw_score > 0.1:
            action = "buy"
            rationale = f"{bullish_count}/4 bullish signals aligned"
        elif bearish_count >= min_signals and raw_score < -0.1:
            action = "sell"
            rationale = f"{bearish_count}/4 bearish signals aligned"

        # --- Cooldown check ---
        if action == "buy":
            last = self.last_entry.get(pair)
            if last and datetime.now(timezone.utc) - last < timedelta(hours=cooldown_hours):
                action = "hold"
                rationale = f"cooldown active ({cooldown_hours}h not elapsed)"

        # --- Position sizing ---
        max_trade_usdt = available_usdt * 0.20  # 20% max per trade
        current_price = closes.iloc[-1]
        suggested_qty = max_trade_usdt / current_price if current_price > 0 else 0

        return {
            "pair": pair,
            "action": action,
            "confidence": round(min(confidence, 1.0), 3),
            "signal_details": signal_details,
            "suggested_qty": round(suggested_qty, 6),
            "suggested_usdt": round(max_trade_usdt, 2),
            "current_price": current_price,
            "rsi": round(rsi_val, 1),
            "ma_signal": ma_signal,
            "raw_score": round(raw_score, 3),
            "rationale": rationale,
            "kronos_predicted_close": kronos_result.get("predicted_close"),
        }

    def record_entry(self, pair: str) -> None:
        """Record entry time for cooldown tracking."""
        self.last_entry[pair] = datetime.now(timezone.utc)
        self._save_cooldown()

    def _load_cooldown(self) -> None:
        """Load cooldown from disk."""
        if COOLDOWN_FILE.exists():
            try:
                with open(COOLDOWN_FILE) as f:
                    data = json.load(f)
                for k, v in data.items():
                    self.last_entry[k] = datetime.fromisoformat(v)
            except (json.JSONDecodeError, ValueError):
                pass

    def _save_cooldown(self) -> None:
        """Save cooldown to disk."""
        COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.isoformat() for k, v in self.last_entry.items()}
        with open(COOLDOWN_FILE, "w") as f:
            json.dump(data, f)
