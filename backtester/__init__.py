"""Backtester module — walk-forward validation with SL/TP simulation.

Provides historical data fetching and backtesting engine with:
- Walk-forward validation (train/test split)
- SL/TP simulation
- Parameter stability testing
- Overfitting detection
"""

from __future__ import annotations

__all__ = ["backtest"]
