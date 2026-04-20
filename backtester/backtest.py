"""Walk-forward backtester with SL/TP simulation.

Fetches historical data from Binance public API, runs strategy simulation,
and generates performance report with overfitting detection.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from trader.runner import check_sl_tp


# Mock Kronos for backtesting (skip ML model loading)
class MockKronos:
    """Mock Kronos that returns neutral signals for backtesting."""

    def forecast(self, bars: list[dict], pair: str) -> dict:
        return {
            "direction": "neutral",
            "confidence": 0.0,
            "predicted_close": None,
            "forecast_reasoning": "backtest_skip_kronos",
        }


class StrategyEngine:
    """Minimal StrategyEngine for backtesting (no Kronos, mock cooldown)."""

    def __init__(self, params: dict | None = None):
        self.params = params or self._default_params()
        self.last_entry: dict[str, datetime] = {}
        self.kronos = MockKronos()

    def _default_params(self) -> dict:
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
        }

    # Import indicator functions locally to avoid circular import
    def _get_indicators(self):
        from strategy.indicators import ma_crossover, rsi, volume_confirm
        return ma_crossover, rsi, volume_confirm

    def analyze(
        self,
        pair: str,
        bars: list[dict],
        available_usdt: float,
        timestamp: datetime | None = None,
    ) -> dict:
        import pandas as pd

        p = self.params

        if not bars or len(bars) < 50:
            return {"action": "hold", "confidence": 0.0, "current_price": 0.0, "raw_score": 0.0}

        df = pd.DataFrame(bars)
        closes = df["close"]
        volumes = df["volume"]
        price_changes = closes.diff()

        ma_crossover, rsi_fn, volume_confirm = self._get_indicators()

        ma_fast = p.get("ma_fast", 5)
        ma_slow = p.get("ma_slow", 15)
        rsi_period = p.get("rsi_period", 7)

        ma_signal = ma_crossover(closes, fast=ma_fast, slow=ma_slow).iloc[-1]
        rsi_val = rsi_fn(closes, period=rsi_period).iloc[-1]
        vol_confirmed = volume_confirm(price_changes, volumes).iloc[-1]
        kronos_result = self.kronos.forecast(bars, pair)

        # Score indicators
        ma_score = 0.0
        rsi_score = 0.0
        vol_score = 0.0
        kronos_score = 0.0

        if ma_signal == "buy":
            ma_score = 1.0
        elif ma_signal == "sell":
            ma_score = -1.0

        if not pd.isna(rsi_val):
            if rsi_val < p.get("rsi_oversold", 30):
                rsi_score = 1.0
            elif rsi_val > p.get("rsi_overbought", 70):
                rsi_score = -1.0
            elif 40 <= rsi_val <= 60:
                rsi_score = 0.0
            elif rsi_val < 40:
                rsi_score = 0.3
            else:
                rsi_score = -0.3

        if vol_confirmed:
            vol_score = 1.0 if price_changes.iloc[-1] > 0 else -1.0

        kronos_dir = kronos_result.get("direction", "neutral")
        kronos_conf = kronos_result.get("confidence", 0.0)
        if kronos_dir == "bullish":
            kronos_score = kronos_conf * 2 - 1
        elif kronos_dir == "bearish":
            kronos_score = -(kronos_conf * 2 - 1)

        # Weighted score
        raw_score = (
            ma_score * p.get("ma_weight", 0.30)
            + rsi_score * p.get("rsi_weight", 0.25)
            + vol_score * p.get("vol_weight", 0.20)
            + kronos_score * p.get("kronos_weight", 0.25)
        )

        scores = [ma_score, rsi_score, vol_score, kronos_score]
        bullish_count = sum(1 for s in scores if s > 0)
        bearish_count = sum(1 for s in scores if s < 0)

        action = "hold"
        rationale = "insufficient signals"
        confidence = abs(raw_score)

        min_signals = p.get("min_signals", 2)
        cooldown_hours = p.get("cooldown_hours", 1)

        if bullish_count >= min_signals and raw_score > 0.1:
            action = "buy"
            rationale = f"{bullish_count}/4 bullish signals aligned"
        elif bearish_count >= min_signals and raw_score < -0.1:
            action = "sell"
            rationale = f"{bearish_count}/4 bearish signals aligned"

        # Cooldown check
        if action == "buy":
            last = self.last_entry.get(pair)
            now = timestamp if timestamp else datetime.now(timezone.utc)
            if last and now - last < timedelta(hours=cooldown_hours):
                action = "hold"
                rationale = f"cooldown active ({cooldown_hours}h not elapsed)"

        return {
            "pair": pair,
            "action": action,
            "confidence": round(min(confidence, 1.0), 3),
            "current_price": closes.iloc[-1],
            "raw_score": round(raw_score, 3),
            "rationale": rationale,
        }

    def record_entry(self, pair: str, timestamp: datetime | None = None) -> None:
        self.last_entry[pair] = timestamp if timestamp else datetime.now(timezone.utc)

# Configuration
DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"
PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "SUIUSDT", "AAVEUSDT", "LINKUSDT", "ADAUSDT", "FETUSDT",
    "DOTUSDT", "APTUSDT", "NEARUSDT",
]
TIMEFRAME = "15m"
BASE_URL = "https://api.binance.com/api/v3/klines"

# Simulation params
STARTING_CAPITAL = 50_000.0
MAX_POSITION_PCT = 0.20
MAX_EXPOSURE_PCT = 0.55
FEE_PCT = 0.001
SLIPPAGE_PCT = 0.0005
SL_PCT = 0.03
TP_PCT = 0.06


def fetch_klines(
    symbol: str,
    interval: str = "15m",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Fetch klines from Binance public API (no auth needed).

    Args:
        symbol: e.g. "BTCUSDT"
        interval: "15m", "1h", "1d", etc.
        start_date: Fetch from this date
        end_date: Fetch until this date
        limit: Max candles per request (1000)

    Returns:
        List of OHLCV dicts
    """
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }

    if start_date:
        params["startTime"] = int(start_date.timestamp() * 1000)
    if end_date:
        params["endTime"] = int(end_date.timestamp() * 1000)

    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    klines = resp.json()

    bars = []
    for k in klines:
        bars.append({
            "timestamp": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": k[6],
            "quote_volume": float(k[7]),
            "trades": int(k[8]),
        })

    return bars


