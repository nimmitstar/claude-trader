"""Kronos price forecasting signal — ML-based 4th indicator.

Kronos is a foundation model for financial K-line forecasting:
https://github.com/shiyu-coder/Kronos

Uses Kronos-small (24.7M params) for CPU-friendly inference.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


class KronosSignal:
    """Kronos-based price forecasting signal.

    Outputs direction (bullish/bearish/neutral), confidence 0-1,
    predicted close price, and reasoning.
    """

    _tokenizer = None
    _model = None
    _predictor = None
    _load_failed = False
    _model_name = "NeoQuasar/Kronos-small"

    def __init__(self, model_name: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._model_name = model_name or self._model_name
        self._timeout = timeout

    @classmethod
    def load_model(cls, model_name: str | None = None) -> bool:
        """Load Kronos model (singleton)."""
        if cls._model is not None:
            return True
        if cls._load_failed:
            return False

        try:
            name = model_name or cls._model_name
            # Add Kronos repo to path
            kronos_path = Path("/tmp/Kronos")
            if kronos_path.exists() and str(kronos_path) not in sys.path:
                sys.path.insert(0, str(kronos_path))

            from model import KronosTokenizer, KronosPredictor
            from huggingface_hub import hf_hub_download
            import json

            # Load model config
            config_path = hf_hub_download(name, "config.json")
            with open(config_path) as f:
                config = json.load(f)

            logger.info(f"Loading Kronos model: {name}")
            cls._tokenizer = KronosTokenizer(**config["tokenizer_config"])
            cls._predictor = KronosPredictor(name, cls._tokenizer)
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

        if self._model is None and not self.load_model(self._model_name):
            return {
                "direction": "neutral",
                "confidence": 0.0,
                "predicted_close": None,
                "forecast_reasoning": "model_load_failed",
            }

        try:
            df = pd.DataFrame(bars)
            closes = df["close"].values
            current_price = closes[-1]

            # Kronos expects OHLCV data as numpy array
            # Shape: (seq_len, 5) — [open, high, low, close, volume]
            ohlcv = df[["open", "high", "low", "close", "volume"]].values
            recent = ohlcv[-64:]  # Last 64 bars

            # Run prediction
            prediction = self._predictor.predict(recent, pred_len=4)

            # prediction is predicted future OHLCV
            # Use the close price of the last predicted bar
            if isinstance(prediction, np.ndarray) and prediction.ndim >= 2:
                predicted_close = prediction[-1, 3]  # Last bar, close column
            else:
                predicted_close = float(prediction)

            if not np.isfinite(predicted_close) or predicted_close <= 0:
                return {
                    "direction": "neutral",
                    "confidence": 0.0,
                    "predicted_close": None,
                    "forecast_reasoning": "nan_prediction",
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
                "forecast_reasoning": f"inference_error: {type(e).__name__}",
            }


_kronos_instance: KronosSignal | None = None


def get_kronos_signal(model_name: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> KronosSignal:
    """Get singleton Kronos signal instance."""
    global _kronos_instance
    if _kronos_instance is None:
        _kronos_instance = KronosSignal(model_name=model_name, timeout=timeout)
    return _kronos_instance
