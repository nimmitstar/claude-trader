# Bybit Claude Trader

Kronos ensemble trading system for crypto on Bybit Testnet (uses real mainnet prices).

## Setup

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your Bybit Testnet credentials from https://testnet.bybit.com/
```

## CLI

```bash
exchange account      # Show account info
exchange positions    # List open positions
exchange bars BTCUSDT --tf 1h --n 500
exchange price BTCUSDT
exchange buy BTCUSDT --qty 0.001
exchange sell BTCUSDT --qty 0.001
exchange cancel --all
```

## Phase Plan

1. ✅ Repo scaffold, CLI wrapper, smoke test
2. Kronos-small integration (24.7M params, CPU-friendly)
3. 200-window walk-forward eval (BTCUSDT, ETHUSDT, SOLUSDT, LTCUSDT, DOGEUSDT)
4. Ensemble optimization and live trading prep

## Development

```bash
ruff check .
ruff format .
mypy .
pytest
```
