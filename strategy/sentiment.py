"""Research phase — fetch crypto news/sentiment before trading cycle.

Uses web search to gather market sentiment, feeds into confidence scoring.
Caches results for 15min to avoid redundant API calls.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

TRADES_DIR = Path(__file__).parent.parent / "trades"
SENTIMENT_CACHE = TRADES_DIR / ".sentiment_cache.json"
CACHE_TTL = 900  # 15 min

WATCHLIST_SYMBOLS = {
    "BTCUSDT": "Bitcoin BTC",
    "ETHUSDT": "Ethereum ETH",
    "SOLUSDT": "Solana SOL",
    "XRPUSDT": "XRP",
    "BNBUSDT": "BNB Binance",
    "SUIUSDT": "Sui SUI",
    "ADAUSDT": "Cardano ADA",
    "DOTUSDT": "Polkadot DOT",
    "APTUSDT": "Aptos APT",
    "NEARUSDT": "NEAR Protocol",
}


def _read_cache() -> dict:
    if SENTIMENT_CACHE.exists():
        try:
            data = json.loads(SENTIMENT_CACHE.read_text())
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_cache(data: dict) -> None:
    TRADES_DIR.mkdir(parents=True, exist_ok=True)
    data["timestamp"] = time.time()
    SENTIMENT_CACHE.write_text(json.dumps(data, indent=2))


def fetch_sentiment() -> dict:
    """Fetch crypto market sentiment via web search.
    
    Returns dict with:
      - overall_sentiment: 'bullish' | 'bearish' | 'neutral'
      - sentiment_score: -1.0 to +1.0
      - per_pair: {pair: {sentiment, score, keywords}}
      - news_highlights: [str]
      - fear_greed: int (0-100, if available)
    """
    # Check cache first
    cached = _read_cache()
    if cached:
        return cached

    result = {
        "overall_sentiment": "neutral",
        "sentiment_score": 0.0,
        "per_pair": {},
        "news_highlights": [],
        "fear_greed": None,
        "source": "web_search",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Fetch Fear & Greed Index
    try:
        import urllib.request
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            fng_data = json.loads(resp.read().decode())
            fng = fng_data["data"][0]
            result["fear_greed"] = int(fng["value"])
            result["fear_greed_label"] = fng["value_classification"]
            # Map to sentiment score
            val = int(fng["value"])
            if val >= 60:
                result["overall_sentiment"] = "greedy"
                result["sentiment_score"] = (val - 50) / 50.0  # 0.0 to 1.0
            elif val <= 40:
                result["overall_sentiment"] = "fearful"
                result["sentiment_score"] = (val - 50) / 50.0  # -1.0 to 0.0
            else:
                result["overall_sentiment"] = "neutral"
                result["sentiment_score"] = 0.0
    except Exception as e:
        result["fear_greed_error"] = str(e)

    # 2. Fetch crypto news via web search
    try:
        import subprocess
        # Search for latest crypto news
        news_queries = [
            "crypto market news today bitcoin ethereum",
            "cryptocurrency trading sentiment analysis",
        ]
        
        headlines = []
        for query in news_queries:
            try:
                proc = subprocess.run(
                    ["openclaw", "web-search", "--query", query, "--limit", "5"],
                    capture_output=True, text=True, timeout=30,
                    env={**os.environ, "PATH": f"/home/KOOMPI/.local/bin:{os.environ.get('PATH', '')}"}
                )
                if proc.returncode == 0 and proc.stdout:
                    for line in proc.stdout.strip().split("\n"):
                        line = line.strip()
                        if line and len(line) > 20:
                            headlines.append(line)
            except Exception:
                pass

        result["news_highlights"] = headlines[:10]

        # Simple sentiment from headlines
        bullish_words = ["surge", "rally", "bull", "breakout", "recovery", "up", "gain", "soar", "jump", "moon"]
        bearish_words = ["crash", "drop", "bear", "fall", "plunge", "dump", "slump", "down", "lose", "fear"]
        
        bull_count = sum(1 for h in headlines for w in bullish_words if w.lower() in h.lower())
        bear_count = sum(1 for h in headlines for w in bearish_words if w.lower() in h.lower())
        
        if bull_count > bear_count + 1:
            result["news_sentiment"] = "bullish"
        elif bear_count > bull_count + 1:
            result["news_sentiment"] = "bearish"
        else:
            result["news_sentiment"] = "neutral"

    except Exception as e:
        result["news_error"] = str(e)

    # 3. Per-pair sentiment (simplified — use Fear & Greed as base, adjust per pair)
    for pair, name in WATCHLIST_SYMBOLS.items():
        result["per_pair"][pair] = {
            "name": name,
            "sentiment": result["overall_sentiment"],
            "score": result["sentiment_score"],
        }

    # Cache and return
    _write_cache(result)
    return result


def sentiment_modifier(composite_score: float, pair: str, sentiment: dict) -> float:
    """Adjust composite score based on market sentiment.
    
    Rules:
    - If sentiment strongly bullish and signal is buy: +0.05 to +0.10
    - If sentiment strongly bearish and signal is buy: -0.10 to -0.15 (fade in bad conditions)
    - If sentiment strongly bearish and signal is sell: +0.05 to +0.10
    - If sentiment strongly bullish and signal is sell: -0.10 to -0.15
    - Neutral sentiment: no adjustment
    """
    score = sentiment.get("sentiment_score", 0.0)
    abs_score = abs(score)
    
    if abs_score < 0.2:
        return composite_score  # neutral, no adjustment
    
    if composite_score > 0:  # buy signal
        if score > 0:
            # Bullish sentiment + buy signal: slight boost
            return composite_score + abs_score * 0.1
        else:
            # Bearish sentiment + buy signal: reduce (counter-sentiment buy)
            return composite_score - abs_score * 0.15
    elif composite_score < 0:  # sell signal
        if score < 0:
            # Bearish sentiment + sell signal: slight boost
            return composite_score - abs_score * 0.1
        else:
            # Bullish sentiment + sell signal: reduce (counter-sentiment sell)
            return composite_score + abs_score * 0.15
    
    return composite_score
