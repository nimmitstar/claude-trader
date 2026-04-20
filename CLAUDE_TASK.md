## Task: Fix 3 bugs + build backtester

Think first. State assumptions, scope, exclusions. Then do.

### Bug Fixes (fix these first)

**Bug 1: Kronos scoring bias in strategy/engine.py**
Kronos confidence is 0-1 range, but MA/RSI/Volume scores are -1 to 1. Kronos is double-weighted.
Fix: Scale Kronos to [-1, 1] with `kronos_score = kronos_conf * 2 - 1`
Find the line where kronos score is used and fix it.

**Bug 2: Cooldown uses datetime.now() in strategy/engine.py**
Find cooldown logic, make it accept an optional timestamp parameter.
When backtesting, pass historical timestamp. When live, pass None (defaults to now).

**Bug 3: SL/TP never checked in trader/runner.py**
SL/TP orders are placed but never triggered. For the backtester, add SL/TP simulation:
- Track entry price per position
- On each new candle, check: did price hit SL (-3%) or TP (+6%)?
- If yes, close position at SL/TP price

### Backtester Build

After bugs fixed, build the backtester:

**File: backtester/backtest.py**

1. **Data downloader**: Fetch 6 months of 15min klines from Binance public API (no auth needed)
   - Endpoint: GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=1000
   - 1000 candles per request, paginate for full 6 months
   - Cache locally to data/ directory as JSON

2. **Walk-forward engine**:
   - Load cached candles
   - For each candle (from oldest to newest):
     a. Build indicator history from prior candles (need at least 30 for indicators to warm up)
     b. Call engine.analyze() with historical timestamp
     c. Skip Kronos signal (pass None or mock)
     d. If signal = BUY and no position: buy at candle close
     e. If signal = SELL or SL hit or TP hit: sell at candle close (or SL/TP price)
   - Track: entry price, exit price, fees, slippage, P&L per trade

3. **Simulation parameters**:
   - Starting capital: $50,000
   - Max position: 20% ($10,000)
   - Max total exposure: 55% ($27,500)
   - Fee: 0.1% per trade
   - Slippage: 0.05% per trade
   - SL: -3%, TP: +6%
   - Cooldown: 1 hour between signals per pair

4. **Walk-forward validation**:
   - Train window: first 4 months (optimize nothing, just run)
   - Test window: last 2 months
   - Report both separately

5. **Parameter stability**:
   - Run with default params, then with:
     - RSI 7->8, MA 5/15->5/14, threshold 0.1->0.15
   - If results change >30% -> flag as overfit

6. **Output report** (print to stdout, save to backtester/results/):
   ```
   === BACKTEST REPORT ===
   Period: 2025-10-20 to 2026-04-20
   Pairs: 13
   Timeframe: 15m
   
   SUMMARY
   Total trades: X
   Win rate: X% (95% CI: X-X%)
   Total P&L: $X (X%)
   Avg win: +X%, Avg loss: -X%
   Profit factor: X
   Max drawdown: -X%
   Sharpe ratio: X
   
   WALK-FORWARD
   Train (months 1-4): X trades, X% win, $X P&L
   Test (months 5-6): X trades, X% win, $X P&L
   
   PER PAIR
   BTCUSDT: X trades, X% win, $X
   ETHUSDT: ...
   
   PARAMETER STABILITY
   Default: X% win, $X P&L
   Perturbed: X% win, $X P&L
   Delta: X% -> OVERFIT FLAG / STABLE
   
   FLAGS
   [!] Win rate > 60%: possible overfitting
   [!] Sharpe < 1.0: not worth the risk
   [OK] Results stable across parameter perturbation
   ```

7. **Anti-overfit flags**:
   - Win rate > 60%: warn
   - Sharpe < 1.0: warn
   - Train/test P&L gap > 50%: warn
   - Parameter sensitivity > 30%: warn

### Files to create/modify:
- strategy/engine.py — fix bugs 1 and 2
- trader/runner.py — fix bug 3 (SL/TP check)
- backtester/__init__.py — new
- backtester/backtest.py — main backtester
- backtester/data/__init__.py — data cache dir

### Existing files to NOT modify:
- strategy/indicators.py — don't touch
- strategy/risk.py — don't touch
- strategy/opus.py — don't touch
- strategy/kronos_signal.py — don't touch
- trader/log.py — don't touch

### Execution:
1. Read existing code first
2. Fix bugs
3. Build backtester
4. Run a quick test on BTCUSDT only (1 month) to verify it works
5. Commit and push

Follow Karpathy protocol: Think -> Decide -> Do -> Verify
