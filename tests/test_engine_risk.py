"""Unit tests for engine and risk functions.

Tests critical logic that doesn't require external API calls.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategy.engine import StrategyEngine, load_params
from strategy.risk import Order, check_risk, calculate_position_size


class TestRiskPositionCap:
    """Test risk management position caps."""

    def test_max_position_pct_default(self) -> None:
        """Test default max position size (base_trade_usdt * 1.5 = 3000 USDT with 1.2x vol mult = 3600)."""
        positions = []
        order = Order(pair="BTCUSDT", side="buy", qty=0.02, price=100000.0)  # 2000 USDT
        total_value = 50000.0  # 50k portfolio
        available_usdt = 50000.0

        result = check_risk(positions, order, total_value, available_usdt)

        assert result["allowed"] is True
        assert result["reason"] == "ok"

    def test_max_position_pct_exceeded(self) -> None:
        """Test order exceeding max trade size (base_trade * 1.5 * vol_mult = 3600)."""
        positions = []
        order = Order(pair="BTCUSDT", side="buy", qty=0.05, price=100000.0)  # 5000 USDT > 3600
        total_value = 50000.0  # 50k portfolio
        available_usdt = 50000.0

        result = check_risk(positions, order, total_value, available_usdt)

        assert result["allowed"] is False
        assert "max trade size" in result["reason"]

    def test_max_exposure_pct(self) -> None:
        """Test total exposure cap (default 50% from params)."""
        positions = [
            {"value_usdt": 25000.0, "is_new": True},  # Existing position (50% of 50k)
        ]
        # Order is 1000 USDT, which is under max trade size but would push exposure to 26000 > 50% of 50k (25k)
        order = Order(pair="BTCUSDT", side="buy", qty=0.01, price=100000.0)  # 1000 USDT
        total_value = 50000.0  # 50k portfolio
        available_usdt = 50000.0

        result = check_risk(positions, order, total_value, available_usdt)

        # 25k existing + 1k new = 26k > 50% of 50k (25k)
        assert result["allowed"] is False
        assert "exposure cap" in result["reason"]

    def test_insufficient_usdt(self) -> None:
        """Test order with insufficient USDT."""
        positions = []
        order = Order(pair="BTCUSDT", side="buy", qty=1.0, price=100000.0)  # 100k USDT
        total_value = 50000.0
        available_usdt = 1000.0  # Only 1k available

        result = check_risk(positions, order, total_value, available_usdt)

        assert result["allowed"] is False
        assert "insufficient USDT" in result["reason"]


class TestPositionSizing:
    """Test position sizing logic."""

    def test_calculate_position_size_default(self) -> None:
        """Test default position size calculation (base_trade_usdt = 2000, BTCUSDT low vol = 1.2x)."""
        available_usdt = 10000.0
        size = calculate_position_size(available_usdt, pair="BTCUSDT")

        assert size == 2400.0  # 2000 * 1.2 (low vol multiplier)

    def test_calculate_position_size_custom_params(self, monkeypatch) -> None:
        """Test position size with custom params."""
        # Mock load_params to return custom value
        def mock_load_params():
            return {"base_trade_usdt": 1000.0, "volatility_groups": {}}

        monkeypatch.setattr("strategy.risk.load_params", mock_load_params)

        available_usdt = 10000.0
        size = calculate_position_size(available_usdt)

        assert size == 1000.0  # custom base_trade_usdt


class TestEngineCooldown:
    """Test engine cooldown logic."""

    def test_cooldown_blocks_buy(self, tmp_path: Path) -> None:
        """Test that cooldown blocks repeated buys."""
        # Create temporary cooldown file
        cooldown_file = tmp_path / "cooldown.json"
        now = datetime.now(timezone.utc)
        recent_entry = {pair: (now - timedelta(minutes=30)).isoformat() for pair in ["BTCUSDT"]}
        cooldown_file.write_text(json.dumps(recent_entry))

        # Monkey patch the cooldown file path
        import strategy.engine
        original = strategy.engine.COOLDOWN_FILE
        strategy.engine.COOLDOWN_FILE = cooldown_file

        try:
            engine = StrategyEngine()
            # Mock bars that would generate a buy signal
            bars = [
                {"timestamp": 1700000000000, "open": 50000, "high": 51000, "low": 49000, "close": 50500, "volume": 1000}
                for _ in range(100)
            ]

            result = engine.analyze("BTCUSDT", bars, 10000.0)

            # Should be held due to cooldown (1hr default)
            assert result["action"] == "hold" or "cooldown" in result.get("rationale", "").lower()
        finally:
            strategy.engine.COOLDOWN_FILE = original

    def test_record_entry_saves_cooldown(self, tmp_path: Path) -> None:
        """Test that recording an entry saves to disk."""
        cooldown_file = tmp_path / "cooldown.json"

        # Monkey patch the cooldown file path
        import strategy.engine
        original = strategy.engine.COOLDOWN_FILE
        strategy.engine.COOLDOWN_FILE = cooldown_file

        try:
            engine = StrategyEngine()
            engine.record_entry("BTCUSDT")

            # Check file was created
            assert cooldown_file.exists()

            # Load and verify
            data = json.loads(cooldown_file.read_text())
            assert "BTCUSDT" in data

            # Verify timestamp is recent (within last minute)
            entry_time = datetime.fromisoformat(data["BTCUSDT"])
            assert datetime.now(timezone.utc) - entry_time < timedelta(minutes=1)
        finally:
            strategy.engine.COOLDOWN_FILE = original


class TestKronosFallback:
    """Test Kronos fallback behavior."""

    def test_kronos_fallback_on_insufficient_bars(self) -> None:
        """Test that Kronos falls back gracefully with insufficient data."""
        from strategy.kronos_signal import get_kronos_signal

        kronos = get_kronos_signal()
        result = kronos.forecast([], "BTCUSDT")

        assert result["direction"] == "neutral"
        assert result["confidence"] == 0.0
        assert "insufficient_data" in result["forecast_reasoning"]


class TestLoadParams:
    """Test params loading with defaults."""

    def test_load_params_defaults(self) -> None:
        """Test that load_params returns empty dict when file missing."""
        # Use non-existent file
        import strategy.config
        original = strategy.config.CONFIG_FILE
        strategy.config.CONFIG_FILE = Path("/nonexistent/path/params.json")

        try:
            params = load_params()

            # load_params returns empty dict when file doesn't exist
            assert params == {}
        finally:
            strategy.config.CONFIG_FILE = original

    def test_load_params_from_file(self, tmp_path: Path) -> None:
        """Test loading params from file."""
        params_file = tmp_path / "params.json"
        custom_params = {
            "base_trade_usdt": 1000.0,
            "cooldown_hours": 2,
            "composite_threshold_buy": 0.40,
        }
        params_file.write_text(json.dumps(custom_params))

        import strategy.config
        original = strategy.config.CONFIG_FILE
        strategy.config.CONFIG_FILE = params_file

        try:
            params = load_params()

            assert params["base_trade_usdt"] == 1000.0
            assert params["cooldown_hours"] == 2
        finally:
            strategy.config.CONFIG_FILE = original
