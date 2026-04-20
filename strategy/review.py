"""Daily review routine — reviews trades, logs lessons, suggests param tweaks.

Run at end of day or on demand. Analyzes all trades from the past 24h,
calculates performance metrics, and generates actionable insights.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

TRADES_DIR = Path(__file__).parent.parent / "trades"
REVIEW_DIR = TRADES_DIR / "reviews"


def get_daily_trades(date_str: str = "") -> list[dict]:
    """Get all trades for a given date."""
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    log_file = TRADES_DIR / f"trade-log-{date_str}.jsonl"
    if not log_file.exists():
        return []
    
    trades = []
    for line in log_file.read_text().strip().split("\n"):
        if line:
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return trades


def calculate_metrics(trades: list[dict]) -> dict:
    """Calculate trading performance metrics."""
    if not trades:
        return {"total_trades": 0}
    
    buys = [t for t in trades if t.get("action") == "buy"]
    sells = [t for t in trades if t.get("action") == "sell"]
    
    total_buy_value = sum(t.get("qty", 0) * t.get("price", 0) for t in buys)
    total_sell_value = sum(t.get("qty", 0) * t.get("price", 0) for t in sells)
    
    # Pair-level analysis
    pair_trades = {}
    for t in trades:
        pair = t.get("pair", "UNKNOWN")
        if pair not in pair_trades:
            pair_trades[pair] = {"buys": [], "sells": [], "total_buy": 0, "total_sell": 0}
        if t.get("action") == "buy":
            pair_trades[pair]["buys"].append(t)
            pair_trades[pair]["total_buy"] += t.get("qty", 0) * t.get("price", 0)
        else:
            pair_trades[pair]["sells"].append(t)
            pair_trades[pair]["total_sell"] += t.get("qty", 0) * t.get("price", 0)
    
    # Signal quality
    signals_with_rationale = [t for t in trades if t.get("rationale")]
    trend_blocked = [t for t in signals_with_rationale if "trend_filter" in t.get("rationale", "")]
    regime_blocked = [t for t in signals_with_rationale if "regime" in t.get("rationale", "")]
    
    # Confidence distribution
    confidences = [t.get("confidence", 0) for t in trades if t.get("confidence")]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
    
    # Composite score distribution
    composites = [t.get("raw_score", t.get("composite_score", 0)) for t in trades]
    avg_composite = sum(composites) / len(composites) if composites else 0
    
    return {
        "total_trades": len(trades),
        "buys": len(buys),
        "sells": len(sells),
        "total_buy_value": round(total_buy_value, 2),
        "total_sell_value": round(total_sell_value, 2),
        "net_flow": round(total_sell_value - total_buy_value, 2),
        "pair_breakdown": {
            pair: {
                "buys": len(v["buys"]),
                "sells": len(v["sells"]),
                "buy_value": round(v["total_buy"], 2),
                "sell_value": round(v["total_sell"], 2),
            }
            for pair, v in pair_trades.items()
        },
        "signal_quality": {
            "trend_blocked": len(trend_blocked),
            "regime_blocked": len(regime_blocked),
            "avg_confidence": round(avg_confidence, 3),
            "avg_composite": round(avg_composite, 3),
        },
    }


def generate_review(date_str: str = "") -> dict:
    """Generate a daily review report."""
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    trades = get_daily_trades(date_str)
    metrics = calculate_metrics(trades)
    
    review = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "lessons": [],
        "param_suggestions": [],
    }
    
    # Generate lessons based on patterns
    if metrics["total_trades"] == 0:
        review["lessons"].append("No trades executed today. Market may be in quiet/chop regime.")
        review["lessons"].append("Consider lowering composite threshold or checking regime detection sensitivity.")
    elif metrics["sells"] == 0 and metrics["buys"] > 5:
        review["lessons"].append("All buys, no sells — accumulating positions without taking profit.")
        review["lessons"].append("Consider: Are sell signals firing? Is composite threshold too high for sells?")
        review["param_suggestions"].append({"param": "composite_threshold_sell", "current": -0.20, "suggested": -0.15, "reason": "No sells today, threshold may be too strict"})
    elif metrics["buys"] > metrics["sells"] * 3:
        review["lessons"].append(f"Buy-heavy day ({metrics['buys']} buys vs {metrics['sells']} sells).")
        review["lessons"].append("Signal reversal exits may not be triggering. Check composite scores on held positions.")
    
    sq = metrics["signal_quality"]
    if sq["regime_blocked"] > 5:
        review["lessons"].append(f"{sq['regime_blocked']} signals blocked by regime detection.")
        review["param_suggestions"].append({"param": "regime thresholds", "reason": f"Many blocked — consider if ADX thresholds are too strict"})
    
    if sq["avg_confidence"] < 0.3:
        review["lessons"].append(f"Low average confidence ({sq['avg_confidence']:.3f}). Most trades are weak signals.")
        review["param_suggestions"].append({"param": "composite_threshold_buy", "reason": "Low confidence trades may not be worth the risk"})
    
    if sq["avg_composite"] < 0.25:
        review["lessons"].append(f"Low average composite ({sq['avg_composite']:.3f}). Threshold may need lowering or indicators aren't aligning.")
    
    # Save review
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    review_file = REVIEW_DIR / f"review-{date_str}.json"
    review_file.write_text(json.dumps(review, indent=2))
    
    return review


def format_review_discord(review: dict) -> str:
    """Format review for Discord posting."""
    m = review["metrics"]
    date = review["date"]
    
    lines = [f"📋 **Daily Review — {date}**\n"]
    lines.append(f"Trades: {m['total_trades']} ({m['buys']} buy / {m['sells']} sell)")
    
    if m.get("total_buy_value"):
        lines.append(f"Buy volume: ${m['total_buy_value']:,.2f}")
    if m.get("total_sell_value"):
        lines.append(f"Sell volume: ${m['total_sell_value']:,.2f}")
    if m.get("net_flow"):
        flow = m['net_flow']
        emoji = "📈" if flow > 0 else "📉"
        lines.append(f"Net flow: {emoji} ${abs(flow):,.2f}")
    
    # Top traded pairs
    if m.get("pair_breakdown"):
        lines.append("\n**Pairs traded:**")
        for pair, data in sorted(m["pair_breakdown"].items(), key=lambda x: x[1]["buys"] + x[1]["sells"], reverse=True)[:5]:
            lines.append(f"  {pair}: {data['buys']}B / {data['sells']}S")
    
    # Signal quality
    sq = m["signal_quality"]
    lines.append(f"\n**Signal quality:** Avg confidence: {sq['avg_confidence']:.3f} | Avg composite: {sq['avg_composite']:.3f}")
    if sq["trend_blocked"]:
        lines.append(f"Trend blocked: {sq['trend_blocked']}")
    if sq["regime_blocked"]:
        lines.append(f"Regime blocked: {sq['regime_blocked']}")
    
    # Lessons
    if review.get("lessons"):
        lines.append("\n**Lessons:**")
        for lesson in review["lessons"][:5]:
            lines.append(f"  • {lesson}")
    
    # Suggestions
    if review.get("param_suggestions"):
        lines.append("\n**Param tweaks:**")
        for sug in review["param_suggestions"][:3]:
            if "current" in sug and "suggested" in sug:
                lines.append(f"  • `{sug['param']}`: {sug['current']} → {sug['suggested']} ({sug['reason']})")
            else:
                lines.append(f"  • {sug['param']}: {sug['reason']}")
    
    return "\n".join(lines)
