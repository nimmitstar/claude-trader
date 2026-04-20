"""Technical indicators — pure pandas/numpy, no ML."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0–100)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    result = 100 - (100 / (1 + rs))
    result.iloc[:period] = float('nan')
    return result


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ── New indicators ──────────────────────────────────────────────────────────


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """Average True Range — returns latest scalar value."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_val = tr.ewm(alpha=1 / period, min_periods=period).mean()
    v = atr_val.iloc[-1]
    return float(v) if np.isfinite(v) else 0.0


def atr_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range — returns full series."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def atr_percentile(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14, lookback: int = 100) -> float:
    """ATR percentile rank over lookback bars. 0 = lowest vol, 1 = highest."""
    atr_s = atr_series(high, low, close, period)
    current = atr_s.iloc[-1]
    window = atr_s.iloc[-lookback:]
    window = window.dropna()
    if len(window) < 10 or not np.isfinite(current):
        return 0.5
    return float((window < current).sum() / len(window))


def connors_rsi(close: pd.Series, rsi_period: int = 3, streak_period: int = 2, rank_period: int = 2) -> float:
    """Connors RSI = average(RSI(period), RSI_streak, percentile_rank). Returns 0-100."""
    n = len(close)
    if n < max(rsi_period, streak_period, rank_period) + 5:
        return float('nan')

    # Component 1: Standard RSI with short period
    rsi_comp = rsi(close, rsi_period).iloc[-1]

    # Component 2: Streak RSI
    closes = close.values
    streaks = np.ones(n, dtype=float)
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            streaks[i] = streaks[i - 1] + 1 if streaks[i - 1] > 0 else 1
        elif closes[i] < closes[i - 1]:
            streaks[i] = streaks[i - 1] - 1 if streaks[i - 1] < 0 else -1
        else:
            streaks[i] = 0
    streak_series = pd.Series(streaks, index=close.index)
    streak_rsi = rsi(streak_series.abs(), streak_period).iloc[-1]
    # Sign: if streak is negative, invert
    if streaks[-1] < 0:
        streak_rsi = 100 - streak_rsi

    # Component 3: Percent rank
    if n >= rank_period + 1:
        recent = close.iloc[-(rank_period + 1):-1].values
        current = close.iloc[-1]
        rank = np.sum(recent < current)
        pct_rank = rank / len(recent) * 100
    else:
        pct_rank = 50.0

    crsi = (rsi_comp + streak_rsi + pct_rank) / 3.0
    return float(np.clip(crsi, 0, 100)) if np.isfinite(crsi) else float('nan')


def bb_squeeze(
    close: pd.Series, period: int = 20, std_dev: float = 2.0, lookback: int = 100
) -> tuple[str, bool, float]:
    """Bollinger Band squeeze detection + breakout.

    Returns: (signal, squeeze_active, bb_width)
    signal: 'buy', 'sell', 'squeeze', 'hold'
    """
    n = len(close)
    if n < period + lookback:
        return ('hold', False, 0.0)

    middle = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bb_width = (upper - lower) / middle

    current_width = bb_width.iloc[-1]
    if not np.isfinite(current_width):
        return ('hold', False, 0.0)

    # Squeeze: current width < 10th percentile of rolling lookback
    width_history = bb_width.iloc[-lookback:-1].dropna()
    if len(width_history) < 20:
        squeeze_active = False
    else:
        squeeze_threshold = width_history.quantile(0.10)
        squeeze_active = bool(current_width < squeeze_threshold)

    # Breakout detection
    current_close = close.iloc[-1]
    current_upper = upper.iloc[-1]
    current_lower = lower.iloc[-1]

    if squeeze_active:
        if current_close > current_upper:
            return ('buy', True, float(current_width))
        elif current_close < current_lower:
            return ('sell', True, float(current_width))
        return ('squeeze', True, float(current_width))

    # Non-squeeze: close beyond bands
    if current_close > current_upper:
        return ('buy', False, float(current_width))
    elif current_close < current_lower:
        return ('sell', False, float(current_width))

    return ('hold', False, float(current_width))


