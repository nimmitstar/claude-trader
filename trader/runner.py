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
TIMEFRAME = "1h"
NUM_BARS = 200


def fetch_bars(symbol: str, tf: str = TIMEFRAME, n: int = NUM_BARS) -> list[dict]:
    """Fetch bars using the binance client directly."""
    client = get_client()
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
    """Get current price for a symbol."""
    client = get_client()
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])


def run(dry_run: bool = True) -> dict:
    """Main trading run.

    Args:
        dry_run: if True, don't execute trades

    Returns:
        Summary dict with all signals and actions
    """
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
        print(f"  Signal: {signal['action']} (confidence: {signal['confidence']})")
        print(f"  RSI: {signal['rsi']} | MA: {signal['ma_signal']} | Score: {signal['raw_score']}")
        print(f"  Rationale: {signal['rationale']}")

        result = {**signal, "executed": False, "order_result": None}

        if signal["action"] in ("buy", "sell") and not dry_run:
            qty = signal["suggested_qty"]
            price = signal["current_price"]
            side = signal["action"]

            # Round qty to lot step size
            try:
                _client = get_client()
                info = _client.get_symbol_info(pair)
                lot_size = next(f for f in info['filters'] if f['filterType'] == 'LOT_SIZE')
                step_size = float(lot_size['stepSize'])
                qty = round(qty / step_size) * step_size
                decimals = len(str(step_size).rstrip('0').split('.')[-1])
                qty = float(f"{qty:.{decimals}}f")
            except Exception:
                pass  # fallback to unrounded qty

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
                    log_trade(trade_entry)
                    executed_trades.append(trade_entry)

                    if side == "buy":
                        engine.record_entry(pair)
                        usdt_available -= qty * price
                    else:
                        usdt_available += qty * price

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
