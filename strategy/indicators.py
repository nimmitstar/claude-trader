"""Technical indicators — pure pandas, no ML."""

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


def volume_confirm(
    price_changes: pd.Series, volumes: pd.Series, window: int = 20
) -> pd.Series:
    """True where volume > 1.5x avg on price move direction."""
    avg_vol = volumes.rolling(window=window).mean()
    bullish = (price_changes > 0) & (volumes > avg_vol * 1.5)
    bearish = (price_changes < 0) & (volumes > avg_vol * 1.5)
    return bullish | bearish


def ma_crossover(series: pd.Series, fast: int = 10, slow: int = 30) -> pd.Series:
    """Signal: 'buy' when fast crosses above slow, 'sell' when below, else 'hold'."""
    fast_ma = sma(series, fast)
    slow_ma = sma(series, slow)
    prev_diff = fast_ma.shift(1) - slow_ma.shift(1)
    curr_diff = fast_ma - slow_ma
    signal = pd.Series("hold", index=series.index)
    cross_up = (prev_diff <= 0) & (curr_diff > 0)
    cross_down = (prev_diff >= 0) & (curr_diff < 0)
    signal[cross_up] = "buy"
    signal[cross_down] = "sell"
    return signal
