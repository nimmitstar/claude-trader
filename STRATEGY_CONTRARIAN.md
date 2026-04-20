# STRATEGY_CONTRARIAN.md — Contrarian Redesign

**Author:** Quant Research | **Date:** 2026-04-20 | **Status:** Design Doc (no code changes)

---

## 1. Why Most 15m Strategies Fail

### The Current Code's Specific Failures

**MA Crossover (5/15 SMA) is nearly useless on 15m.** A 5-period crossover fires constantly on noise, while 15-period is too slow to catch anything real. On 15m crypto, price moves are dominated by volatility clusters, not smooth trends. MA crossovers assume trend persistence — the one thing 15m candles don't have. Result: the `ma_crossover()` function returns "hold" 95%+ of the time, contributing zero signal.

**RSI-7 is a noise amplifier.** The current RSI period of 7 on 15m means you're looking at 105 minutes of data. Crypto volatility spikes make RSI-7 oscillate wildly between 20-80 with zero predictive power. The code then awards 0.3 score to "weak_bullish" (RSI 60-70) — this is literally rewarding being in a meaningless range.

**Volume confirmation with 1.5x threshold on 20-bar average is dead on arrival.** Crypto 15m volume is extremely spiky — 80%+ of bars will be below 1.5x average. The `volume_confirm()` function almost always returns False, so `vol_score` is always 0. This means 20% of your scoring weight is permanently zeroed.

**Kronos ML on 64 bars of 15m data is trying to predict noise.** A 24.7M param foundation model trained on daily/weekly K-lines is being fed 16 hours of 15m data. The 10% deviation filter catches garbage, but the "flat" predictions (within 0.2%) are useless — on 15m, 0.2% is one wick.

**The trend filter blocks profitable counter-trend trades.** EMA-50 on 4h blocks buy signals in "bearish" markets and sell signals in "bullish" markets. But on 15m, the best trades are often counter-trend bounces within the larger trend. You're filtering out your edge.

**min_signals=1 means any single indicator triggers a trade.** With 4 indicators where 1-2 are usually flat, getting 1 bullish signal is trivial. This explains "too many buys, no sells."

**SL/TP at 3%/6% is arbitrary and doesn't adapt to asset volatility.** BTC moves 2% in an hour routinely. SOL moves 5%. ADA might move 8%. A fixed 3% SL means BTC gets stopped out on noise while ADA holds through catastrophic drawdowns.

---

## 2. Non-Obvious Edges That Actually Work

### 2.1 Funding Rate Divergence
Most retail traders ignore Bybit/Binance funding rates. When funding is strongly positive (longs paying shorts) and price is still rising, it's a crowded long — high probability of a flush. When funding flips negative after a long positive streak, the reversal has started. This is not available in your current indicator set but is the single strongest signal in crypto.

### 2.2 Order Book Imbalance (Bid/Ask Depth Ratio)
Not volume — depth. A 15m candle with average volume but massive bid/ask imbalance (>2:1) predicts next-candle direction better than any TA indicator. Bybit API provides this via `orderbook` endpoint.

### 2.3 Cross-Pair Momentum Rotation
Instead of analyzing each pair independently, rank all 10 pairs by momentum (e.g., 4h return) and go long the top 2, short the bottom 2. Crypto pairs are highly correlated — when BTC dumps, everything dumps. The alpha is in *relative* performance, not absolute direction.

### 2.4 Volatility Compression Breakout
The single most reliable pattern on 15m: Bollinger Band width at 10th percentile of its 100-bar lookback + a close outside the band. This catches the start of volatility expansion. The current code has no volatility regime detection.

### 2.5 Asian Session Range (00:00-08:00 UTC)
Crypto has a well-documented Asian consolidation pattern. The high/low of this 8-hour range, when broken during US hours, has a 65-70% continuation rate. Your code trades every 15m candle equally — ignoring time-of-day structure.

---

## 3. Alternative Indicators (Replace All 4)

### Replace MA Crossover → **Bollinger Band Squeeze + Breakout**
```
BB Period: 20
BB StdDev: 2.0
Squeeze detection: BB Width < 10th percentile of rolling 100-bar BB Width
Signal: Close above upper BB after squeeze = buy. Close below lower BB after squeeze = sell.
```
Why: MA crossover looks for trend changes that already happened. BB squeeze catches volatility *before* it expands. On 15m, this is the difference between chasing and leading.