def macd_histogram_signal(
    close: pd.Series, fast: int = 12, slow: int = 26, signal_period: int = 9
) -> tuple[str, float, float]:
    """MACD histogram with 3-bar momentum.

    Returns: (signal, histogram_value, hist_change_3bar)
    signal: 'bullish', 'bearish', 'neutral'
    """
    _, _, hist = macd(close, fast, slow, signal_period)
    current = hist.iloc[-1]
    three_bars_ago = hist.iloc[-4] if len(hist) >= 4 else hist.iloc[0]
    change = current - three_bars_ago

    if not np.isfinite(current) or not np.isfinite(change):
        return ('neutral', 0.0, 0.0)

    if current > 0 and change > 0:
        signal = 'bullish'
    elif current < 0 and change < 0:
        signal = 'bearish'
    else:
        signal = 'neutral'

    return (signal, float(current), float(change))


def atr_momentum(
    close: pd.Series, high: pd.Series, low: pd.Series,
    mom_period: int = 4, atr_period: int = 14, z_lookback: int = 50
) -> tuple[str, float, float]:
    """ATR-normalized momentum with z-score.

    Returns: (signal, momentum_value, z_score)
    signal: 'bullish', 'bearish', 'neutral'
    """
    if len(close) < max(mom_period, atr_period, z_lookback) + 5:
        return ('neutral', 0.0, 0.0)

    atr_s = atr_series(high, low, close, atr_period)
    momentum = (close - close.shift(mom_period)) / atr_s

    recent_mom = momentum.iloc[-z_lookback:].dropna()
    if len(recent_mom) < 10:
        return ('neutral', 0.0, 0.0)

    current_mom = momentum.iloc[-1]
    if not np.isfinite(current_mom):
        return ('neutral', 0.0, 0.0)

    mean_mom = recent_mom.mean()
    std_mom = recent_mom.std()
    z = (current_mom - mean_mom) / std_mom if std_mom > 1e-10 else 0.0

    if z > 2.0:
        signal = 'bullish'
    elif z < -2.0:
        signal = 'bearish'
    else:
        signal = 'neutral'

    return (signal, float(current_mom), float(z))


def detect_regime(bars_4h: list[dict], atr_val: float) -> str:
    """Detect market regime from 4h bars + ATR.

    Returns: 'trending', 'mean_revert', 'quiet', 'volatile_chop'
    """
    if not bars_4h or len(bars_4h) < 20:
        return 'mean_revert'

    closes = pd.Series([b["close"] for b in bars_4h])
    highs = pd.Series([b["high"] for b in bars_4h])
    lows = pd.Series([b["low"] for b in bars_4h])
    avg_price = closes.iloc[-1]

    if avg_price <= 0 or atr_val <= 0:
        return 'mean_revert'

    # ADX approximation: directional movement
    prev_high = highs.shift(1)
    prev_low = lows.shift(1)
    plus_dm = (highs - prev_high).clip(lower=0)
    minus_dm = (prev_low - lows).clip(lower=0)
    tr = pd.concat([highs - lows, (highs - closes.shift(1)).abs(), (lows - closes.shift(1)).abs()], axis=1).max(axis=1)

    atr_14 = tr.ewm(alpha=1 / 14, min_periods=14).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / 14, min_periods=14).mean() / atr_14.replace(0, 1e-10))
    minus_di = 100 * (minus_dm.ewm(alpha=1 / 14, min_periods=14).mean() / atr_14.replace(0, 1e-10))
    di_diff = (plus_di - minus_di).abs()
    di_sum = plus_di + minus_di
    adx = 100 * (di_diff / di_sum.replace(0, 1e-10)).ewm(alpha=1 / 14, min_periods=14).mean()

    current_adx = adx.iloc[-1]

    # Volatility check: ATR as % of price
    atr_pct = atr_val / avg_price

    if not np.isfinite(current_adx):
        return 'mean_revert'

    if atr_pct < 0.002:
        return 'quiet'
    if atr_pct > 0.06:
        return 'volatile_chop'
    if current_adx > 25:
        return 'trending'
    if current_adx < 20:
        return 'mean_revert'
    # Between 20-25: trending if DI spread wide
    return 'trending' if di_diff.iloc[-1] > 15 else 'mean_revert'
