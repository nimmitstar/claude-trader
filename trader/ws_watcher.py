"""WebSocket watcher — real-time price monitoring for reactive trading.

Listens to Bybit spot tickers via WebSocket. When a pair moves >2% since last analysis,
triggers an immediate strategy run (skipped if analysis happened within 10 min).

Shares a lock file with the cron runner to prevent duplicate analyses.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from exchange_cli.bybit import get_client
from strategy.engine import StrategyEngine
from strategy.risk import Order, check_risk, check_daily_circuit_breaker
from trader.log import TRADES_DIR, log_trade
from trader.runner import (
    PAIRS,
    PAIR_ASSETS,
    cancel_sl_tp_orders,
    fetch_bars,
    get_account_info,
    get_current_price,
)

# WebSocket URL (Bybit mainnet public spot tickers)
WS_URL = "wss://stream.bybit.com/v5/public/spot"

# Config
MOVE_THRESHOLD_PCT = 2.0  # Trigger analysis if price moves this much
COOLDOWN_SECONDS = 600  # 10 min between analyses per pair (dedup with cron)
LOCK_DIR = TRADES_DIR / ".analysis_locks"

# Track prices
_last_prices: dict[str, float] = {}


def _get_lock_path(pair: str) -> Path:
    return LOCK_DIR / f"{pair}.lock"


def _is_locked(pair: str) -> bool:
    """Check if pair was analyzed recently (within cooldown)."""
    lock = _get_lock_path(pair)
    if not lock.exists():
        return False
    try:
        ts = float(lock.read_text().strip())
        return (time.time() - ts) < COOLDOWN_SECONDS
    except (ValueError, OSError):
        return False


def _set_lock(pair: str) -> None:
    """Mark pair as just analyzed."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    _get_lock_path(pair).write_text(str(time.time()))


def _on_ticker(data: dict) -> None:
    """Handle incoming ticker message."""
    if data.get("topic") != "tickers":
        return

    for item in data.get("data", []):
        symbol = item.get("s", "")
        if symbol not in PAIRS:
            continue

        try:
            price = float(item.get("lastPrice", 0))
        except (ValueError, TypeError):
            continue

        if price <= 0:
            continue

        # Check if this is a big move
        last = _last_prices.get(symbol)
        if last and last > 0:
            move_pct = abs(price - last) / last * 100
            if move_pct >= MOVE_THRESHOLD_PCT:
                if _is_locked(symbol):
                    print(f"  ⏭️ {symbol}: {move_pct:.1f}% move but locked (analyzed recently)")
                    continue

                print(f"\n🚨 {symbol}: {move_pct:.1f}% move detected (${last:,.2f} → ${price:,.2f})")
                try:
                    from trader.discord_notify import notify_big_move
                    notify_big_move(symbol, move_pct, price, last)
                except Exception:
                    pass
                _set_lock(symbol)
                _run_analysis(symbol, price)

        _last_prices[symbol] = price