### Replace RSI-7 → **Connors RSI (CRSI)**
```
CRSI = average of:
  - RSI(3) period
  - Streak(RSI): consecutive up/down day count normalized to 0-100
  - PercentRank(close, 2) = rank of current close vs last 2 closes

Parameters: RSI_period=3, streak_period=2, rank_period=2
Buy: CRSI < 15
Sell: CRSI > 85
```
Why: Connors RSI was specifically designed for mean reversion on short timeframes. It combines momentum, streak, and magnitude — three independent signals in one. CRSI < 15 on 15m crypto is a genuine oversold condition, not RSI-7 noise.

### Replace Volume Confirm → **Volume-Weighted Price Change (VWPC)**
```
VWPC = sum(close_change * volume, window=10) / sum(volume, window=10)
Normalize: VWPC_z = (VWPC - VWPC_SMA(50)) / VWPC_STD(50)
Signal: VWPC_z > 1.5 = bullish volume momentum. VWPC_z < -1.5 = bearish.
```
Why: Raw volume threshold misses high-volume sideways bars. VWPC measures whether volume is *pushing price*. The z-score normalization adapts to each pair's typical volume profile — no fixed 1.5x threshold.

### Replace Kronos ML → **ATR-Adjusted Momentum Score**
```
ATR(14) = Average True Range
Momentum = (close - close[4]) / ATR(14)
Normalize: use rolling 50-bar z-score of momentum
Signal: momentum_z > 2.0 = strong bullish. momentum_z < -2.0 = strong bearish.
```
Why: Kronos on 16 hours of data is noise. ATR-normalized momentum tells you whether the recent move is *statistically unusual* for this specific asset. BTC moving 1% with ATR of 0.5% = meaningful. BTC moving 1% with ATR of 2% = nothing.

---

## 4. Adaptive Parameters

### RSI/CRSI Period → Scale with ATR
```
if ATR(14) / close < 0.01:    # Low vol (BTC typical)
    crsi_rsi_period = 3
elif ATR(14) / close < 0.03:  # Medium vol
    crsi_rsi_period = 4
else:                          # High vol (altcoins pumping)
    crsi_rsi_period = 5
```
Why: In low volatility, 3 bars capture a meaningful move. In high volatility, you need 4-5 bars to distinguish signal from noise.

### Position Size → Scale with ATR (Kelly-lite)
```
base_size = available_usdt * 0.05  # 5% per trade base

# ATR-adjusted: smaller positions in high vol
atr_pct = ATR(14) / close
if atr_pct > 0.03:
    size_mult = 0.5
elif atr_pct > 0.02:
    size_mult = 0.75
elif atr_pct > 0.01:
    size_mult = 1.0
else:
    size_mult = 1.25  # Can size up in low vol

# Confidence-adjusted: stronger signals get more
confidence_mult = 0.5 + (confidence * 0.5)  # 0.5x to 1.0x

position_usdt = base_size * size_mult * confidence_mult
```

### SL/TP → ATR-Based, Not Fixed %
```
sl_distance = ATR(14) * 1.5
tp_distance = ATR(14) * 3.0  # 2:1 reward/risk

sl_price = entry_price - sl_distance  # (or + for shorts)
tp_price = entry_price + tp_distance
```
Why: BTC ATR on 15m ≈ $200-400. SOL ATR ≈ $0.10-0.30. Fixed 3% SL means BTC stops at $700-800 (way too wide) and SOL stops at $0.05 (too tight). ATR-based means SL adapts to *actual* noise level.

---

## 5. Mean Reversion vs Trend Following: Regime Detection

### Regime Classification (run every 4h)
```
# Use ADX(14) on 4h bars
adx = ADX(14, 4h_bars)

# Use 200-bar rolling std of returns on 15m
vol_regime = rolling_std(close.pct_change(), 200)
vol_percentile = percentile_rank(vol_regime, lookback=1000)

REGIMES:
  "trending"      if ADX > 25
  "mean_revert"   if ADX < 20 AND vol_percentile > 50
  "quiet"         if ADX < 20 AND vol_percentile < 50
  "volatile_chop" if ADX >= 20 AND ADX <= 25 AND vol_percentile > 70
```

### Behavior by Regime
| Regime | Strategy | Indicators Weight | Position Size |
|--------|----------|-------------------|---------------|
| Trending | Follow breakout | BB: 0.4, CRSI: 0.1, VWPC: 0.3, ATR-Mom: 0.2 | 1.0x |
| Mean Revert | Fade extremes | BB: 0.2, CRSI: 0.4, VWPC: 0.2, ATR-Mom: 0.2 | 0.75x |
| Quiet | Don't trade | — | 0x |
| Volatile Chop | Don't trade | — | 0x |

