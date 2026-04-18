You are picking up a new project mid-stream. Read this entire message before doing anything else. Do not skim.

## Project summary

Build an agentic crypto trading bot that runs on **Alpaca paper** (never live real money during this project). Architecture:

- **Kronos** (open-source foundation model on HuggingFace under `NeoQuasar/`) produces numerical 24h OHLCV forecasts
- **Claude** reasons over the forecast + account state + guardrails and decides paper orders
- A **scheduler** triggers runs on a cadence (target: hourly to start)
- Everything is logged to SQLite; a daily digest goes to the user via Telegram or email

The user is treating this as a research project. No real money will be traded until ≥ 4 weeks of paper operation meeting specific P&L / Sharpe / max-drawdown criteria (see plan §2 Phase 9).

## Required reading (do this before anything else)

The full, up-to-date plan lives at `./alpaca-claude-trader-plan.md` in this directory. Read it end-to-end. It defines:

- Two-stage brain (Kronos + Claude) architecture with a diagram
- 9 phases with concrete scope per phase
- Hard guardrails (enforced in code, not prompts)
- The **Kronos evaluation sprint** (Phase 3) — a mandatory gate before Kronos forecasts can drive any trade
- The decision-prompt JSON output contract
- Risks and failure modes with mitigations

Do not write any code until you have read the plan. If the plan file is missing, stop and tell the user you need it before proceeding.

## User profile

- Intermediate Python, some manual crypto trading experience
- Wants Claude to "free-trade 24/7" on Alpaca paper — confirmed agentic-bot architecture, not human-in-the-loop
- Risk-tolerant in general but scope here is paper only
- Believed "stock and crypto are the same" — has been corrected for this project; do not re-open that
- Project instruction: follow the **Think-Then-Do protocol** (ref: https://github.com/forrestchang/andrej-karpathy-skills)

## Think-Then-Do protocol (non-optional)

For every non-trivial step:

1. **Think** — write out, in prose, what you're about to do and why. Call out what could go wrong and what assumptions you're making.
2. **Do** — make the change, run the command, create the file.
3. **Verify** — small test, read-back, or smoke check that the change actually did what you intended.

Do not skip the thinking phase for anything touching orders, secrets, schedulers, or deployment.

## Hard constraints (non-negotiable)

- **Never place a live-money order.** Paper endpoint only, for the entire duration.
- **Never write Alpaca API keys to memory, CLAUDE.md, committed files, or chat.** The user types them into `.env` directly; you do not need to see them.
- `.env`, `*.pem`, `ed25519*`, `journal.db`, `logs/` → must be in `.gitignore` *before the first commit*. If that's not true yet, fix it before any `git add`.
- Every guardrail in the plan is enforced in **runner code**, not in Claude's prompt. Prompt rules can be disregarded by a future Claude run; code rules cannot.
- At runtime, every proposed order must carry a rationale that **references the Kronos forecast**. Proposals without one are rejected by the runner.
- No order exceeds the per-asset cap (default 5% of equity) no matter what Claude says at runtime.
- **Kronos must pass the Phase 3 eval gate before its forecasts drive any trade.** If it loses to trivial baselines (last-price persistence, 24h momentum), drop it from the critical path and report honestly.

## Outstanding decisions (ask the user first, don't assume)

Four things must be decided before scaffolding. Ask them plainly:

1. **Kronos variant** — `Kronos-small` (24.7M params, CPU is fine) or `Kronos-base` (102.3M, probably wants a GPU)?
2. **Eval depth** — full 200-window walk-forward (1–2 days, thorough) or a 50-window pilot first (~2–3 hours, faster go/no-go)?
3. **Watchlist** — BTC/USD, ETH/USD, SOL/USD, LTC/USD, DOGE/USD? Confirm or change.
4. **Local OS** + **GitHub preference** — are we pushing to a private GitHub repo or keeping it local for now?

Recommend if asked: **Kronos-small, 50-window pilot, the watchlist above, local-first with optional GitHub push later.**

## First-session goal

Once the four decisions land, do this and only this, in order:

1. Scaffold repo per plan §6. Python 3.11+, `uv` for env, `ruff` for lint/format, `mypy` for types.
2. Write `.gitignore`, `.env.example`, `pyproject.toml`, `README.md` stub. Initialize git. **Do not commit yet**; confirm `.gitignore` covers secrets first.
3. Build `alpaca_cli/alpaca.py`: wrap `alpaca-py` with subcommands `account`, `positions`, `bars`, `quote`, `buy`, `sell`, `cancel`. Every subcommand prints JSON to stdout.
4. Write a smoke test: fetch account, fetch BTC/USD 1h bars (last 500), submit a paper test order at very small qty. Verify every call works.
5. Make the first git commit. Message convention: imperative, specific, referenced to plan phase (e.g., `phase 2: alpaca cli wrapper + smoke test`).
6. Stop. Show the user the smoke-test output. Wait for go-ahead before Phase 3.

That is one session's worth of scope. Do not try to do more in one pass.

## Later sessions (per plan)

- Session 2: Kronos wrapper + Phase 3 eval sprint. Produce `kronos_eval/kronos-eval.md` with the accuracy table. Wait for user verdict on go/no-go.
- Session 3: runner v0 in print-mode, prompt builder, Pydantic output schema for Claude's JSON.
- Session 4: guardrails module + unit tests for every guardrail. No order placement yet.
- Session 5: flip `DRY_RUN` off, set up scheduler, build daily digest, heartbeat alerts.
- Session 6+: weekly reviews, prompt tuning, approach Phase 9 gate.

Default to one session's scope per run. Ask the user which scope they want if in doubt.

## Coding conventions

- Python 3.11+; `uv` preferred for env and dependency management
- `ruff check` + `ruff format` on every change
- `mypy --strict` on `alpaca_cli`, `kronos_service`, `runner`, `journal`
- **Pydantic** models at every system boundary: Claude's decision JSON, Alpaca responses, Kronos forecast structs, config
- **SQLite** with a thin hand-rolled repository layer (no ORM)
- Structured JSON-line logs to `logs/`, rotated daily
- Every external call has a timeout and retries with backoff
- Every guardrail has a unit test; they land before the guardrail is trusted in production
- Commit messages: imperative mood, reference plan phase where relevant

## What to deliver at the end of each session

- Short summary of what was done (diff, tests passing, smoke test output)
- Explicit list of what's NOT done and is deferred to the next session
- Any divergence from the plan and why, so the plan can be updated
- Any newly surfaced risk or unknown

## Begin

Start by reading `./alpaca-claude-trader-plan.md`. Then ask the four outstanding decisions above. Wait for answers. Do not scaffold, install, or write code until all four are answered.
