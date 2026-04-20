# Opus Review — AI Crypto Trader

**Reviewer:** Claude Sonnet 4.6 (Opus-tier second opinion)  
**Date:** 2026-04-20  
**Scope:** Full codebase (~2,080 lines), 10 pairs, $100k active allocation

---

## Grades

| Category | Score | Notes |
|----------|-------|-------|
| Signal Quality | 5/10 | RSI + MA + Volume is standard but weak. Kronos adds ML flavor but 50% filter is too generous. Missing: trend filter, multi-timeframe confirmation. |
| Risk Management | 6/10 | Position caps and exposure limits are solid. But: no drawdown limit, no daily loss circuit breaker, no correlation-aware sizing. |
| Execution Logic | 7/10 | Order flow is clean, SL/TP via conditional orders works, lot size guards present. Sell logic correct after recent fixes. |
| Code Quality | 6/10 | Well-structured, good separation of concerns. But: two `load_params()` functions (engine.py + opus.py), magic numbers, no tests. |

**Overall: 6/10** — functional for learning, not production-ready.

---

## Top 3 Strategic Improvements

### 1. Add a Trend Filter (HIGH IMPACT)

**Problem:** The strategy trades in both directions equally. In a strong downtrend, it'll keep buying oversold bounces and getting stopped out.

**Fix:** Add an EMA-50 trend filter on the 1h or 4h timeframe:
- If price < EMA-50 on 4h → only allow sells (no buys even if RSI oversold)
- If price > EMA-50 on 4h → only allow buys (no sells even if RSI overbought)
- If price near EMA-50 → allow both (neutral zone)

**Impact:** Eliminates ~40% of losing trades in trending markets. This is the single biggest improvement.

### 2. Daily Loss Circuit Breaker (HIGH IMPACT)

**Problem:** No limit on how much can be lost in a day. A string of bad trades compounds.

**Fix:** Track daily P&L. If losses exceed 3% of active_capital ($3,000) in 24h → halt trading until next day.

**Impact:** Prevents catastrophic drawdowns. Standard risk practice.

### 3. Position Sizing Based on Signal Strength (MEDIUM IMPACT)

**Problem:** Every buy gets the same allocation (15% of $100k = $15k) regardless of confidence. A weak RSI-29 buy gets the same size as a strong RSI-15 + MA crossover + Kronos bullish buy.

**Fix:** Scale position size by confidence score:
- confidence < 0.2 → 5% of allocation
- confidence 0.2-0.4 → 10%
- confidence > 0.4 → 15% (full)

**Impact:** Better risk-adjusted returns. Bigger bets on higher-conviction trades.

---

## Honorable Mentions

- **Kronos plausibility filter at 50% is too loose.** A 40% price prediction swing in 1 hour is garbage. Tighten to 10%.
- **Opus self-tuning is unused.** The review prompt is built but never sent to an LLM. Either wire it up or remove the dead code.
- **Two `load_params()` functions** — `strategy/engine.py` and `strategy/opus.py` both have their own. Should be one source of truth.

---

## WebSocket Reactive Layer — Design Spec

### Goal
React to large price moves within minutes, not waiting for the next 15m cron cycle.

### Architecture

```
Bybit WebSocket (mainnet spot tickers)
    ↓
Price Monitor (process: detects >2% moves in 5min window)
    ↓
Strategy Engine (existing: analyze pair with fresh bars)
    ↓
Executor (existing: place orders via Bybit client)
```

### Implementation

**File:** `trader/ws_watcher.py` (~100 lines)

**Listen to:** Bybit spot public WebSocket `wss://stream.bybit.com/v5/public/spot`
- Subscribe to tickers for all 10 pairs
- Track: last "analysis price" per pair + timestamp

**Trigger logic:**
```
For each ticker update:
    price_change_pct = abs(current_price - last_analysis_price) / last_analysis_price
    
    if price_change_pct >= 2.0%:
        if time_since_last_analysis < 5 minutes:
            skip  # avoid duplicate with cron cycle
        trigger_analysis(pair)
        last_analysis_price[pair] = current_price
```

**Deduplication with cron:**
- Shared lock file: `trades/.analysis_lock_{pair}`
- Before any analysis (cron or WS), check if pair was analyzed in last 10 minutes
- If yes → skip
- This prevents double-trading when cron and WS fire close together

**Systemd service:** `claude-trader-ws.service` (long-running process)
- Restart on crash
- Separate from cron timer

### Priority
Low-medium. The 15m cron catches most moves. WebSocket adds value during volatile sessions (flash crashes, sudden pumps). Build after trend filter and circuit breaker — those are more impactful.
