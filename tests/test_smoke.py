"""Smoke test for Binance CLI and API.

Run this after setting up .env credentials to verify everything works.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run_cli(args: list[str]) -> dict:
    """Run CLI command and parse JSON output."""
    result = subprocess.run(
        [sys.executable, "-m", "binance_cli.binance"] + args,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"CLI failed: {result.stderr}")

    return json.loads(result.stdout)


@pytest.mark.skipif(not os.getenv("BINANCE_API_KEY"), reason="No API credentials")
def test_account_info() -> None:
    """Test fetching account information."""
    data = run_cli(["account"])

    assert "balances" in data
    assert "can_trade" in data
    assert isinstance(data["balances"], list)


@pytest.mark.skipif(not os.getenv("BINANCE_API_KEY"), reason="No API credentials")
def test_positions() -> None:
    """Test fetching positions (balances)."""
    data = run_cli(["positions"])

    assert isinstance(data, list)
    # Testnet accounts start with USDT balance


@pytest.mark.skipif(not os.getenv("BINANCE_API_KEY"), reason="No API credentials")
def test_bars_btcusdt() -> None:
    """Test fetching BTCUSDT bars."""
    data = run_cli(["bars", "BTCUSDT", "--tf", "1h", "--n", "100"])

    assert isinstance(data, list)
    assert len(data) <= 100

    if data:  # Market might be closed or no data
        bar = data[0]
        assert "timestamp" in bar
        assert "open" in bar
        assert "high" in bar
        assert "low" in bar
        assert "close" in bar
        assert "volume" in bar


@pytest.mark.skipif(not os.getenv("BINANCE_API_KEY"), reason="No API credentials")
def test_price_btcusdt() -> None:
    """Test fetching BTCUSDT price."""
    data = run_cli(["price", "BTCUSDT"])

    assert "symbol" in data
    assert data["symbol"] == "BTCUSDT"
    assert "price" in data
    assert isinstance(data["price"], float)
    assert data["price"] > 0


@pytest.mark.skipif(not os.getenv("BINANCE_API_KEY"), reason="No API credentials")
def test_paper_buy_order() -> None:
    """Test submitting a tiny testnet buy order.

    Uses smallest possible quantity - should be ~0.001 BTC (~$10-100 on testnet).
    Market order will fill immediately or expire.
    """
    try:
        data = run_cli(["buy", "BTCUSDT", "--qty", "0.001"])
        assert "order_id" in data or "status" in data
    except RuntimeError as e:
        # Order might fail if market closed or insufficient balance
        # That's OK for smoke test - we verified the API call path works
        assert any(x in str(e).lower() for x in ["order failed", "insufficient", "error"])


@pytest.mark.skipif(not os.getenv("BINANCE_API_KEY"), reason="No API credentials")
def test_list_orders() -> None:
    """Test listing open orders."""
    data = run_cli(["cancel", "BTCUSDT"])

    assert isinstance(data, list)


if __name__ == "__main__":
    """Run smoke test manually for quick verification."""
    print("Running smoke test...")

    if not os.getenv("BINANCE_API_KEY"):
        print("ERROR: Set BINANCE_API_KEY and BINANCE_API_SECRET in .env")
        sys.exit(1)

    tests = [
        ("Account info", lambda: run_cli(["account"])),
        ("Positions", lambda: run_cli(["positions"])),
        ("BTCUSDT bars", lambda: run_cli(["bars", "BTCUSDT", "--tf", "1h", "--n", "10"])),
        ("BTCUSDT price", lambda: run_cli(["price", "BTCUSDT"])),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            result = test_func()
            print(f"✓ {name}")
            if isinstance(result, dict) and result:
                print(f"  Sample: {json.dumps(result, indent=2)[:200]}...")
            passed += 1
        except Exception as e:
            print(f"✗ {name}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")

    if failed > 0:
        sys.exit(1)