**Key insight:** In "quiet" and "volatile_chop" regimes, the expected value of any trade is negative after fees. The current code trades in all conditions — this is the single biggest leak.

### Auto-Detection Implementation
```
every 4 hours:
  fetch 200 bars of 4h data
  compute ADX(14)
  fetch 200 bars of 15m data  
  compute vol_percentile
  update regime in shared state
  if regime in ["quiet", "volatile_chop"]:
    set engine to "no-trade" mode
```

---

## 6. Anti-Patterns the Current Code Gets Wrong

### 6.1 "More Indicators = Better Signal"
Current: 4 indicators with fixed weights, min_signals=1.
Reality: With 4 indicators where 2 are almost always flat (volume, Kronos), you're really running a 2-indicator system with noise. Worse, the weights (0.30/0.25/0.20/0.25) pretend all 4 contribute equally. **Anti-pattern: overweighting indicators that don't fire.**

Fix: Weight should be proportional to *actual signal rate*. If volume confirms only 5% of bars, its weight should reflect that it's a filter, not a scorer.

### 6.2 "Trend Filter Protects Against Bad Trades"
Current: EMA-50 4h blocks counter-trend trades.
Reality: On 15m, the best risk/reward trades are counter-trend scalps within a larger move. A 3% move against the 4h trend with tight ATR-based SL has better R:R than a trend-following trade that needs 6% to hit TP. **Anti-pattern: filtering trades that would have been profitable.**

Fix: Trend filter should *adjust* position size and SL/TP width, not block trades entirely.

### 6.3 "Higher Confidence = Better Trade"
Current: `confidence = abs(raw_score)`, used for position sizing.
Reality: High raw_score usually means multiple indicators agree — but they're often *the same signal dressed differently* (e.g., MA crossover and RSI both react to the same price move). True edge comes from *divergent* signals agreeing (e.g., volume spike + CRSI oversold). **Anti-pattern: double-counting correlated signals.**

Fix: Decorrelate indicators before scoring. Track cross-correlation of indicator signals and reduce weight of highly correlated pairs.

### 6.4 "Cooldown Prevents Overtrading"
Current: 2-hour cooldown per pair.
Reality: A 2-hour cooldown means you miss the next 8 candles. In a trending move, the best entry is often the second or third pullback — which hits right after cooldown expires, at the worst possible time. **Anti-pattern: arbitrary time-based risk control instead of signal-based.**

Fix: Replace cooldown with "no re-entry until signal flips to opposite" or "no re-entry if last trade is underwater."

### 6.5 "Equal Treatment of All Pairs"
Current: Same parameters for BTC ($90k) and ADA ($0.30).
Reality: BTC has 15m ATR of ~0.3%, SOL has ~1.5%, ADA has ~2%. Using the same RSI period, BB width, and SL% for all is like using the same golf club for driving and putting. **Anti-pattern: ignoring asset-specific volatility profiles.**

Fix: Normalize all indicators by ATR. Group pairs into volatility buckets (low/med/high) with different parameter sets.

---

## 7. Specific Parameter Sets

### By Volatility Bucket
```
LOW_VOL (BTC, ETH, BNB):
  bb_period=20, bb_std=2.0
  crsi_rsi_period=3, crsi_streak=2, crsi_rank=2
  vwpc_window=10, vwpc_z_threshold=1.5
  atr_mom_period=4, atr_mom_z_threshold=2.0
  sl_atr_mult=1.5, tp_atr_mult=3.0
  base_position_pct=5.0

MED_VOL (SOL, XRP, DOT, NEAR):
  bb_period=20, bb_std=2.0
  crsi_rsi_period=4, crsi_streak=2, crsi_rank=2
  vwpc_window=8, vwpc_z_threshold=1.3
  atr_mom_period=4, atr_mom_z_threshold=1.8
  sl_atr_mult=1.5, tp_atr_mult=2.5
  base_position_pct=4.0

HIGH_VOL (SUI, ADA, APT):
  bb_period=20, bb_std=2.5
  crsi_rsi_period=5, crsi_streak=2, crsi_rank=3
  vwpc_window=6, vwpc_z_threshold=1.2
  atr_mom_period=6, atr_mom_z_threshold=1.5
  sl_atr_mult=1.5, tp_atr_mult=2.5
  base_position_pct=3.0
```

### Signal Thresholds (Regime-Dependent)
```
TRENDING regime:
  min_aligned_indicators=2 (of 4)
  min_raw_score=0.15
  require: BB breakout OR ATR-Mom > 2.0

MEAN_REVERT regime:
  min_aligned_indicators=2 (of 4)
  min_raw_score=0.15
  require: CRSI < 15 OR CRSI > 85
```

