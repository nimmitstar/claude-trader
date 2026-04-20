"""Binance trading CLI.

All subcommands print JSON to stdout for easy piping.
"""

from __future__ import annotations

from .binance import cli

__all__ = ["cli"]
