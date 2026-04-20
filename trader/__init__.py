"""Trader module — executes trades, manages positions, handles notifications.

Main components:
- runner: Main trading loop, order execution, SL/TP management
- log: Trade logging to JSONL files
- notify: Discord notification formatting
- discord_notify: Webhook integration
- ws_watcher: Real-time price monitoring for reactive trading
- opus_reviewer: Post-trade analysis via Claude Opus
"""

from __future__ import annotations

__all__ = ["runner", "log", "notify", "discord_notify", "ws_watcher", "opus_reviewer"]