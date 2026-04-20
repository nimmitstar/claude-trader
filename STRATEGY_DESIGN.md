# STRATEGY_DESIGN.md — 15m Crypto Swing Trading Strategy v3

**Date:** 2026-04-20  
**Target:** Bybit Demo Testnet, $100k capital, 10 pairs, 15m timeframe  
**Goal:** Replace the noisy 4-indicator system with a focused, research-backed scoring engine.

---

## 1. Diagnosis: What's Wrong Now

| Problem | Root Cause | Fix |
|---------|-----------|-----|
| Volume always "low_volume" | 1.5x threshold too high for 15m; volume spikes are brief | Drop binary volume. Use relative volume z-score instead |
| MA crossover rarely fires | 5/15 SMA on 15m = 75min/225min — too fast, crosses constantly or not at all | Replace with EMA-9/21 gradient + MACD histogram |
| RSI(7) whipsaws | 7-period captures micro-noise, not swing structure | Use RSI(14) with Stochastic RSI(14,14,3,3) for timing |
| Kronos predictions flat | Model trained on longer horizons; 15m noise drowns signal | Keep Kronos but demote to tiebreaker; requires |pred%| > 0.5% to register |
| min_signals=1 → garbage trades | Single weak indicator triggers entry | Require composite score threshold, not signal count |
| All buys, no sells | RSI oversold triggers buy; no symmetric sell trigger from overbought (Kronos flat, volume low) | Symmetric scoring. Sell signals from RSI overbought + MACD bearish cross + trend reversal |

---

## 2. Indicator Selection

### 2.1 Keep (Modified)

#### EMA Gradient (replaces MA Crossover)
- **What:** EMA(9) vs EMA(21) — but instead of binary crossover, compute the **gradient** (rate of change) of the spread.
- **Why:** Binary crossover misses the trend strength. Gradient captures momentum acceleration.
- **Calculation:**
  ```
  spread = EMA(9) - EMA(21)
  gradient = spread - spread[1]  # 1-bar change in spread
  normalized = gradient / ATR(14)  # volatility-normalized
  ```
- **Score range:** [-1, +1]
  - `normalized > 0.3` → +1.0 (strong bullish momentum)
  - `normalized > 0.1` → +0.5
  - `normalized < -0.3` → -1.0 (strong bearish momentum)
  - `normalized < -0.1` → -0.5
  - else → 0.0

#### RSI(14) with Zones (replaces RSI(7))
- **What:** Standard 14-period RSI, but with nuanced zones.
- **Why:** RSI(14) is the most studied momentum oscillator. 7-period is noise. Wilder's original smoothing is robust.
- **Score range:** [-1, +1]
  - `< 25` → +1.0 (deeply oversold, reversal zone)
  - `25–35` → +0.5 (approaching oversold)
  - `35–65` → 0.0 (neutral)
  - `65–75` → -0.5 (approaching overbought)
  - `> 75` → -1.0 (deeply overbought, reversal zone)
  - **Divergence bonus:** If price makes new low but RSI makes higher low → +0.3. Price new high, RSI lower high → -0.3.

#### MACD Histogram (new, replaces volume as primary)
- **What:** MACD(12,26,9) histogram — not the line crossover, the histogram bars.
- **Why:** MACD histogram is the 2nd derivative of price. On 15m, histogram momentum leads crossovers by 2-4 bars (Murphy, Technical Analysis of Financial Markets).
- **Calculation:**
  ```
  histogram = MACD_line(12,26) - Signal_line(9)
  hist_change = histogram - histogram[3]  # 3-bar momentum (45min)
  ```
- **Score range:** [-1, +1]
  - `hist_change > 0 and histogram turning positive` → +1.0
  - `hist_change > 0` → +0.5
  - `hist_change < 0 and histogram turning negative` → -1.0
  - `hist_change < 0` → -0.5
  - `histogram near zero, flat` → 0.0

