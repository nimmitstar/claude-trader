# TradingView MCP + Claude Strategy Research

## Video 1: Miles Deutscher — Claude + TradingView MCP
**Key insight:** TradingView released MCP server that feeds live chart data directly to Claude. No more screenshots.
- Claude can draw support/resistance zones, analyze charts in real-time
- Create custom PineScript indicators via voice/text prompt
- Backtest strategies live in TradingView
- Compare altcoins for relative strength automatically
- **Critical:** Connected to OpenClaw for 24/7 remote control via Telegram
- Uses TradingView desktop app (not Chrome) + Chrome DevTools protocol

**For us:** TradingView MCP could replace our manual indicator calculations. Claude reads live charts directly. However, we're already doing indicator computation ourselves. The MCP is more useful for visual analysis + backtesting in TradingView.

## Video 2: Nate Herk — Autonomous Trading Bot with Claude Code Routines
**Architecture (key for us):**
- Claude Code routines as scheduler (pre-market, market open, midday, close, weekly review)
- Alpaca API for brokerage (paper trading)
- Perplexity API for research
- Memory architecture: agent wakes up stateless → reads files → does job → writes lessons back
- **Context budget:** ~200k tokens per routine, treat tokens like money
- Strategy files + trade log + research all in project folder

**Strategy approach:**
- NOT day trading — beat S&P long-term (fundamentals-driven)
- "Teach like a kid to ride a bike" — start simple, add layers
- Opus 4.6 beat S&P by 8% in 30 days with $10k
- Sub-agents for research (spin up team)
- Daily journal → agent learns from mistakes

**For us:** The routine architecture (pre-market research → execute → review) is better than our simple cron. We should add research phases. Memory architecture matches our brain files approach.

## Video 3: Samin — Claude + Alpaca Copy Trading + Wheel Strategy
**Level 1:** Paper trading setup with Alpaca
**Level 2:** Copy trading — track Wall Street whales + politicians, auto-copy their moves
**Level 3:** Options — wheel strategy (sell covered calls + cash-secured puts)
- Uses trailing stop strategy as base: set floor, drag it up as price rises
- "Never hand AI a pile of money and say go figure it out" — encode YOUR rules
- Trailing stop = protect downside, lock in gains, move on fast when wrong

**For us:** Trailing stop implementation we already have. The key insight is encoding OUR strategy clearly, not letting the AI figure it out. Also: whale/politician tracking is a data source we don't use.

## Actionable Improvements for Our Trader

### High Priority
1. **Add research phase to cron cycle** — before trading, fetch market news/sentiment via web search. Feed into signal confidence.
2. **Daily journal/review routine** — after market close, agent reviews all trades, logs lessons, adjusts params
3. **Memory architecture like Nate's** — each cron wake reads strategy files + recent trade log + lessons learned
4. **TradingView MCP integration** — for visual backtesting and chart analysis (Phase 2)

### Medium Priority
5. **Whale/politician tracking** — use public filings data as additional signal
6. **Routine-based architecture** — replace simple 15m cron with scheduled routines (pre-market, open, midday, close)
7. **Sub-agent research** — spawn research agent to analyze news before each trade cycle

### Already Done
- Trailing stop ✅ (in strategy v3)
- Paper trading ✅ (demo testnet)
- Memory files ✅ (brain files, trade logs)
- ATR-based SL/TP ✅ (in strategy v3)
- Regime detection ✅ (in strategy v3)
