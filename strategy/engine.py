"""Strategy engine — runs indicators, scores signals, decides action."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

import json
from pathlib import Path

from strategy.indicators import ma_crossover, rsi, volume_confirm

COOLDOWN_FILE = Path(__file__).parent.parent / "trades" / "cooldown.json"

# Strategy weights
MA_WEIGHT = 0.40
RSI_WEIGHT = 0.35
VOL_WEIGHT = 0.25

# RSI thresholds
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# Cooldown
COOLDOWN_HOURS = 4

# Entry: need >=2 of 3 indicators aligned
MIN_SIGNALS = 2


class StrategyEngine:
    """Runs technical analysis and produces trade signals."""

    def __init__(self) -> None:
        self.last_entry: dict[str, datetime] = {}
        self._load_cooldown()

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
        if not bars or len(bars) < 50:
            return {
                "pair": pair,
                "action": "hold",
                "confidence": 0.0,
                "signal_details": {"ma": "insufficient_data", "rsi": "insufficient_data", "volume": "insufficient_data"},
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

        # --- Run indicators ---
        ma_signal = ma_crossover(closes, fast=10, slow=30).iloc[-1]
        rsi_val = rsi(closes, period=14).iloc[-1]
        vol_confirmed = volume_confirm(price_changes, volumes).iloc[-1]

        # --- Score each indicator ---
        ma_score = 0.0
        rsi_score = 0.0
        vol_score = 0.0
        signal_details: dict[str, str] = {}

        # MA crossover
        if ma_signal == "buy":
            ma_score = 1.0
            signal_details["ma"] = "bullish_crossover"
        elif ma_signal == "sell":
            ma_score = -1.0
            signal_details["ma"] = "bearish_crossover"
        else:
            signal_details["ma"] = "no_crossover"

        # RSI momentum
        if pd.isna(rsi_val):
            rsi_score = 0.0
            signal_details["rsi"] = "insufficient_data"
        elif rsi_val < RSI_OVERSOLD:
            rsi_score = 1.0
            signal_details["rsi"] = f"oversold({rsi_val:.1f})"
        elif rsi_val > RSI_OVERBOUGHT:
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
            # Check direction
            if price_changes.iloc[-1] > 0:
                vol_score = 1.0
                signal_details["volume"] = "high_volume_bullish"
            else:
                vol_score = -1.0
                signal_details["volume"] = "high_volume_bearish"
        else:
            signal_details["volume"] = "low_volume"

        # --- Weighted score ---
        raw_score = ma_score * MA_WEIGHT + rsi_score * RSI_WEIGHT + vol_score * VOL_WEIGHT

        # --- Count aligned signals ---
        bullish_count = sum(1 for s in [ma_score, rsi_score, vol_score] if s > 0)
        bearish_count = sum(1 for s in [ma_score, rsi_score, vol_score] if s < 0)

        # --- Determine action ---
        action = "hold"
        rationale = "insufficient signals"
        confidence = abs(raw_score)

        if bullish_count >= MIN_SIGNALS and raw_score > 0.1:
            action = "buy"
            rationale = f"{bullish_count}/3 bullish signals aligned"
        elif bearish_count >= MIN_SIGNALS and raw_score < -0.1:
            action = "sell"
            rationale = f"{bearish_count}/3 bearish signals aligned"

        # --- Cooldown check ---
        if action == "buy":
            last = self.last_entry.get(pair)
            if last and datetime.now(timezone.utc) - last < timedelta(hours=COOLDOWN_HOURS):
                action = "hold"
                rationale = f"cooldown active ({COOLDOWN_HOURS}h not elapsed)"

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
