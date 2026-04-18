# Alpaca Claude Trader

Kronos ensemble trading system for crypto on Alpaca.

## Setup

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your Alpaca credentials
```

## CLI

```bash
alpaca account      # Show account info
alpaca positions    # List open positions
alpaca bars BTC/USD --tf 1Hour --n 500
alpaca quote BTC/USD
alpaca buy BTC/USD --qty 0.001
alpaca sell BTC/USD --qty 0.001
alpaca cancel --all
```

## Phase Plan

1. ✅ Repo scaffold, CLI wrapper, smoke test
2. Kronos-small integration (24.7M params, CPU-friendly)
3. 200-window walk-forward eval (BTC/USD, ETH/USD, SOL/USD, LTC/USD, DOGE/USD)
4. Ensemble optimization and live trading prep

## Development

```bash
ruff check .
ruff format .
mypy .
pytest
```
