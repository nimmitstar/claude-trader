"""Main trading runner — fetch data, run strategy, execute, log."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

from binance_cli.binance import get_client

from strategy.engine import StrategyEngine
from strategy.opus import (
    apply_opus_changes,
    build_review_prompt,
    get_audit_trail,
    load_params,
    parse_opus_response,
)
from strategy.risk import Order, check_risk
from trader.log import TRADES_DIR, log_trade, save_portfolio_state

PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PAIR_ASSETS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}
TIMEFRAME = "15m"
NUM_BARS = 200

# Track open SL/TP order IDs: {pair: {"sl": order_id, "tp": order_id}}
_open_sl_tp_orders: dict[str, dict[str, int]] = {}
SL_TP_FILE = TRADES_DIR / "sl_tp_orders.json"


def _load_sl_tp_orders() -> None:
    """Load SL/TP orders from disk."""
    global _open_sl_tp_orders
    if SL_TP_FILE.exists():
        try:
            with open(SL_TP_FILE) as f:
                _open_sl_tp_orders = json.load(f)
        except (json.JSONDecodeError, ValueError):
            _open_sl_tp_orders = {}


def _save_sl_tp_orders() -> None:
    """Save SL/TP orders to disk."""
    SL_TP_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SL_TP_FILE, "w") as f:
        json.dump(_open_sl_tp_orders, f)


def cancel_sl_tp_orders(pair: str) -> None:
    """Cancel any open SL/TP orders for a pair."""
    global _open_sl_tp_orders
    if pair not in _open_sl_tp_orders:
        return
    client = get_client()
    for order_type in ("sl", "tp"):
        order_id = _open_sl_tp_orders[pair].get(order_type)
        if order_id:
            try:
                client.cancel_order(symbol=pair, orderId=order_id)
                print(f"  ↩️ Cancelled {order_type.upper()} order {order_id} for {pair}")
            except Exception:
                pass  # order may already be filled/cancelled
    del _open_sl_tp_orders[pair]
    _save_sl_tp_orders()


def fetch_bars(symbol: str, tf: str = TIMEFRAME, n: int = NUM_BARS) -> list[dict]:
    """Fetch bars from mainnet (real market data)."""
    client = get_client(mainnet=True)
    tf_map = {
        "1m": client.KLINE_INTERVAL_1MINUTE,
        "5m": client.KLINE_INTERVAL_5MINUTE,
        "15m": client.KLINE_INTERVAL_15MINUTE,
        "1h": client.KLINE_INTERVAL_1HOUR,
        "4h": client.KLINE_INTERVAL_4HOUR,
        "1d": client.KLINE_INTERVAL_1DAY,
    }
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    klines = client.get_historical_klines(
        symbol,
        tf_map[tf],
        start_str=start.strftime("%d %b %Y %H:%M:%S"),
        end_str=end.strftime("%d %b %Y %H:%M:%S"),
        limit=n,
    )
    bars = []
    for k in klines[-n:]:
        bars.append({
            "timestamp": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": k[6],
            "quote_volume": float(k[7]),
            "trades": int(k[8]),
        })

    # Stale data check: most recent bar should be within 30 minutes
    if bars:
        last_bar_time = datetime.fromtimestamp(bars[-1]["timestamp"] / 1000, tz=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        if now_utc - last_bar_time > timedelta(minutes=30):
            print(f"  ⚠️ Stale data: last bar from {last_bar_time.strftime('%H:%M:%S')} (>{(now_utc - last_bar_time).seconds // 60} min old)")

    return bars


def get_account_info() -> dict:
    """Get account balance and USDT available."""
    client = get_client()
    account = client.get_account()

    balances = {}
    usdt_free = 0.0
    for b in account["balances"]:
        free = float(b["free"])
        locked = float(b["locked"])
        total = free + locked
        if total > 0:
            balances[b["asset"]] = {"free": free, "locked": locked, "total": total}
            if b["asset"] == "USDT":
                usdt_free = free

    return {"balances": balances, "usdt_available": usdt_free}


def get_current_price(symbol: str) -> float:
    """Get current price from mainnet."""
    client = get_client(mainnet=True)
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])


def run(dry_run: bool = True) -> dict:
    """Main trading run.

    Args:
        dry_run: if True, don't execute trades

    Returns:
        Summary dict with all signals and actions
    """
    _load_sl_tp_orders()  # Load persisted SL/TP orders
    engine = StrategyEngine()
    account = get_account_info()
    usdt_available = account["usdt_available"]

    # Calculate portfolio value (USDT + positions)
    prices: dict[str, float] = {}
    for pair in PAIRS:
        prices[pair] = get_current_price(pair)

    positions: list[dict] = []
    total_value = usdt_available

    pair_assets = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}
    for pair in PAIRS:
        asset = pair_assets[pair]
        bal = account["balances"].get(asset)
        if bal and bal["total"] > 0:
            val = bal["total"] * prices[pair]
            positions.append({
                "asset": asset,
                "pair": pair,
                "qty": bal["total"],
                "price": prices[pair],
                "value_usdt": val,
            })
            total_value += val

    print(f"Portfolio: {total_value:,.2f} USDT | Available: {usdt_available:,.2f} USDT")
    print(f"Positions: {len(positions)}")
    print("-" * 60)

    results = []
    executed_trades: list[dict] = []

    for pair in PAIRS:
        print(f"\n📊 Analyzing {pair}...")

        # Fetch bars
        bars = fetch_bars(pair)
        print(f"  Fetched {len(bars)} bars")

        if len(bars) < 50:
            print(f"  ⚠️ Insufficient bars: {len(bars)} < 50, skipping")
            results.append({"pair": pair, "action": "hold", "rationale": "insufficient_data", "executed": False})
            continue

        # Run strategy
        signal = engine.analyze(pair, bars, usdt_available)
        kronos_line = ""
        if signal.get("kronos_predicted_close"):
            kronos_line = f" | Kronos: {signal['kronos_predicted_close']}"
        print(f"  Signal: {signal['action']} (confidence: {signal['confidence']})")
        print(f"  RSI: {signal['rsi']} | MA: {signal['ma_signal']} | Score: {signal['raw_score']}{kronos_line}")
        print(f"  Rationale: {signal['rationale']}")

        result = {**signal, "executed": False, "order_result": None}

        if signal["action"] in ("buy", "sell") and not dry_run:
            qty = signal["suggested_qty"]
            price = signal["current_price"]
            side = signal["action"]

            # Get exchange info for lot size and min notional (testnet)
            step_size = 0.001
            min_notional = 5.0
            try:
                _client = get_client(mainnet=False)  # testnet for execution rules
                info = _client.get_symbol_info(pair)
                if not info:
                    # Fallback to mainnet if testnet symbol info unavailable
                    _client = get_client(mainnet=True)
                    info = _client.get_symbol_info(pair)
                for f in info['filters']:
                    if f['filterType'] == 'LOT_SIZE':
                        step_size = float(f['stepSize'])
                    elif f['filterType'] == 'MIN_NOTIONAL':
                        min_notional = float(f['minNotional'])

                # Round qty to lot step size
                qty = round(qty / step_size) * step_size
                decimals = len(str(step_size).rstrip('0').split('.')[-1])
                qty = float(f"{qty:.{decimals}}f")

                # Fix #5: lot size → zero qty guard
                if qty < step_size:
                    print(f"  ❌ Quantity {qty} below minimum lot size {step_size}")
                    result["risk_reason"] = "qty_below_lot_minimum"
                    results.append(result)
                    continue

                # Fix #6: min notional check
                order_value = qty * price
                if order_value < min_notional:
                    print(f"  ❌ Order value ${order_value:.2f} below minimum ${min_notional}")
                    result["risk_reason"] = "below_min_notional"
                    results.append(result)
                    continue
            except Exception as e:
                print(f"  ⚠️ Could not get symbol info: {e}")

            # Fix #2: sell without position check
            if side == "sell":
                asset = PAIR_ASSETS.get(pair)
                if asset:
                    held = account["balances"].get(asset, {}).get("free", 0)
                    if qty > held:
                        qty = held
                    if qty <= 0:
                        print(f"  ❌ No {asset} to sell")
                        result["risk_reason"] = "no_position"
                        results.append(result)
                        continue

            order = Order(pair=pair, side=side, qty=qty, price=price)
            risk = check_risk(positions, order, total_value, usdt_available)

            if not risk["allowed"]:
                print(f"  ❌ Risk blocked: {risk['reason']}")
                result["risk_reason"] = risk["reason"]
            else:
                print(f"  ✅ Risk ok | SL: {risk['stop_loss']} | TP: {risk['take_profit']}")
                try:
                    client = get_client()
                    if side == "buy":
                        order_res = client.order_market_buy(symbol=pair, quantity=qty)
                    else:
                        order_res = client.order_market_sell(symbol=pair, quantity=qty)

                    trade_entry = {
                        "pair": pair,
                        "action": side,
                        "qty": qty,
                        "price": price,
                        "confidence": signal["confidence"],
                        "signal_details": signal["signal_details"],
                        "stop_loss": risk["stop_loss"],
                        "take_profit": risk["take_profit"],
                        "rationale": signal["rationale"],
                        "order_id": order_res["orderId"],
                        "status": order_res["status"],
                    }

                    # Fix #1: Place SL/TP as actual exchange orders after buy
                    sl_order_id = None
                    tp_order_id = None
                    if side == "buy":
                        fills = order_res.get("fills") or []
                        entry_price = float(fills[0]["price"]) if fills and fills[0].get("price") else price
                        stop_loss_pct = risk["stop_loss"] / entry_price * 100 if entry_price > 0 else 3.0
                        take_profit_pct = (risk["take_profit"] / entry_price - 1) * 100 if entry_price > 0 else 6.0
                        sl_price = entry_price * (1 - abs(stop_loss_pct) / 100)
                        tp_price = entry_price * (1 + abs(take_profit_pct) / 100)

                        try:
                            # TODO: Verify these parameters match Binance testnet API — test with small order first
                            sl_order = client.create_order(
                                symbol=pair, side="SELL", type="STOP_MARKET",
                                quantity=qty, stopPrice=str(round(sl_price, 2)),
                                timeInForce="GTC",
                            )
                            sl_order_id = sl_order["orderId"]
                            print(f"  🛡️ SL order placed @ {sl_price:.2f} (id: {sl_order_id})")
                        except Exception as e:
                            print(f"  ⚠️ SL order failed: {e}")

                        try:
                            tp_order = client.create_order(
                                symbol=pair, side="SELL", type="TAKE_PROFIT_MARKET",
                                quantity=qty, stopPrice=str(round(tp_price, 2)),
                                timeInForce="GTC",
                            )
                            tp_order_id = tp_order["orderId"]
                            print(f"  🎯 TP order placed @ {tp_price:.2f} (id: {tp_order_id})")
                        except Exception as e:
                            print(f"  ⚠️ TP order failed: {e}")

                        if sl_order_id or tp_order_id:
                            _open_sl_tp_orders[pair] = {
                                "sl": sl_order_id,
                                "tp": tp_order_id,
                            }
                            _save_sl_tp_orders()

                    elif side == "sell":
                        # Cancel SL/TP orders before selling
                        cancel_sl_tp_orders(pair)

                    trade_entry["sl_order_id"] = sl_order_id
                    trade_entry["tp_order_id"] = tp_order_id

                    log_trade(trade_entry)
                    executed_trades.append(trade_entry)

                    if side == "buy":
                        engine.record_entry(pair)
                        usdt_available -= qty * price
                    else:
                        usdt_available += qty * price

                    # Fix #4: re-fetch actual balance after trade
                    account = get_account_info()
                    usdt_available = account["usdt_available"]

                    print(f"  ✅ Executed: {side} {qty} {pair} @ {price}")
                    result["executed"] = True
                    result["order_result"] = {"order_id": order_res["orderId"]}
                except Exception as e:
                    print(f"  ❌ Order failed: {e}")
                    result["order_result"] = {"error": str(e)}
        elif dry_run and signal["action"] != "hold":
            print(f"  🔍 DRY RUN — would {signal['action']} {signal['suggested_qty']} {pair}")

        results.append(result)

    # --- Opus review for executed trades ---
    opus_results = []
    if executed_trades:
        print("\n🧠 Claude Opus reviewing trades...")
        params = load_params()
        for trade in executed_trades:
            # Build market context from bars
            pair = trade["pair"]
            try:
                bars = fetch_bars(pair)
                last_5 = bars[-5:] if len(bars) >= 5 else bars
                market_ctx = {
                    "pair": pair,
                    "recent_closes": [b["close"] for b in last_5],
                    "recent_volumes": [b["volume"] for b in last_5],
                    "current_params": params,
                }
                prompt = build_review_prompt(trade, market_ctx)
                # Note: actual Opus call happens in the cron session via sessions_spawn
                # Here we log the review request for the cron session to pick up
                review_request = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trade": trade,
                    "market_context": market_ctx,
                    "review_prompt": prompt,
                    "status": "pending_opus_review",
                }
                opus_results.append(review_request)
            except Exception as e:
                print(f"  ⚠️ Could not build review for {pair}: {e}")

        # Save review requests for cron session
        if opus_results:
            review_file = TRADES_DIR / f"opus-review-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            with open(review_file, "w") as f:
                json.dump(opus_results, f, indent=2)
            print(f"  📝 {len(opus_results)} review(s) queued for Opus")

    # Save portfolio state
    portfolio_state = {
        "total_value_usdt": total_value,
        "usdt_available": usdt_available,
        "positions": positions,
        "signals": results,
        "trades_executed": executed_trades,
        "dry_run": dry_run,
    }
    save_portfolio_state(portfolio_state)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    buys = [r for r in results if r["action"] == "buy"]
    sells = [r for r in results if r["action"] == "sell"]
    holds = [r for r in results if r["action"] == "hold"]
    print(f"  Buy signals:  {len(buys)}")
    print(f"  Sell signals: {len(sells)}")
    print(f"  Hold:         {len(holds)}")
    print(f"  Trades executed: {len(executed_trades)}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")

    return portfolio_state


def main() -> None:
    """CLI entry point."""
    dry = "--live" not in sys.argv
    run(dry_run=dry)


if __name__ == "__main__":
    main()