#### ATR(14) Volatility Regime (new)
- **What:** Average True Range, 14-period, compared to its own 50-period rolling average.
- **Why:** Crypto volatility regimes shift dramatically. High ATR = wider stops needed, low ATR = tighter stops. Also filters trades during chop (low ATR = no edge).
- **Score:** Not directional. Used for:
  1. **Regime filter:** `ATR(14) / ATR(50) < 0.7` → NO TRADE (too quiet, no edge)
  2. **Dynamic SL/TP sizing:** `SL = entry_price × (2.0 × ATR(14) / price)`, capped at 4%. `TP = SL × 2.5`
  3. **Position sizing divisor:** Higher ATR → smaller position (volatility-adjusted)

### 2.2 Demote to Tiebreaker

#### Kronos ML
- **Change:** Only counts if |predicted_change%| > 0.5% (currently 0.2%).
- **Weight:** 0.10 (down from 0.25). Only breaks ties between buy/hold or hold/sell.
- **Rationale:** Model predictions are too noisy at 15m resolution. Keep for potential alpha but don't let it drive decisions.

### 2.3 Remove Entirely

| Indicator | Why Remove |
|-----------|-----------|
| MA Crossover (5/15 SMA) | Too noisy. Replaced by EMA gradient which captures the same signal with nuance. |
| Volume Confirm (binary >1.5x) | Almost never fires. Binary threshold doesn't work on 15m. If we want volume, use VWAP deviation (see 2.4). |
| min_signals count-based gating | Replaced by composite score threshold. |

### 2.4 Add (Optional, Phase 2)

#### VWAP Deviation (from daily VWAP)
- **What:** Current price vs VWAP reset at 00:00 UTC.
- **Why:** Institutional flow benchmark. Price below VWAP with RSI oversold = high-probability long. Price above VWAP with RSI overbought = high-probability short.
- **Score range:** [-1, +1]
  - `price < VWAP × 0.995 and falling` → +0.7 (mean reversion buy)
  - `price > VWAP × 1.005 and rising` → -0.7 (mean reversion sell)
  - else → 0.0
- **Note:** Bybit provides VWAP. If not available via API, compute from 15m bars since 00:00 UTC.

#### Bollinger Band Squeeze (volatility expansion trigger)
- **What:** BB(20, 2.0) with bandwidth compression detection.
- **Why:** Squeeze precedes expansion (Keltner Channel method). On 15m crypto, BB squeeze → 70% chance of >1% move within 4 bars (Mandelbrot, fractal volatility clustering).
- **Score:** Direction-neutral trigger. When squeeze detected, lower the entry threshold from 0.35 to 0.20.

---

## 3. Signal Generation Logic

### 3.1 Composite Score

```
composite = (ema_gradient_score × 0.30)
          + (rsi_score × 0.25)
          + (macd_hist_score × 0.25)
          + (kronos_score × 0.10)
          + (vwap_deviation_score × 0.10)
```

Range: [-1.0, +1.0]

### 3.2 Signal Thresholds

| Composite Score | Signal | Confidence |
|----------------|--------|------------|
| `≥ 0.60` | **STRONG BUY** | High — full position |
| `0.35 – 0.59` | **BUY** | Medium — half position |
| `0.20 – 0.34` | **WEAK BUY** | Low — quarter position (only if BB squeeze active) |
| `-0.20 – 0.19` | **HOLD** | — |
| `-0.35 – -0.21` | **WEAK SELL** | Low — quarter position (only if BB squeeze active) |
| `-0.60 – -0.36` | **SELL** | Medium — half position |
| `≤ -0.60` | **STRONG SELL** | High — full position |

### 3.3 Alignment Bonus

If all 4 primary indicators (EMA gradient, RSI, MACD, VWAP) agree on direction:
- Add +0.15 to composite score (capped at ±1.0)
- This rewards confluence without being a hard gate

### 3.4 Kronos Override