def _run_analysis(pair: str, trigger_price: float) -> None:
    """Run strategy analysis for a single pair and execute if signal fires."""
    print(f"  📊 Running reactive analysis for {pair}...")

    # Circuit breaker check
    if check_daily_circuit_breaker(TRADES_DIR):
        print("  🛑 CIRCUIT BREAKER active, skipping")
        return

    # Fetch bars
    bars = fetch_bars(pair)
    if len(bars) < 50:
        print(f"  ⚠️ Insufficient bars: {len(bars)}")
        return

    # Get account info
    account = get_account_info()
    usdt_available = account["usdt_available"]

    # Build positions list
    positions = []
    price = get_current_price(pair)
    asset = PAIR_ASSETS.get(pair)
    if asset:
        bal = account["balances"].get(asset)
        if bal and bal["total"] > 0:
            positions.append({
                "asset": asset, "pair": pair,
                "qty": bal["total"], "price": price,
                "value_usdt": bal["total"] * price,
                "is_new": False,
            })

    # Run strategy
    engine = StrategyEngine()
    signal = engine.analyze(pair, bars, usdt_available)

    kronos_line = ""
    if signal.get("kronos_predicted_close"):
        kronos_line = f" | Kronos: {signal['kronos_predicted_close']}"
    print(f"  Signal: {signal['action']} (confidence: {signal['confidence']})")
    print(f"  RSI: {signal['rsi']} | MA: {signal['ma_signal']} | Score: {signal['raw_score']}{kronos_line}")
    print(f"  Trend: {signal['signal_details'].get('trend', 'N/A')}")
    print(f"  Rationale: {signal['rationale']}")

    if signal["action"] not in ("buy", "sell"):
        print(f"  ⏸️ No action needed")
        return

    # Execute trade
    qty = signal["suggested_qty"]
    price = signal["current_price"]
    side = signal["action"]

    # Lot size rounding
    try:
        _client = get_client(mainnet=False)
        info = _client.get_symbol_info(pair)
        if not info:
            _client = get_client(mainnet=True)
            info = _client.get_symbol_info(pair)
        for f in info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                qty = round(qty / step_size) * step_size
                step_str = f"{step_size:.10f}".rstrip('0').rstrip('.')
                decimals = len(step_str.split('.')[-1]) if '.' in step_str else 0
                qty = round(qty, decimals)
            elif f['filterType'] == 'MIN_NOTIONAL':
                min_notional = float(f['minNotional'])
                if qty * price < min_notional:
                    print(f"  ❌ Below min notional")
                    return
        if qty <= 0:
            print(f"  ❌ Qty rounded to 0")
            return
    except Exception as e:
        print(f"  ⚠️ Symbol info error: {e}")

    # Sell check
    if side == "sell" and asset:
        held = account["balances"].get(asset, {}).get("free", 0)
        if qty > held:
            qty = held
        if qty <= 0:
            print(f"  ❌ No {asset} to sell")
            return

    # Risk check
    order = Order(pair=pair, side=side, qty=qty, price=price)
    total_value = usdt_available + sum(p["value_usdt"] for p in positions)
    risk = check_risk(positions, order, total_value, usdt_available)
    if not risk["allowed"]:
        print(f"  ❌ Risk blocked: {risk['reason']}")
        return

    print(f"  ✅ Risk ok | SL: {risk['stop_loss']} | TP: {risk['take_profit']}")

    # Execute
    try:
        client = get_client()
        if side == "buy":
            order_res = client.order_market_buy(symbol=pair, quantity=qty)
        else:
            order_res = client.order_market_sell(symbol=pair, quantity=qty)

        trade_entry = {
            "pair": pair, "action": side, "qty": qty, "price": price,
            "confidence": signal["confidence"],
            "signal_details": signal["signal_details"],
            "stop_loss": risk["stop_loss"], "take_profit": risk["take_profit"],
            "rationale": signal["rationale"],
            "order_id": order_res["orderId"], "status": order_res["status"],
            "source": "websocket_reactive",
        }

        # SL/TP
        if side == "buy":
            for order_type, price_key in [("STOP_MARKET", "stop_loss"), ("TAKE_PROFIT_MARKET", "take_profit")]:
                try:
                    sl_tp = client.create_order(
                        symbol=pair, side="SELL", type=order_type,
                        quantity=qty, stopPrice=risk[price_key],
                    )
                    tag = "sl" if "STOP" in order_type else "tp"
                    trade_entry[f"{tag}_order_id"] = sl_tp["orderId"]
                    print(f"  {'🛡️' if tag == 'sl' else '🎯'} {order_type} @ {risk[price_key]:,.2f}")
                except Exception as e:
                    print(f"  ⚠️ {order_type} failed: {e}")
        elif side == "sell":
            cancel_sl_tp_orders(pair)

        log_trade(trade_entry)
        print(f"  ✅ EXECUTED: {side.upper()} {qty} {pair} @ ${price:,.2f} [WS REACTIVE]")

        # Post to Discord immediately
        try:
            from trader.discord_notify import notify_trade
            notify_trade(
                side, pair, qty, price,
                signal.get("confidence", ""),
                signal.get("rationale", ""),
                trade_entry.get("stop_loss", 0),
                trade_entry.get("take_profit", 0),
                source="websocket_reactive",
            )
        except Exception:
            pass
    except Exception as e:
        print(f"  ❌ Order failed: {e}")


def main() -> None:
    """Run WebSocket watcher."""
    print(f"🔗 WebSocket watcher starting...")
    print(f"   Monitoring {len(PAIRS)} pairs for >{MOVE_THRESHOLD_PCT}% moves")
    print(f"   Analysis cooldown: {COOLDOWN_SECONDS}s")
    print(f"   Press Ctrl+C to stop")
    print()

    try:
        import asyncio
        import websockets
    except ImportError:
        print("Installing websockets...")
        subprocess.run(["uv", "pip", "install", "websockets"], check=True)
        import asyncio
        import websockets

    # Initialize last prices from current tickers
    print("Fetching initial prices...")
    for pair in PAIRS:
        try:
            price = get_current_price(pair)
            _last_prices[pair] = price
            print(f"  {pair}: ${price:,.2f}")
        except Exception as e:
            print(f"  {pair}: failed to fetch ({e})")
    print()

    # Build subscription message
    args = ",".join(f'tickers.{pair}' for pair in PAIRS)

    async def _ws_loop():
        async with websockets.connect(WS_URL) as ws:
            # Subscribe
            sub_msg = json.dumps({"op": "subscribe", "args": args.split(",")})
            await ws.send(sub_msg)
            print(f"📡 Subscribed to {len(PAIRS)} ticker streams")
            print(f"   Waiting for price moves...\n")

            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    data = json.loads(msg)

                    if "topic" in data:
                        _on_ticker(data)
                    elif data.get("op") == "subscribe":
                        print(f"  ✅ Subscribed: {data.get('success', {})}")

                except asyncio.TimeoutError:
                    # Send ping to keep alive
                    await ws.send(json.dumps({"op": "ping"}))

    try:
        asyncio.run(_ws_loop())
    except KeyboardInterrupt:
        print("\n👋 WebSocket watcher stopped")


if __name__ == "__main__":
    main()
