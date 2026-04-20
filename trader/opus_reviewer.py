#!/usr/bin/env python3
"""Opus review runner — called by cron session after trades execute.

Reads pending review files, calls Claude Opus via subprocess (simulated here
for the cron session to orchestrate), and auto-applies parameter changes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from strategy.opus import get_audit_trail

TRADES_DIR = Path(__file__).parent.parent / "trades"


def find_pending_reviews() -> list[Path]:
    """Find unprocessed Opus review files."""
    if not TRADES_DIR.exists():
        return []
    return sorted(TRADES_DIR.glob("opus-review-*.json"))


def call_opus(prompt: str) -> str:
    """Return the review prompt for the cron session to pass to Opus.

    The cron session uses sessions_spawn with claude-opus-4.6 model override.
    This function exists for API compatibility; actual Opus calls happen externally.
    """
    return prompt


def main() -> dict:
    """Process pending reviews and apply Opus suggestions."""
    pending = find_pending_reviews()

    if not pending:
        return {"status": "no_pending_reviews", "processed": 0}

    results = []
    for review_file in pending:
        with open(review_file) as f:
            reviews = json.load(f)

        file_results = []
        for review in reviews:
            prompt = review.get("review_prompt", "")
            trade = review.get("trade", {})

            if not prompt:
                continue

            # Output prompt for cron session to feed to Opus
            print("=== OPUS REVIEW REQUEST ===")
            print(f"Trade: {trade.get('pair')} {trade.get('action')} @ {trade.get('price')}")
            print(f"Confidence: {trade.get('confidence')}")
            print(f"Prompt length: {len(prompt)} chars")
            print("=== END REQUEST ===")
            print()

            file_results.append({
                "trade": f"{trade.get('pair')} {trade.get('action')}",
                "status": "needs_opus_call",
                "review_file": str(review_file),
            })

        results.extend(file_results)

    return {
        "status": "reviews_queued",
        "pending_files": len(pending),
        "reviews": results,
        "audit_trail": get_audit_trail(5),
    }


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2))