### Risk Parameters
```
max_exposure_pct=40.0        # Down from 80% — never risk more than 40% at once
max_single_position_pct=8.0  # Down from 15%
max_correlated_exposure=20.0 # New: cap total exposure in same vol bucket
circuit_breaker_daily=0.02   # Tighter: 2% daily loss halt (was 3%)
trailing_stop_atr=2.0        # New: trail SL at 2x ATR after 1x ATR profit
```

---

## 8. Backtesting Approach

### Phase 1: Offline Vectorized Backtest (2 days)
```
Data: Download 6 months of 15m OHLCV for all 10 pairs from Binance public klines
   python fetch_history.py --pairs PAIRS --interval 15m --months 6 --output data/

Implementation: Pandas vectorized (no bar-by-bar loop)
  - Apply BB squeeze, CRSI, VWPC, ATR-Mom to full 6-month series
  - Simulate entries/exits with ATR-based SL/TP
  - Include 0.1% taker fee per trade (Bybit testnet rate)
  - Track: total return, max drawdown, Sharpe, win rate, avg R:R, profit factor

Benchmarks:
  - Current strategy (4 existing indicators, fixed SL/TP) — baseline
  - Buy & hold each pair
  - New strategy (4 new indicators, ATR SL/TP, regime filter)

Minimum acceptance criteria:
  - Sharpe > 1.0 (annualized)
  - Max drawdown < 15%
  - Win rate > 45% (with 2:1 R:R, this is profitable)
  - Profit factor > 1.5
  - Must beat buy & hold on at least 6/10 pairs
```

### Phase 2: Walk-Forward Validation (1 day)
```
Method: Expanding window
  - Train window: 3 months
  - Test window: 1 month  
  - Roll forward by 2 weeks, repeat 4 times
  - Average metrics across all windows

Key check: Does the strategy maintain performance out-of-sample?
  If Sharpe drops >40% from train to test, the edge is curve-fitted.
```

### Phase 3: Paper Trading (2 weeks)
```
Run the new strategy on Bybit Demo Testnet alongside the current strategy
  - Both strategies get same $100k, same 10 pairs
  - Log all signals, trades, P&L
  - Compare daily: new vs current vs benchmark (50/50 BTC/ETH buy & hold)

Go/No-Go criteria after 2 weeks:
  - New strategy P&L > current strategy P&L
  - New strategy max drawdown < 10% in any single day
  - No more than 3 consecutive losing days
  - Signal quality: at least 60% of trades should have 2+ aligned indicators
```

### Phase 4: Gradual Live Ramp (if Phase 3 passes)
```
Week 1: 25% of max position sizes (live capital)
Week 2: 50% if Week 1 profitable
Week 3: 75% if cumulative profitable
Week 4: 100% if all weeks profitable

Kill switch: Revert to current strategy if any week loses >3%
```

### Anti-Overfitting Safeguards
1. Never optimize parameters on less than 3 months of data
2. Parameters must be "round numbers" — no RSI period of 7.3
3. If a parameter only works for one pair, it's overfit — reject
4. Cross-validate: strategy must work on at least 3 different time windows
5. The regime filter itself should not be optimized — use fixed ADX thresholds (20, 25)

---

## Summary: What Changes

| Component | Current | Proposed |
|-----------|---------|----------|
| Indicator 1 | MA Crossover (5/15) | BB Squeeze Breakout (20, 2.0) |
| Indicator 2 | RSI-7 | Connors RSI (3/2/2) |
| Indicator 3 | Volume 1.5x threshold | VWPC z-score (10-bar) |
| Indicator 4 | Kronos ML | ATR-normalized Momentum |
| Trend Filter | EMA-50 4h (block trades) | ADX regime (adjust behavior) |
| SL/TP | Fixed 3%/6% | ATR-based 1.5x/3.0x |
| Position Size | Flat % | ATR × confidence scaled |
| Volume Bucket | None | Low/Med/High with different params |
| Min Signals | 1 (too loose) | 2 + directional requirement |
| Max Exposure | 80% | 40% |
| Cooldown | 2h fixed | Signal-flip based |
| Non-Trading | Never | Quiet + volatile chop regimes |

**Core thesis:** The current strategy fails because it uses indicators designed for daily charts on 15m data, applies fixed parameters across assets with wildly different volatility profiles, and trades in all market conditions. The fix is regime-aware, volatility-adaptive, and focuses on *not trading* when conditions are unfavorable.
