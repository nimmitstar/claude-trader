# Binance Claude Trader

Kronos ensemble trading system for crypto on Binance Testnet.

## Setup

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
# Edit .env with your Binance Testnet credentials
```

## CLI

```bash
binance account      # Show account info
binance positions    # List open positions
binance bars BTCUSDT --tf 1h --n 500
binance price BTCUSDT
binance buy BTCUSDT --qty 0.001
binance sell BTCUSDT --qty 0.001
binance cancel --all
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
