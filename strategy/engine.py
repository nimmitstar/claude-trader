"""Strategy engine — composite scoring with regime detection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

import json
from pathlib import Path

from strategy.config import load_params
from strategy.indicators import (
    atr, atr_momentum, bb_squeeze, connors_rsi, detect_regime,
    macd_histogram_signal, sma, ema,
)
from strategy.kronos_signal import get_kronos_signal

COOLDOWN_FILE = Path(__file__).parent.parent / "trades" / "cooldown.json"


class StrategyEngine:
    """Runs technical analysis with composite scoring and regime detection."""

    def __init__(self, params: dict | None = None) -> None:
        self.params = params or load_params()
        self.last_entry: dict[str, datetime] = {}
        self._load_cooldown()
        self.kronos = get_kronos_signal(
            timeout=self.params.get("kronos_timeout_seconds", 30),
        )

    def get_trend_filter(self, pair: str) -> str:
        """EMA-50 trend filter on 4h timeframe."""
        try:
            from exchange_cli.bybit import get_client
            client = get_client(mainnet=True)
            klines = client.get_klines(symbol=pair, interval=client.KLINE_INTERVAL_4HOUR, limit=50)
            closes = [float(k[4]) for k in klines]
            if len(closes) < 50:
                return "neutral"
            e = closes[0]
            mult = 2 / 51
            for c in closes[1:]:
                e = c * mult + e * (1 - mult)
            current = closes[-1]
            diff_pct = (current - e) / e
            if diff_pct > 0.005:
                return "bullish"
            elif diff_pct < -0.005:
                return "bearish"
            return "neutral"
        except Exception:
            return "neutral"

    def analyze(
        self,
        pair: str,
        bars: list[dict],
        available_usdt: float,
        timestamp: datetime | None = None,
    ) -> dict:
        p = self.params

        if not bars or len(bars) < 50:
            return {
                "pair": pair,
                "action": "hold",
                "confidence": 0.0,
                "signal_details": {
                    "regime": "insufficient_data",
                    "crsi": "insufficient_data",
                    "bb": "insufficient_data",
                    "macd": "insufficient_data",
                    "momentum": "insufficient_data",
                    "kronos": "insufficient_data",
                    "trend": "neutral",
                    "atr": 0,
                },
                "suggested_qty": 0.0,
                "suggested_usdt": 0.0,
                "current_price": 0.0,
                "rsi": 0.0,
                "ma_signal": "n/a",
                "raw_score": 0.0,
                "rationale": "insufficient_data",
                "kronos_predicted_close": None,
                "stop_loss": 0.0,
                "take_profit": 0.0,
            }

        df = pd.DataFrame(bars)
        closes = df["close"]
        highs = df["high"]
        lows = df["low"]

        # ── 1. Compute indicators ────────────────────────────────────────────
        crsi_val = connors_rsi(
            closes,
            rsi_period=p.get("crsi_rsi_period", 3),
            streak_period=p.get("crsi_streak_period", 2),
            rank_period=p.get("crsi_rank_period", 2),
        )

        bb_signal, squeeze_active, bb_width = bb_squeeze(
            closes,
            period=p.get("bb_period", 20),
            std_dev=p.get("bb_std", 2.0),
            lookback=p.get("bb_squeeze_lookback", 100),
        )

        macd_sig, macd_hist, macd_change = macd_histogram_signal(
            closes,
            fast=p.get("macd_fast", 12),
            slow=p.get("macd_slow", 26),
            signal_period=p.get("macd_signal", 9),
        )

        mom_sig, mom_val, mom_z = atr_momentum(
            closes, highs, lows,
            mom_period=p.get("atr_momentum_period", 4),
            atr_period=p.get("atr_period", 14),
            z_lookback=p.get("atr_momentum_z_lookback", 50),
        )

        atr_val = atr(highs, lows, closes, p.get("atr_period", 14))

        # ── 2. Regime detection (needs 4h bars) ─────────────────────────────
        try:
            from exchange_cli.bybit import get_client
            client = get_client(mainnet=True)
            klines_4h = client.get_klines(symbol=pair, interval=client.KLINE_INTERVAL_4HOUR, limit=50)
            bars_4h = [{"close": float(k[4]), "high": float(k[2]), "low": float(k[3])} for k in klines_4h]
            regime = detect_regime(bars_4h, atr_val)
        except Exception:
            regime = "mean_revert"

        # ── 3. Regime filter ────────────────────────────────────────────────
        if regime in ('quiet', 'volatile_chop'):
            return {
                "pair": pair,
                "action": "hold",
                "confidence": 0.0,
                "signal_details": {
                    "regime": regime,
                    "crsi": f"{crsi_val:.1f}" if crsi_val == crsi_val else "nan",
                    "bb": bb_signal,
                    "macd": macd_sig,
                    "momentum": mom_sig,
                    "kronos": "skipped",
                    "trend": "neutral",
                    "atr": round(atr_val, 4),
                    "squeeze": squeeze_active,
                },
                "suggested_qty": 0.0,
                "suggested_usdt": 0.0,
                "current_price": float(closes.iloc[-1]),
                "rsi": float(crsi_val) if crsi_val == crsi_val else 0.0,
                "ma_signal": "n/a",
                "raw_score": 0.0,
                "rationale": f"regime={regime}: no trade",
                "kronos_predicted_close": None,
                "stop_loss": 0.0,
                "take_profit": 0.0,
            }

        # ── 4. Score each indicator [-1, +1] ────────────────────────────────
        scores = {}
        details = {}

        # CRSI scoring
        if crsi_val == crsi_val:  # not nan
            scores['crsi'] = max(-1, min(1, (50 - crsi_val) / 35))
            details['crsi'] = f"crsi={crsi_val:.1f}"
        else:
            scores['crsi'] = 0.0
            details['crsi'] = "nan"

        # BB scoring
        if bb_signal == 'buy':
            scores['bb'] = 0.8 if squeeze_active else 0.4
            details['bb'] = f"buy_squeeze={squeeze_active}"
        elif bb_signal == 'sell':
            scores['bb'] = -0.8 if squeeze_active else -0.4
            details['bb'] = f"sell_squeeze={squeeze_active}"
        elif bb_signal == 'squeeze':
            scores['bb'] = 0.0
            details['bb'] = "squeeze_active"
        else:
            scores['bb'] = 0.0
            details['bb'] = "hold"

        # MACD scoring
        if macd_sig == 'bullish':
            scores['macd'] = min(1.0, 0.3 + abs(macd_change) / (abs(macd_hist) + 1e-10) * 0.3)
            details['macd'] = f"bullish(hist={macd_hist:.4f},chg={macd_change:.4f})"
        elif macd_sig == 'bearish':
            scores['macd'] = max(-1.0, -(0.3 + abs(macd_change) / (abs(macd_hist) + 1e-10) * 0.3))
            details['macd'] = f"bearish(hist={macd_hist:.4f},chg={macd_change:.4f})"
        else:
            scores['macd'] = 0.0
            details['macd'] = "neutral"

        # Momentum scoring
        if mom_sig == 'bullish':
            scores['momentum'] = min(1.0, mom_z / 3.0)
            details['momentum'] = f"bullish(z={mom_z:.2f})"
        elif mom_sig == 'bearish':
            scores['momentum'] = max(-1.0, mom_z / 3.0)
            details['momentum'] = f"bearish(z={mom_z:.2f})"
        else:
            scores['momentum'] = 0.0
            details['momentum'] = f"neutral(z={mom_z:.2f})"

        # ── 5. Regime-dependent weights ─────────────────────────────────────
        if regime == 'trending':
            weights = {'bb': 0.30, 'macd': 0.25, 'momentum': 0.30, 'crsi': 0.15}
        else:  # mean_revert
            weights = {'crsi': 0.35, 'bb': 0.25, 'macd': 0.20, 'momentum': 0.20}

        composite = sum(scores[k] * weights[k] for k in weights)

        # ── 6. Kronos tiebreaker ────────────────────────────────────────────
        kronos_result = self.kronos.forecast(bars, pair)
        kronos_pred = kronos_result.get("predicted_close")
        kronos_dir = kronos_result.get("direction", "neutral")
        kronos_details = kronos_result.get("forecast_reasoning", "neutral")

        if kronos_pred and kronos_pred > 0:
            current_price = float(closes.iloc[-1])
            pred_pct = (kronos_pred - current_price) / current_price
            if abs(pred_pct) > 0.005:  # > 0.5%
                # Check for strong disagreement
                if (composite > 0 and pred_pct < -0.01) or (composite < 0 and pred_pct > 0.01):
                    composite *= 0.85  # reduce by 15%
                    kronos_details += " (disagreement_penalty)"
            details['kronos'] = kronos_details
        else:
            details['kronos'] = "skipped"

        # ── 7. Sentiment adjustment ──────────────────────────────────────
        try:
            from strategy.sentiment import fetch_sentiment, sentiment_modifier
            sentiment = fetch_sentiment()
            composite = sentiment_modifier(composite, pair, sentiment)
            details["sentiment"] = sentiment.get("overall_sentiment", "unknown")
            details["fear_greed"] = sentiment.get("fear_greed")
        except Exception:
            pass  # non-critical

        # ── 8. Signal thresholds ────────────────────────────────────────────
        threshold_strong_buy = p.get("composite_threshold_strong_buy", 0.60)
        threshold_buy = p.get("composite_threshold_buy", 0.35)
        threshold_sell = p.get("composite_threshold_sell", -0.35)
        threshold_strong_sell = p.get("composite_threshold_strong_sell", -0.60)
        squeeze_threshold = p.get("squeeze_threshold", 0.20)

        action = "hold"
        confidence = "low"
        rationale = "no_signal"

        if composite >= threshold_strong_buy:
            action, confidence = "buy", "high"
            rationale = f"strong_buy(composite={composite:.3f},regime={regime})"
        elif composite >= threshold_buy:
            action, confidence = "buy", "medium"
            rationale = f"buy(composite={composite:.3f},regime={regime})"
        elif composite >= squeeze_threshold and squeeze_active:
            action, confidence = "buy", "low"
            rationale = f"buy_squeeze_breakout(composite={composite:.3f})"
        elif composite <= threshold_strong_sell:
            action, confidence = "sell", "high"
            rationale = f"strong_sell(composite={composite:.3f},regime={regime})"
        elif composite <= threshold_sell:
            action, confidence = "sell", "medium"
            rationale = f"sell(composite={composite:.3f},regime={regime})"
        elif composite <= -squeeze_threshold and squeeze_active:
            action, confidence = "sell", "low"
            rationale = f"sell_squeeze_breakout(composite={composite:.3f})"

        # ── 8. Trend filter → position size modifier ────────────────────────
        trend = self.get_trend_filter(pair)
        details["trend"] = trend
        trend_modifier = 1.0
        if trend == "bearish" and action == "buy":
            trend_modifier = 0.5
            rationale += " | counter-trend: size halved"
        elif trend == "bullish" and action == "sell":
            trend_modifier = 0.5
            rationale += " | counter-trend: size halved"

        # ── 9. Cooldown ─────────────────────────────────────────────────────
        if action == "buy":
            last = self.last_entry.get(pair)
            now = timestamp if timestamp else datetime.now(timezone.utc)
            cooldown_hours = p.get("cooldown_hours", 2)
            if last and now - last < timedelta(hours=cooldown_hours):
                action = "hold"
                rationale = f"cooldown_active ({cooldown_hours}h)"

        # ── 10. Position sizing (volatility-adjusted) ───────────────────────
        current_price = float(closes.iloc[-1])
        vol_groups = p.get("volatility_groups", {})
        pair_upper = pair.upper()

        if any(pair_upper in v for v in vol_groups.get("low", [])):
            base_pct = p.get("max_position_pct_low_vol", 5.0)
        elif any(pair_upper in v for v in vol_groups.get("medium", [])):
            base_pct = p.get("max_position_pct_med_vol", 4.0)
        elif any(pair_upper in v for v in vol_groups.get("high", [])):
            base_pct = p.get("max_position_pct_high_vol", 3.0)
        else:
            base_pct = 4.0

        conf_mult = {"high": 1.0, "medium": 0.75, "low": 0.5}.get(confidence, 0.5)
        position_pct = base_pct * conf_mult * trend_modifier

        active_capital = p.get("active_capital_usdt", 0)
        sizing_base = min(available_usdt, active_capital) if active_capital > 0 else available_usdt
        max_trade_usdt = sizing_base * position_pct / 100.0
        suggested_qty = max_trade_usdt / current_price if current_price > 0 else 0

        # ── 11. SL/TP via ATR ───────────────────────────────────────────────
        from strategy.risk import calculate_sl_tp
        side = action if action in ('buy', 'sell') else 'buy'
        sl, tp = calculate_sl_tp(current_price, atr_val, side=side)

        # Fill details
        details["regime"] = regime
        details["atr"] = round(atr_val, 4)
        details["squeeze"] = squeeze_active
        details["composite"] = round(composite, 3)
        details["confidence"] = confidence
        details["trend_modifier"] = trend_modifier

        conf_score = {"high": 0.8, "medium": 0.5, "low": 0.25}.get(confidence, 0.0)
        if action == "hold":
            conf_score = 0.0

        return {
            "pair": pair,
            "action": action,
            "confidence": round(conf_score, 3),
            "signal_details": details,
            "suggested_qty": round(suggested_qty, 6),
            "suggested_usdt": round(max_trade_usdt, 2),
            "current_price": current_price,
            "rsi": float(crsi_val) if crsi_val == crsi_val else 0.0,
            "ma_signal": "n/a",
            "raw_score": round(composite, 3),
            "rationale": rationale,
            "kronos_predicted_close": kronos_pred,
            "stop_loss": round(sl, 2),
            "take_profit": round(tp, 2),
            "composite_score": round(composite, 3),
            "regime": regime,
            "atr_value": round(atr_val, 4),
            "squeeze_active": squeeze_active,
        }

    def record_entry(self, pair: str, timestamp: datetime | None = None) -> None:
        self.last_entry[pair] = timestamp if timestamp else datetime.now(timezone.utc)
        self._save_cooldown()

    def _load_cooldown(self) -> None:
        if COOLDOWN_FILE.exists():
            try:
                with open(COOLDOWN_FILE) as f:
                    data = json.load(f)
                for k, v in data.items():
                    self.last_entry[k] = datetime.fromisoformat(v)
            except (json.JSONDecodeError, ValueError):
                pass

    def _save_cooldown(self) -> None:
        COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.isoformat() for k, v in self.last_entry.items()}
        with open(COOLDOWN_FILE, "w") as f:
            json.dump(data, f)
