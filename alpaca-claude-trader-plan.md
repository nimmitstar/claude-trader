# Alpaca Paper Trading — Claude + Kronos Trader — Step-by-Step Plan

**What you asked for:** Claude trades crypto on Alpaca paper. Use [Kronos](https://github.com/shiyu-coder/Kronos) (foundation model for OHLCV forecasting) as the numerical engine. No hand-written strategy.

**What we're actually building:** an agentic trading bot with a **two-stage brain** — Kronos produces price forecasts, Claude reasons over those forecasts + account state + risk rules and decides trades. A scheduler triggers the whole thing. Still a bot; now with a much cleaner division of labor than before.

---

## 0. Why Kronos is a real upgrade (and where I want to gate-check it)

The prior plan had Claude staring at 100 raw OHLCV rows and deciding. That's asking one model to do two jobs: quantitative pattern recognition *and* risk-aware judgment. LLMs are mediocre at the first and good at the second.

**Kronos (deterministic, numerical) → Claude (judgmental, contextual)** is a better design:

| Task | Best handled by |
|---|---|
| "Given 500 hourly bars, project the next 24h distribution" | Kronos |
| "Given this forecast distribution + my open position + overnight news + my risk limits, is this trade worth taking?" | Claude |
| "Is this trade allowed by guardrails?" | Deterministic code |

**The honest concern.** The Kronos repo publishes no accuracy benchmarks. 19k GitHub stars ≠ forecasting edge on BTC hourlies. Before we wire it into the loop we measure it — if Kronos beats trivial baselines (last-price persistence, 24h momentum, random walk) on out-of-sample Alpaca crypto data, keep it. If it ties or loses, we drop it into an optional lane, not the critical path. No model earns the car keys by vibes.

---

## 1. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  SCHEDULER  (scheduled-tasks MCP — every 60 min)             │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  RUNNER (Python)                                             │
│   1. Pull account/positions/orders/fills (Alpaca)            │
│   2. Pull last 500 × 1h bars for each watchlist pair         │
│   3. ── KRONOS ── predict next 24h (N sampled paths)         │
│        ▸ derive: median path, 10th/90th percentile,          │
│          expected 6h/12h/24h return, forecast "confidence"   │
│   4. (Optional) pull news/sentiment (LunarCrush or skip v1)  │
│   5. Check guardrails (kill switch, daily drawdown, caps)    │
│   6. Build Claude prompt: account state + forecast summary   │
│      + guardrails + last 24h journal                         │
│   7. Claude returns JSON: list of orders + rationale each    │
│   8. Validate orders against guardrails (reject violators)   │
│   9. Place approved orders via Alpaca                        │
│  10. Persist to SQLite: decision, forecast, orders, fills    │
│  11. Alert on Telegram for: trade opened, trade closed,      │
│      guardrail rejection, any error                          │
└──────────────────────────┬───────────────────────────────────┘
                           ▼
                ┌─────────────────────────────┐
                │  ALPACA PAPER (~$100k fake) │
                └─────────────────────────────┘

Side processes:
  • Daily digest — reads journal, posts recap
  • Kronos eval job — weekly, measures forecast accuracy on held-out data
```

---

## 2. Phase plan (in order)

### Phase 1 — Alpaca paper account (30 min)
Create account → enable Paper Trading + Crypto → generate paper API key → store in `.env`. These keys cannot touch real money. $100k starting fake balance.

### Phase 2 — Alpaca access layer (2–4 hours)
Build a CLI wrapper (`alpaca.py`) around `alpaca-py`. Subcommands: `account`, `positions`, `bars`, `quote`, `buy`, `sell`, `cancel`. Claude shells out to this. Port to a proper MCP later if useful. Smoke test: read account, pull BTC/USD bars, submit a tiny test order.

### Phase 3 — **Kronos evaluation sprint** (1–2 days) ← critical gate
Before wiring Kronos into the trading loop, prove it works on *our* data.

1. `pip install` Kronos requirements, download `Kronos-small` (24.7M, fits on CPU) from HuggingFace.
2. Pull **6 months** of 1h bars for BTC/USD, ETH/USD, SOL/USD, LTC/USD, DOGE/USD from Alpaca.
3. **Walk-forward evaluation** — for each of, say, 200 random timestamps in the last month:
   - Feed Kronos the prior 500 bars
   - Get its 24h forecast (median of 20 sampled paths)
   - Compare to actual
4. Metrics per pair:
   - **Directional accuracy** (did Kronos correctly predict up/down at 6h, 12h, 24h horizons?)
   - **MAE / MAPE** on the close price path
   - **Beats last-price persistence?** (i.e., "tomorrow = today")
   - **Beats 24h momentum?** (i.e., "next 24h returns = last 24h returns")
5. Decision tree:
   - Kronos beats both baselines on directional accuracy by ≥ 3 percentage points → **keep, wire in**
   - Kronos ties baselines → **keep as optional signal, not primary driver**
   - Kronos loses → **drop entirely**; Claude reads OHLCV directly like the prior plan, we can revisit Kronos-base or Kronos-mini later

Deliverable: a short report (`kronos-eval.md`) with a table of results. This becomes the evidence we need to justify Kronos's role — or retire it.

### Phase 4 — Runner skeleton (1 day)
- `runner.py` implementing steps 1–11 above, **starting in print-only mode** (no order placement).
- SQLite `journal.db`: tables `decisions`, `forecasts`, `orders`, `fills`, `guardrail_events`.
- Run manually 5–10 times, read the JSON output, spot-check the rationales.
- **Output contract for Claude's decision:**
  ```json
  {
    "market_read": "one-paragraph assessment across watchlist",
    "orders": [
      {
        "symbol": "BTC/USD",
        "side": "buy",
        "qty_usd": 2500,
        "order_type": "limit",
        "limit_price": 64850,
        "stop_loss_pct": 2.0,
        "take_profit_pct": 4.0,
        "rationale": "Kronos 24h median +3.1% with narrow path spread; no current BTC position; 2.5k is ~2.5% of equity; R/R 2:1; no news flagged."
      }
    ],
    "hold_reasons": ["ETH forecast is mixed (high path variance)", "DOGE below min liquidity threshold"]
  }
  ```
  Every order MUST have a rationale that references the forecast. "Gut feel" is not acceptable — the prompt will reject that explicitly.

### Phase 5 — Guardrails (half day, before enabling live orders)
Enforced in runner code, NOT in prompt:
- Max position size per asset (e.g., 5% of equity)
- Max total crypto exposure (e.g., 40% of equity)
- Max orders per run (e.g., 3)
- Max orders per day (e.g., 20)
- Cooldown after a loss (30 min)
- Daily drawdown circuit breaker (>3% intraday = stop for 24h)
- Kill switch file (`/tmp/KILL` → runner exits immediately)
- Symbol whitelist (rejects orders for unknown tickers — prevents Claude hallucinations)
- Position-consistent sell (cannot sell more than current position)

Test each guardrail with a unit test before Phase 6.

### Phase 6 — Enable paper order placement (instant flip)
Change one config line: `DRY_RUN=true` → `false`. First day: watch every run live.

### Phase 7 — Scheduler + observability (half day)
- Scheduled task every 60 min invokes runner
- Heartbeat alert if no run in 2h
- Daily digest (Telegram or email): trades, P&L, forecast vs actual from yesterday, guardrail events

### Phase 8 — Weekly review (you, Sundays, 30 min)
- Read all rationales from closed trades
- Compare Kronos forecasts to actuals (continuously validating Phase 3's assumption)
- Tune the prompt or rules
- Commit all changes with notes

### Phase 9 — Go/no-go review after 4 weeks
Required before we even *discuss* real money:
- ≥ 100 completed trades
- Sharpe > 1 after fees
- Max drawdown < 10%
- Forecasts still beating baselines
- Rationales read like a thoughtful trader, not a pattern-match

Miss any of these → stay on paper. Fail repeatedly → retire the approach.

---

## 3. Decision prompt design

The prompt is the highest-leverage thing we write. First version:

```
You are a disciplined crypto trader operating a paper account. You prioritize
capital preservation over home runs. You only trade when the forecast and
risk/reward are both favorable; otherwise you hold.

=== ACCOUNT ===
Equity: $102,340
Buying power: $58,220
Open positions:
  BTC/USD: 0.062 @ avg 63,400 | current 64,710 | unrealized +$81 (+2.07%)
  ETH/USD: 1.45 @ avg 3,220  | current 3,185  | unrealized -$51 (-1.09%)

=== KRONOS FORECAST (24h horizon, 20 sampled paths) ===
BTC/USD  current $64,710
  Median 6h:  +0.3%   |  12h: +1.1%   |  24h: +2.8%
  10th pct:  -1.4%,  -2.1%,  -2.9%
  90th pct:  +2.0%,  +3.8%,  +6.2%
  Path variance: LOW (forecasts tightly clustered)

ETH/USD  current $3,185
  Median 6h:  -0.1%   |  12h: -0.4%   |  24h: -0.2%
  10th pct:  -3.0%,  -4.2%,  -5.1%
  90th pct:  +2.5%,  +3.1%,  +4.3%
  Path variance: HIGH (forecasts diverge wildly — low confidence)

[... rest of watchlist ...]

=== RECENT TRADES (last 24h) ===
2026-04-17 09:00  BUY  BTC 0.02  @ 63,500  rationale: "..."  → OPEN (+$24)
2026-04-17 14:00  SELL ETH 0.5   @ 3,240   rationale: "..."  → CLOSED (-$14)

=== GUARDRAILS (hard limits, runner enforces) ===
Max position per asset: 5% of equity = $5,117
Max orders this run: 3 remaining
Max orders today: 17 remaining
Daily P&L: +$24 (circuit breaker triggers at -$3,070)

=== TASK ===
Decide what orders to place, if any. For each order, rationale MUST reference
the forecast. Hold is a valid answer. Output strict JSON matching the schema.
High path variance = LOW CONFIDENCE — require stronger signal to trade.
```

---

## 4. Cost math (updated with Kronos)

Kronos is local compute, no token cost. Claude's input shrinks (summary stats instead of raw OHLCV), so per-run tokens drop vs. the prior plan.

| Schedule | Runs/day | Claude $/day | Kronos compute | Monthly |
|---|---|---|---|---|
| Hourly (recommended) | 24 | ~$1.50–3 | ~1min/run on CPU | **~$45–90** |
| 15-min | 96 | ~$6–12 | ~1min/run on CPU | ~$180–360 |
| 5-min | 288 | ~$18–36 | GPU recommended | $540–1080 + GPU |

Kronos-small on CPU: ~30–60s per watchlist inference pass. Kronos-base: add a small GPU ($5–10/mo VPS with GPU, or run locally on a modern Mac).

---

## 5. Risks (what's different with Kronos in the loop)

| Failure | Likelihood | Mitigation |
|---|---|---|
| Kronos has no real edge on crypto (just interpolates noise) | **Medium-High** | Phase 3 eval sprint; weekly re-validation; baselines as fallback |
| Claude over-trusts the forecast on a high-variance prediction | Medium | Include path variance in prompt; explicit "low confidence → hold" rule |
| Forecast goes stale (crypto regime changes) | Medium | Weekly re-eval; auto-disable Kronos if rolling accuracy drops below baseline |
| Kronos and Claude drift out of sync (prompt shows old forecast) | Low | Timestamp every forecast, validate freshness in prompt |
| CPU inference is too slow for hourly runs | Low-Med | Use Kronos-small; skip pairs below liquidity threshold; upgrade to GPU if needed |
| Multi-path sampling makes the forecast non-deterministic across runs | Expected | Fix random seed; or accept as feature (ensemble smoothing) |
| We forget to re-evaluate and Kronos rots silently | **High over time** | Scheduled weekly eval with alert if accuracy degrades |
| Claude hallucinates a ticker or sells more than held | Medium | Runner validates symbols + position sizes; reject + log |
| Prompt contract breaks (Claude returns unparseable JSON) | High early | JSON schema validation; fall back to "hold" on parse error |

The first row is the big one. If Kronos doesn't actually forecast better than "today's price = tomorrow's price," then the whole system's an expensive random number generator. The Phase 3 gate exists to prevent us deploying into that situation unknowingly.

---

## 6. What the repo will look like

```
alpaca-trader/
├── .env                          # Alpaca paper keys (gitignored)
├── .env.example
├── .gitignore
├── README.md
├── pyproject.toml
├── alpaca_cli/
│   └── alpaca.py                 # CLI wrapper Claude calls
├── kronos_service/
│   ├── forecast.py               # wrapper around KronosPredictor
│   └── cache.py                  # cache forecasts to avoid redundant inference
├── kronos_eval/
│   ├── evaluate.py               # Phase 3 walk-forward eval
│   └── kronos-eval.md            # results report
├── runner/
│   ├── runner.py                 # the main loop body
│   ├── guardrails.py
│   ├── prompt.py                 # prompt builder
│   └── schemas.py                # Pydantic models for Claude's JSON output
├── journal/
│   ├── db.py                     # SQLite ORM
│   └── journal.db                # (gitignored)
├── ops/
│   ├── daily_digest.py
│   └── kill                      # touch this to stop the bot
└── tests/
    └── test_guardrails.py        # unit tests for every guardrail
```

---

## 7. What I will NOT do

- Trade real money until ≥ 4 weeks of paper with the Phase 9 metrics met
- Pick coins for you in the plan (Claude picks at runtime; you pick the watchlist)
- Store your API keys in memory
- Skip the Kronos evaluation sprint because the repo "looks cool"
- Give financial advice

---

## Next actions — I need four decisions

1. **Kronos variant:** `Kronos-small` (24.7M, CPU-friendly, my recommendation) or `Kronos-base` (102.3M, more capable, probably wants GPU)?
2. **Phase 3 eval depth:** full walk-forward with 200 windows (1–2 days, thorough) or a quick 50-window pilot first (2–3 hours, gets us to a go/no-go faster)?
3. **Watchlist:** BTC/USD, ETH/USD, SOL/USD, LTC/USD, DOGE/USD — confirm or change?
4. **Start building now?** If yes, I'll scaffold the repo, write the Alpaca CLI wrapper, pull 6mo of bars for the eval, and we'll have Phase 3 results to look at in this session.

My strong recommendations: **Kronos-small, quick pilot first, the watchlist above, yes start now.** Tell me if that works and I'll go.