def download_data(
    symbols: list[str],
    months: int = 6,
    force: bool = False,
) -> dict[str, list[dict]]:
    """Download and cache historical kline data.

    Args:
        symbols: List of trading pairs
        months: Number of months of history to fetch
        force: Re-download even if cached

    Returns:
        Dict mapping symbol -> list of bars
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=months * 30)

    all_data = {}

    for symbol in symbols:
        cache_file = DATA_DIR / f"{symbol}_{months}m.json"

        if cache_file.exists() and not force:
            print(f"  [cached] {symbol}")
            with open(cache_file) as f:
                all_data[symbol] = json.load(f)
            continue

        print(f"  [fetch] {symbol} ({months} months)...")
        bars = []
        current_start = start_date

        while current_start < end_date:
            batch = fetch_klines(
                symbol,
                interval=TIMEFRAME,
                start_date=current_start,
                end_date=end_date,
                limit=1000,
            )
            if not batch:
                break
            bars.extend(batch)

            # Move start date forward
            last_ts = bars[-1]["timestamp"]
            current_start = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc) + timedelta(minutes=15)

            if len(batch) < 1000:
                break

        # Cache to disk
        with open(cache_file, "w") as f:
            json.dump(bars, f)

        all_data[symbol] = bars
        print(f"    → {len(bars)} candles cached")

    return all_data


class Backtester:
    """Walk-forward backtester with SL/TP simulation."""

    def __init__(
        self,
        capital: float = STARTING_CAPITAL,
        max_position_pct: float = MAX_POSITION_PCT,
        max_exposure_pct: float = MAX_EXPOSURE_PCT,
        fee_pct: float = FEE_PCT,
        slippage_pct: float = SLIPPAGE_PCT,
        sl_pct: float = SL_PCT,
        tp_pct: float = TP_PCT,
    ):
        self.capital = capital
        self.max_position_pct = max_position_pct
        self.max_exposure_pct = max_exposure_pct
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct

        # State
        self.usdt = capital
        self.positions: dict[str, dict] = {}  # pair -> {qty, entry_price, entry_ts, sl, tp}
        self.trades: list[dict] = []
        self.equity_curve: list[float] = [capital]
        self.engine = StrategyEngine()

    def _get_price_with_costs(self, price: float, side: str) -> float:
        """Apply fee and slippage to execution price."""
        if side == "buy":
            return price * (1 + self.fee_pct + self.slippage_pct)
        else:
            return price * (1 - self.fee_pct - self.slippage_pct)

    def _calculate_position_size(self, price: float) -> float:
        """Calculate max position size in USDT."""
        max_trade = self.usdt * self.max_position_pct
        current_exposure = sum(p["qty"] * p["entry_price"] for p in self.positions.values())
        available_exposure = (self.capital * self.max_exposure_pct) - current_exposure
        return min(max_trade, available_exposure, self.usdt)

    def _enter_position(self, pair: str, price: float, qty: float, ts: datetime, signal: dict) -> None:
        """Enter a long position."""
        cost = qty * self._get_price_with_costs(price, "buy")
        if cost > self.usdt:
            qty = self.usdt / self._get_price_with_costs(price, "buy")

        self.usdt -= cost
        self.positions[pair] = {
            "qty": qty,
            "entry_price": price,
            "entry_ts": ts,
            "sl": price * (1 - self.sl_pct),
            "tp": price * (1 + self.tp_pct),
        }

        self.trades.append({
            "pair": pair,
            "action": "buy",
            "qty": qty,
            "price": price,
            "timestamp": ts.isoformat(),
            "signal_confidence": signal.get("confidence", 0),
        })

    def _exit_position(self, pair: str, price: float, reason: str, ts: datetime) -> None:
        """Exit a position."""
        pos = self.positions.pop(pair)
        proceeds = pos["qty"] * self._get_price_with_costs(price, "sell")
        self.usdt += proceeds

        pnl = (price - pos["entry_price"]) * pos["qty"]
        pnl_pct = (price - pos["entry_price"]) / pos["entry_price"]

        self.trades.append({
            "pair": pair,
            "action": "sell",
            "qty": pos["qty"],
            "entry_price": pos["entry_price"],
            "exit_price": price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "timestamp": ts.isoformat(),
            "hold_minutes": (ts - pos["entry_ts"]).total_seconds() / 60,
        })

    def _check_sl_tp(self, pair: str, high: float, low: float, ts: datetime) -> bool:
        """Check if SL or TP hit. Returns True if position closed."""
        if pair not in self.positions:
            return False

        pos = self.positions[pair]
        result = check_sl_tp(pos["entry_price"], high, low, self.sl_pct, self.tp_pct)

        if result["triggered"]:
            self._exit_position(pair, result["exit_price"], result["action"], ts)
            return True

        return False

    def run(
        self,
        data: dict[str, list[dict]],
        start_idx: int = 0,
        end_idx: int | None = None,
    ) -> dict:
        """Run backtest on historical data.

        Args:
            data: Dict mapping symbol -> list of bars
            start_idx: Start at this bar index (walk-forward test window)
            end_idx: End at this bar index (None = end of data)

        Returns:
            Summary dict with trade stats
        """
        # Reset state
        self.usdt = self.capital
        self.positions = {}
        self.trades = []
        self.equity_curve = [self.capital]

        # Get min length across all pairs
        min_len = min(len(bars) for bars in data.values())
        if end_idx is None:
            end_idx = min_len

        # Need at least 30 bars to warm up indicators
        warmup = 30
        if start_idx < warmup:
            start_idx = warmup

        # Walk forward bar by bar
        for i in range(start_idx, end_idx):
            current_ts = None
            current_prices = {}

            # First, check all open positions for SL/TP
            for pair, bars in data.items():
                if i >= len(bars):
                    continue
                bar = bars[i]
                current_ts = datetime.fromtimestamp(bar["timestamp"] / 1000, tz=timezone.utc)
                current_prices[pair] = bar["close"]
                self._check_sl_tp(pair, bar["high"], bar["low"], current_ts)

            # Then, generate new signals
            for pair, bars in data.items():
                if i >= len(bars):
                    continue

                bar = bars[i]
                ts = datetime.fromtimestamp(bar["timestamp"] / 1000, tz=timezone.utc)
                price = bar["close"]

                # Skip if already in position
                if pair in self.positions:
                    continue

                # Get historical bars for indicators
                hist_bars = bars[max(0, i - 50):i + 1]
                if len(hist_bars) < 30:
                    continue

                # Run engine with historical timestamp (for cooldown)
                signal = self.engine.analyze(pair, hist_bars, self.usdt, timestamp=ts)

                # Skip Kronos signal for backtesting (no ML model)
                if signal["action"] == "buy":
                    max_size = self._calculate_position_size(price)
                    if max_size > 5:  # Min notional
                        qty = max_size / price
                        self._enter_position(pair, price, qty, ts, signal)

            # Track equity
            total_value = self.usdt + sum(
                p["qty"] * current_prices.get(pair, p["entry_price"])
                for pair, p in self.positions.items()
            )
            self.equity_curve.append(total_value)

        # Close all remaining positions at last price
        final_ts = current_ts or datetime.now(timezone.utc)
        for pair in list(self.positions.keys()):
            if pair in current_prices:
                self._exit_position(pair, current_prices[pair], "end_of_period", final_ts)

        return self._calculate_stats()

    def _calculate_stats(self) -> dict:
        """Calculate performance statistics."""
        sells = [t for t in self.trades if t["action"] == "sell"]

        if not sells:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "sharpe": 0,
                "max_drawdown": 0,
            }

        wins = [t for t in sells if t["pnl"] > 0]
        losses = [t for t in sells if t["pnl"] <= 0]

        win_rate = len(wins) / len(sells) * 100 if sells else 0
        total_pnl = sum(t["pnl"] for t in sells)
        avg_win = statistics.mean([t["pnl_pct"] for t in wins]) * 100 if wins else 0
        avg_loss = statistics.mean([t["pnl_pct"] for t in losses]) * 100 if losses else 0

        profit_factor = (
            sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))
            if losses else 0
        )

        # Max drawdown
        peak = self.equity_curve[0]
        max_dd = 0
        for val in self.equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Sharpe ratio (annualized, assuming 15m = 35 bars/day, 252 trading days)
        returns = [
            (self.equity_curve[i] - self.equity_curve[i - 1]) / self.equity_curve[i - 1]
            for i in range(1, len(self.equity_curve))
            if self.equity_curve[i - 1] > 0
        ]
        sharpe = 0
        if returns:
            mean_return = statistics.mean(returns)
            std_return = statistics.stdev(returns) if len(returns) > 1 else 0
            if std_return > 0:
                # Annualize: 15m bars → 35 * 252 = 8820 periods/year
                sharpe = (mean_return / std_return) * math.sqrt(8820)

        # Win rate 95% CI (Wilson score interval)
        ci_lower = 0
        ci_upper = 0
        if sells:
            n = len(sells)
            p = len(wins) / n
            z = 1.96  # 95% CI
            denom = 2 * (n + z**2)
            center = (2 * n * p + z**2) / denom
            margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
            ci_lower = max(0, (center - margin) * 100)
            ci_upper = min(100, (center + margin) * 100)

        return {
            "total_trades": len(sells),
            "win_rate": round(win_rate, 1),
            "win_rate_ci": (round(ci_lower, 1), round(ci_upper, 1)),
            "total_pnl": round(total_pnl, 2),
            "pnl_pct": round(total_pnl / self.capital * 100, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe": round(sharpe, 2),
            "final_capital": round(self.equity_curve[-1], 2),
        }


def calculate_parameter_sensitivity(
    data: dict[str, list[dict]],
    train_end_idx: int,
    test_start_idx: int,
    test_end_idx: int,
) -> dict:
    """Run backtest with perturbed params to check overfitting."""
    base_params = {"rsi_period": 7, "ma_fast": 5, "ma_slow": 15, "threshold": 0.1}

    results = {"default": None, "perturbed": None}

    # Default params
    bt = Backtester()
    results["default"] = bt.run(data, test_start_idx, test_end_idx)

    # Perturbed params
    perturbed = StrategyEngine(params={
        "rsi_period": 8,
        "ma_fast": 5,
        "ma_slow": 14,
        "min_signals": 2,
        "rsi_overbought": 70,
        "rsi_oversold": 30,
    })
    bt_perturbed = Backtester()
    bt_perturbed.engine = perturbed
    results["perturbed"] = bt_perturbed.run(data, test_start_idx, test_end_idx)

    # Calculate sensitivity
    default_wr = results["default"]["win_rate"]
    perturbed_wr = results["perturbed"]["win_rate"]
    delta_wr = abs(default_wr - perturbed_wr) / default_wr * 100 if default_wr > 0 else 0

    default_pnl = results["default"]["total_pnl"]
    perturbed_pnl = results["perturbed"]["total_pnl"]
    delta_pnl = abs(default_pnl - perturbed_pnl) / abs(default_pnl) * 100 if default_pnl != 0 else 0

    results["delta_pct"] = max(delta_wr, delta_pnl)
    results["overfit_flag"] = results["delta_pct"] > 30

    return results


def print_report(
    train_stats: dict,
    test_stats: dict,
    param_results: dict,
    start_date: datetime,
    end_date: datetime,
    per_pair: dict[str, dict],
) -> None:
    """Print formatted backtest report."""
    print("=" * 60)
    print("BACKTEST REPORT")
    print("=" * 60)
    print(f"Period: {start_date.date()} to {end_date.date()}")
    print(f"Pairs: {len(PAIRS)}")
    print(f"Timeframe: {TIMEFRAME}")
    print()

    print("SUMMARY")
    print("-" * 40)
    s = test_stats
    print(f"Total trades: {s['total_trades']}")
    print(f"Win rate: {s['win_rate']}% (95% CI: {s['win_rate_ci'][0]}-{s['win_rate_ci'][1]}%)")
    print(f"Total P&L: ${s['total_pnl']} ({s['pnl_pct']}%)")
    print(f"Avg win: +{s['avg_win']}%, Avg loss: {s['avg_loss']}%")
    print(f"Profit factor: {s['profit_factor']}")
    print(f"Max drawdown: -{s['max_drawdown']}%")
    print(f"Sharpe ratio: {s['sharpe']}")
    print()

    print("WALK-FORWARD")
    print("-" * 40)
    print(f"Train (months 1-4): {train_stats['total_trades']} trades, {train_stats['win_rate']}% win, ${train_stats['total_pnl']} P&L")
    print(f"Test (months 5-6): {test_stats['total_trades']} trades, {test_stats['win_rate']}% win, ${test_stats['total_pnl']} P&L")
    print()

    print("PER PAIR")
    print("-" * 40)
    for pair, stats in sorted(per_pair.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
        print(f"{pair}: {stats['total_trades']} trades, {stats['win_rate']}% win, ${stats['total_pnl']}")

    print()
    print("PARAMETER STABILITY")
    print("-" * 40)
    print(f"Default: {param_results['default']['win_rate']}% win, ${param_results['default']['total_pnl']} P&L")
    print(f"Perturbed: {param_results['perturbed']['win_rate']}% win, ${param_results['perturbed']['total_pnl']} P&L")
    print(f"Delta: {param_results['delta_pct']:.1f}% -> {'OVERFIT FLAG' if param_results['overfit_flag'] else 'STABLE'}")
    print()

    print("FLAGS")
    print("-" * 40)
    s = test_stats
    if s["win_rate"] > 60:
        print("[!] Win rate > 60%: possible overfitting")
    if s["sharpe"] < 1.0:
        print("[!] Sharpe < 1.0: not worth the risk")
    if train_stats["total_pnl"] > 0:
        gap = abs(train_stats["total_pnl"] - test_stats["total_pnl"]) / abs(train_stats["total_pnl"]) * 100
        if gap > 50:
            print(f"[!] Train/test P&L gap > 50%: {gap:.1f}%")
    if param_results["overfit_flag"]:
        print(f"[!] Parameter sensitivity > 30%: {param_results['delta_pct']:.1f}%")
    if s["win_rate"] <= 60 and s["sharpe"] >= 1.0 and not param_results["overfit_flag"]:
        print("[OK] Results stable across parameter perturbation")
    print()


def main(
    months: int = 6,
    symbols: list[str] | None = None,
    quick_test: bool = False,
) -> dict:
    """Run full backtest with walk-forward validation.

    Args:
        months: Months of historical data to fetch
        symbols: Override default pair list (for quick testing)
        quick_test: If True, run quick test on 1 month of BTC only
    """
    if quick_test:
        symbols = ["BTCUSDT"]
        months = 1
        print("QUICK TEST MODE: 1 month, BTCUSDT only")

    data_symbols = symbols or PAIRS

    # Download data
    print(f"Fetching {months} months of {TIMEFRAME} data...")
    data = download_data(data_symbols, months=months)

    if not data:
        print("No data fetched!")
        return {}

    # Calculate train/test split (80/20)
    min_len = min(len(bars) for bars in data.values())
    train_end = int(min_len * 0.67)  # First 67% for train
    test_start = train_end  # Walk-forward from here

    print(f"\nTotal bars per pair: {min_len}")
    print(f"Train window: 0-{train_end}")
    print(f"Test window: {test_start}-{min_len}")
    print()

    # Run train window
    print("Running train window...")
    bt_train = Backtester()
    train_stats = bt_train.run(data, 0, train_end)

    # Run test window
    print("Running test window...")
    bt_test = Backtester()
    test_stats = bt_test.run(data, test_start, min_len)

    # Calculate per-pair stats
    per_pair = {}
    for pair in data_symbols:
        bt_pair = Backtester()
        pair_data = {pair: data[pair]}
        per_pair[pair] = bt_pair.run(pair_data, test_start, min(len(data[pair]), min_len))

    # Parameter stability
    print("Checking parameter sensitivity...")
    param_results = calculate_parameter_sensitivity(data, train_end, test_start, min_len)

    # Get date range from first pair's data
    first_bar = data[data_symbols[0]][0]
    last_bar = data[data_symbols[0]][-1]
    start_date = datetime.fromtimestamp(first_bar["timestamp"] / 1000, tz=timezone.utc)
    end_date = datetime.fromtimestamp(last_bar["timestamp"] / 1000, tz=timezone.utc)

    # Print report
    print_report(train_stats, test_stats, param_results, start_date, end_date, per_pair)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = RESULTS_DIR / f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    full_results = {
        "params": {
            "starting_capital": STARTING_CAPITAL,
            "max_position_pct": MAX_POSITION_PCT,
            "max_exposure_pct": MAX_EXPOSURE_PCT,
            "fee_pct": FEE_PCT,
            "slippage_pct": SLIPPAGE_PCT,
            "sl_pct": SL_PCT,
            "tp_pct": TP_PCT,
        },
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "train_stats": train_stats,
        "test_stats": test_stats,
        "per_pair": per_pair,
        "parameter_stability": param_results,
    }

    with open(result_file, "w") as f:
        json.dump(full_results, f, indent=2)
    print(f"Results saved to: {result_file}")

    return full_results


if __name__ == "__main__":
    import sys
    quick = "--quick" in sys.argv or "-q" in sys.argv
    main(quick_test=quick)
