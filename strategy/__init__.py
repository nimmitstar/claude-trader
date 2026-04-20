"""Trading strategy — technical analysis, risk management, signal generation.

Main components:
- StrategyEngine: Composite scoring with regime detection
- risk: Position sizing, exposure caps, ATR-based SL/TP
- indicators: Technical indicators (RSI, MACD, ATR, Bollinger, etc.)
- config: Shared parameter loading
- kronos_signal: ML-based price forecasting
- sentiment: Market sentiment analysis (Fear & Greed, news)
- opus: Opus self-tuning for parameter optimization
- review: Daily trade review and metrics
"""

from __future__ import annotations

__all__ = [
    "config",
    "engine",
    "indicators",
    "risk",
    "sentiment",
    "kronos_signal",
    "opus",
    "review",
]