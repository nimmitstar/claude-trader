"""Smoke test for Alpaca CLI and API.

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
        [sys.executable, "-m", "alpaca_cli.alpaca"] + args,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"CLI failed: {result.stderr}")

    return json.loads(result.stdout)


@pytest.mark.skipif(not os.getenv("ALPACA_API_KEY"), reason="No API credentials")
def test_account_info() -> None:
    """Test fetching account information."""
    data = run_cli(["account"])

    assert "id" in data
    assert "buying_power" in data
    assert isinstance(data["buying_power"], float)
    assert data["buying_power"] >= 0


@pytest.mark.skipif(not os.getenv("ALPACA_API_KEY"), reason="No API credentials")
def test_positions() -> None:
    """Test fetching positions (may be empty)."""
    data = run_cli(["positions"])

    assert isinstance(data, list)


@pytest.mark.skipif(not os.getenv("ALPACA_API_KEY"), reason="No API credentials")
def test_bars_btc_usd() -> None:
    """Test fetching BTC/USD bars."""
    data = run_cli(["bars", "BTC/USD", "--tf", "1Hour", "--n", "100"])

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


@pytest.mark.skipif(not os.getenv("ALPACA_API_KEY"), reason="No API credentials")
def test_quote_btc_usd() -> None:
    """Test fetching BTC/USD quote."""
    data = run_cli(["quote", "BTC/USD"])

    assert "symbol" in data
    assert data["symbol"] == "BTC/USD"
    # Bid/ask might be None if market closed


@pytest.mark.skipif(not os.getenv("ALPACA_API_KEY"), reason="No API credentials")
def test_paper_buy_order() -> None:
    """Test submitting a tiny paper buy order.

    Uses smallest possible quantity - should be ~0.00001 BTC (~$0.50).
    Market order will fill immediately or expire.
    """
    try:
        data = run_cli(["buy", "BTC/USD", "--qty", "0.00001"])
        assert "id" in data or "status" in data
    except RuntimeError as e:
        # Order might fail if market closed or insufficient balance
        # That's OK for smoke test - we verified the API call path works
        assert "Order failed" in str(e) or "buying_power" in str(e).lower()


@pytest.mark.skipif(not os.getenv("ALPACA_API_KEY"), reason="No API credentials")
def test_cancel_all_orders() -> None:
    """Test canceling all orders."""
    data = run_cli(["cancel", "--all"])

    assert "canceled_count" in data
    assert isinstance(data["canceled_count"], int)


if __name__ == "__main__":
    """Run smoke test manually for quick verification."""
    print("Running smoke test...")

    if not os.getenv("ALPACA_API_KEY"):
        print("ERROR: Set ALPACA_API_KEY and ALPACA_API_SECRET in .env")
        sys.exit(1)

    tests = [
        ("Account info", lambda: run_cli(["account"])),
        ("Positions", lambda: run_cli(["positions"])),
        ("BTC/USD bars", lambda: run_cli(["bars", "BTC/USD", "--tf", "1Hour", "--n", "10"])),
        ("BTC/USD quote", lambda: run_cli(["quote", "BTC/USD"])),
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