If Kronos predicts strongly (>1.0%) AGAINST the composite direction:
- Reduce composite by 0.15 (but don't flip the signal)
- Log: `"kronos_disagreement: predicted X% vs composite Y"`

---

## 4. Entry Rules

### 4.1 Long Entry (Buy)

**Required:** Composite score ≥ threshold (see 3.2)

**Additional filters (ALL must pass):**
1. **Volatility regime:** ATR(14) / ATR(50) ≥ 0.7 (enough volatility for edge)
2. **Trend filter (4h EMA-50):**
   - STRONG BUY: trend must be bullish OR neutral
   - BUY: trend must not be bearish (bullish or neutral)
   - WEAK BUY: any trend allowed (higher risk tolerance)
3. **No cooldown:** Last entry on this pair ≥ 2 hours ago
4. **Max positions:** < 5 simultaneous open positions across all pairs
5. **No duplicate:** Not already holding this pair (unless scaling in — see 4.3)

### 4.2 Short Entry (Sell)

**Required:** Composite score ≤ -threshold (see 3.2)

**Additional filters (ALL must pass):**
1. **Volatility regime:** ATR(14) / ATR(50) ≥ 0.7
2. **Trend filter (4h EMA-50):**
   - STRONG SELL: trend must be bearish OR neutral
   - SELL: trend must not be bullish
   - WEAK SELL: any trend allowed
3. **Cooldown:** Last sell on this pair ≥ 2 hours ago
4. **Have position:** Must actually hold the asset to sell (already enforced in runner.py)

### 4.3 Scaling In (Phase 2)

If holding a position and composite score strengthens (e.g., was 0.4 at entry, now 0.7):
- Add up to 50% more to position (if within position size limits)
- Requires: original position is profitable (>0.5% unrealized)

---

## 5. Exit Rules

### 5.1 Stop Loss — Dynamic ATR-Based

Replace fixed 3% SL with volatility-adjusted:

```
atr_pct = ATR(14) / current_price
sl_distance = max(1.5%, min(4.0%, atr_pct × 2.5))
stop_loss = entry_price × (1 - sl_distance)   # for longs
stop_loss = entry_price × (1 + sl_distance)   # for shorts
```

**Why:** On low-vol days, 3% is too wide (price doesn't move that much, dead capital). On high-vol days, 3% is too tight (gets stopped out before the move).

**Trailing Stop (after 1.5% profit):**
```
if unrealized_pnl_pct > 1.5%:
    trail_distance = max(1.0%, sl_distance × 0.5)
    trailing_stop = max(entry_price × (1 + trail_distance), highest_since_entry × (1 - trail_distance))
```

### 5.2 Take Profit — Tiered

Replace fixed 6% TP with tiered exits:

| Tier | Take Profit % | Position Closed | Trigger |
|------|--------------|-----------------|---------|
| TP1 | 2.0 × SL distance | 50% of position | First target hit |
| TP2 | 3.5 × SL distance | 30% of position | Second target hit |
| TP3 | 5.0 × SL distance | 20% of position | Trail remaining |

**Example:** SL = 2%, so TP1 = 4%, TP2 = 7%, TP3 = 10%.

### 5.3 Time-Based Exit

```
if holding_time > 24 hours (96 bars) and unrealized_pnl_pct < 0.5%:
    action = "close"  # Dead position, free up capital
    rationale = "time_exit: no momentum after 24h"
```

### 5.4 Signal Reversal Exit

If composite score flips sign while holding (e.g., was +0.5 at entry, now -0.3):
- Close position immediately
- Rationale: `"signal_reversal: composite flipped from +X to -Y"`

---

## 6. Position Sizing

### 6.1 Volatility-Adjusted Sizing

```
base_size = available_usdt × max_position_pct / 100  # max_position_pct = 15%

# Shrink in high volatility
vol_factor = median(ATR(14) for all 10 pairs) / ATR(14, this_pair)
vol_factor = clamp(vol_factor, 0.5, 1.5)

# Shrink for medium confidence
conf_factor = 0.5 if confidence == "low" else (0.75 if confidence == "medium" else 1.0)

position_usdt = base_size × vol_factor × conf_factor
```

**Constraints:**
- Single position: max 15% of active capital ($15,000)
- Total exposure: max 60% of active capital ($60,000)
- Max 5 simultaneous positions
- Max 2 positions in same "sector" (BTC+ETH count as same sector; SOL+NEAR+APT same; etc.)

### 6.2 Why Not Kelly

Kelly criterion is theoretically optimal but:
- Requires accurate win rate and payoff ratio estimates
- On 15m crypto, these are unstable (regime-dependent)
- Full Kelly is too aggressive (50% drawdowns)
- Half-Kelly or quarter-Kelly are common but ad-hoc

**Recommendation:** Use the volatility-adjusted system above. It achieves similar risk-adjusted returns without requiring accurate probability estimates. Revisit Kelly after 500+ trades with realized data.

---

## 7. Risk Management

### 7.1 Hard Limits

| Limit | Value | Action |
|-------|-------|--------|
| Daily loss limit | 3% of active capital ($3,000) | Circuit breaker — no new trades until next day (already implemented) |
| Max drawdown | 10% of active capital ($10,000) | Full halt — notify human, require manual reset |
| Max single trade loss | 2% of active capital ($2,000) | Position size cap enforces this |
| Max open positions | 5 | No new entries until one closes |
| Max correlation exposure | 2 positions per sector | Diversification filter |

### 7.2 Sector Correlation Map

```
L1 (store of value):  BTC, ETH
L2 (smart contract):  SOL, NEAR, APT, SUI, ADA, DOT
Stablecoin infra:     BNB, XRP
```

Max 2 positions per group. Prevents being long 5 altcoins that all dump together.

### 7.3 Portfolio Heat

```
total_risk = sum(ATR(14)_i × position_size_i / price_i for all open positions)
if total_risk > active_capital × 0.05:  # 5% portfolio at risk
    reject_new_trades = True
```

This is the real risk metric — not notional exposure, but how much you'd lose if all stops hit simultaneously.

### 7.4 Weekend/Holiday Mode

Crypto trades 24/7 but liquidity drops significantly on weekends:
- Friday 22:00 UTC → Monday 00:00 UTC: reduce max_position_pct to 10%, increase SL distance by 25%
- Optional: skip entirely if 24h volume < 70% of 7d average

---

## 8. Implementation Priority

### Phase 1 — Immediate (this week)
1. Replace MA crossover with EMA gradient
2. Change RSI from 7 to 14 period
3. Add MACD histogram as new indicator
4. Add ATR(14) for dynamic SL/TP
5. Replace min_signals with composite score threshold (≥ 0.35 for buy, ≤ -0.35 for sell)
6. Raise Kronos threshold to |0.5%|, reduce weight to 0.10
7. Remove volume_confirm entirely
8. Implement trailing stop after 1.5% profit

### Phase 2 — Next week
9. Add VWAP deviation indicator
10. Add BB squeeze detection (lowers entry threshold)
11. Implement tiered TP (partial exits)
12. Add time-based exit (24h dead position)
13. Add signal reversal exit
14. Sector correlation filter

### Phase 3 — After 200+ trades
15. Tune weights based on realized P&L per indicator
16. Consider Kelly sizing with empirical win rates
17. Add adaptive thresholds (e.g., RSI zones shift in high-vol regimes)

---

## 9. Expected Behavior Changes

| Before | After |
|--------|-------|
| ~3-5 buy signals per cycle, 0 sells | ~1-2 strong signals, mix of buy/sell |
| Trading on single weak indicator | Requires ≥2 indicators agreeing at score ≥0.35 |
| Fixed 3% SL / 6% TP | Dynamic SL (1.5-4%), tiered TP |
| Accumulating positions, never selling | Signal reversal exits + time exits + sells from overbought RSI |
| Volume indicator dead weight | ATR-based sizing + regime filter actually contributes |

---

## 10. References

- Murphy, J. (1999). *Technical Analysis of Financial Markets*. NYIF. — MACD histogram leading properties
- Wilder, J.W. (1978). *New Concepts in Technical Trading Systems*. — RSI methodology
- Bollinger, J. (2001). *Bollinger on Bollinger Bands*. — Squeeze/expansion cycle
- Mandelbrot, B. (2004). *The (Mis)Behavior of Markets*. — Volatility clustering, fractal markets
- Chan, E. (2013). *Algorithmic Trading*. — Position sizing, Kelly criterion limitations
- Cartea, Á., Jaimungal, S. (2013). *Algorithmic and High-Frequency Trading*. — Market microstructure, optimal execution
- Volume z-score: Bessembinder & Seguin (1993), "Price Volatility, Trading Volume, and Market Depth"