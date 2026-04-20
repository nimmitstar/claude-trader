"""Kronos price forecasting signal — ML-based 4th indicator.

TODO: Opus self-tuning should incorporate realized P&L feedback, not just entry signals.
This would enable adaptive parameter optimization based on actual trade outcomes.

Kronos is a foundation model for financial K-line forecasting:
https://github.com/shiyu-coder/Kronos

Uses Kronos-small (24.7M params) with Kronos-Tokenizer-base.
Falls back gracefully to 3-indicator mode if model unavailable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30

# Model repos
KRONOS_MODEL_REPO = "NeoQuasar/Kronos-small"
KRONOS_TOKENIZER_REPO = "NeoQuasar/Kronos-Tokenizer-base"


class KronosSignal:
    """Kronos-based price forecasting signal."""

    _tokenizer = None
    _model = None
    _predictor = None
    _load_failed = False

    def __init__(self, model_name: str | None = None, tokenizer_name: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._model_name = model_name or KRONOS_MODEL_REPO
        self._tokenizer_name = tokenizer_name or KRONOS_TOKENIZER_REPO
        self._timeout = timeout

    @classmethod
    def load_model(cls, model_name: str | None = None, tokenizer_name: str | None = None) -> bool:
        """Load Kronos model (singleton)."""
        if cls._model is not None:
            return True
        if cls._load_failed:
            return False

        try:
            from kronos_model import KronosTokenizer, Kronos, KronosPredictor

            m_repo = model_name or KRONOS_MODEL_REPO
            t_repo = tokenizer_name or KRONOS_TOKENIZER_REPO

            logger.info(f"Loading Kronos tokenizer: {t_repo}")
            cls._tokenizer = KronosTokenizer.from_pretrained(t_repo)

            logger.info(f"Loading Kronos model: {m_repo}")
            cls._model = Kronos.from_pretrained(m_repo)
            cls._model.eval()

            logger.info("Creating Kronos predictor")
            cls._predictor = KronosPredictor(cls._model, cls._tokenizer)
            logger.info("Kronos model loaded successfully")
            return True

        except Exception as e:
            cls._load_failed = True
            logger.warning(f"Failed to load Kronos model: {e}")
            return False

    def forecast(self, bars: list[dict], pair: str) -> dict:
        """Generate forecast from OHLCV bars.

        Args:
            bars: List of OHLCV dicts (need at least 32 bars)
            pair: Trading pair (e.g. "BTCUSDT")

        Returns:
            Signal dict with direction, confidence, predicted_close, forecast_reasoning
        """
        if len(bars) < 32:
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "predicted_close": None,
                "forecast_reasoning": f"insufficient_data ({len(bars)} < 32 bars)",
            }

        if self._model is None and not self.load_model(self._model_name, self._tokenizer_name):
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "predicted_close": None,
                "forecast_reasoning": "model_load_failed",
            }

        try:
            df = pd.DataFrame(bars)
            current_price = df["close"].iloc[-1]

            # Build DataFrame with required columns
            recent = df[["open", "high", "low", "close", "volume"]].tail(64).copy()
            recent.columns = ["open", "high", "low", "close", "volume"]

            # Build timestamps using actual bar timestamps (unix ms → pd.Timestamp)
            last_bar_ts = pd.to_datetime(df["timestamp"].iloc[-1], unit="ms")
            # Generate 15min intervals ending at last bar time
            x_timestamp = pd.date_range(
                end=last_bar_ts,
                periods=len(recent),
                freq="15min",
            )
            y_timestamp = pd.date_range(
                start=last_bar_ts + pd.Timedelta(minutes=15),
                periods=4,
                freq="15min",
            )

            # Run prediction (pred_len=4 = 1 hour of 15min candles)
            pred_df = self._predictor.predict(
                recent,
                x_timestamp,
                y_timestamp,
                pred_len=4,
                verbose=False,
            )

            # Get predicted close prices
            predicted_closes = pred_df["close"].values
            predicted_close = predicted_closes[-1]

            if not np.isfinite(predicted_close) or predicted_close <= 0:
                return {
                    "direction": "neutral",
                    "confidence": 0.0,
                    "predicted_close": None,
                    "forecast_reasoning": "nan_prediction",
                }

            # Plausibility filter: reject predictions >50% away from current price
            deviation_pct = abs(predicted_close - current_price) / current_price
            if deviation_pct > 0.10:
                logger.warning(f"Kronos garbage prediction for {pair}: predicted {predicted_close}, current {current_price} ({deviation_pct:.1%} deviation)")
                return {
                    "direction": "neutral",
                    "confidence": 0.0,
                    "predicted_close": None,
                    "forecast_reasoning": f"implausible_prediction ({deviation_pct:.1%} deviation)",
                }

            # Determine direction and confidence
            price_change_pct = (predicted_close - current_price) / current_price * 100
            confidence = min(abs(price_change_pct) / 2.0, 1.0)

            if price_change_pct > 0.2:
                direction = "bullish"
                reasoning = f"kronos_predicts_up_{price_change_pct:.2f}%"
            elif price_change_pct < -0.2:
                direction = "bearish"
                reasoning = f"kronos_predicts_down_{abs(price_change_pct):.2f}%"
            else:
                direction = "neutral"
                reasoning = f"kronos_predicts_flat_{price_change_pct:.2f}%"

            return {
                "direction": direction,
                "confidence": round(confidence, 3),
                "predicted_close": round(predicted_close, 2),
                "forecast_reasoning": reasoning,
            }

        except Exception as e:
            logger.warning(f"Kronos inference error for {pair}: {e}")
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "predicted_close": None,
                "forecast_reasoning": f"inference_error: {type(e).__name__}: {e}",
            }


_kronos_instance: KronosSignal | None = None


def get_kronos_signal(
    model_name: str | None = None,
    tokenizer_name: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> KronosSignal:
    """Get singleton Kronos signal instance."""
    global _kronos_instance
    if _kronos_instance is None:
        _kronos_instance = KronosSignal(
            model_name=model_name,
            tokenizer_name=tokenizer_name,
            timeout=timeout,
        )
    return _kronos_instance
