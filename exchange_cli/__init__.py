"""Exchange CLI adapters — unified interface for multiple exchanges.

Provides Binance-compatible wrapper around Bybit V5 API.
All subcommands print JSON to stdout for easy piping.
"""

from __future__ import annotations

from . import bybit

__all__ = ["bybit"]
