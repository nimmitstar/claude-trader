"""Binance trading CLI.

All subcommands print JSON to stdout for easy piping.
"""

import json
import os
import sys
from datetime import UTC, datetime, timedelta

import click
from binance.client import Client
from binance.enums import HistoricalKlinesType
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

load_dotenv()


def get_client() -> Client:
    """Get authenticated Binance client.

    Uses testnet by default for safe development.
    """
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        raise click.ClickException("BINANCE_API_KEY and BINANCE_API_SECRET must be set in .env")

    # Use testnet by default
    testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    if testnet:
        # Binance Spot Testnet
        return Client(api_key, api_secret, testnet=True)
    else:
        return Client(api_key, api_secret)


def json_output(data: object) -> None:
    """Print JSON to stdout."""

    def serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode()
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        if isinstance(obj, (list, tuple)):
            return [serialize(x) for x in obj]
        if isinstance(obj, dict):
            return {k: serialize(v) for k, v in obj.items()}
        return obj

    json.dump(serialize(data), sys.stdout, default=serialize, indent=2)


@click.group()
@click.version_option(package_name="alpaca-claude-trader")
def cli() -> None:
    """Binance trading CLI - JSON output only."""
    pass


@cli.command()
def account() -> None:
    """Show account information."""
    client = get_client()

    result = client.get_account()

    # Parse account info
    balances = [
        {"asset": b["asset"], "free": float(b["free"]), "locked": float(b["locked"])}
        for b in result["balances"]
        if float(b["free"]) > 0 or float(b["locked"]) > 0
    ]

    output = {
        "maker_commission": result["makerCommission"],
        "taker_commission": result["takerCommission"],
        "buyer_commission": result["buyerCommission"],
        "seller_commission": result["sellerCommission"],
        "can_trade": result["canTrade"],
        "can_withdraw": result["canWithdraw"],
        "can_deposit": result["canDeposit"],
        "update_time": result["updateTime"],
        "account_type": result["accountType"],
        "balances": balances,
        "permissions": result["permissions"],
    }
    json_output(output)


@cli.command()
def positions() -> None:
    """List open positions."""
    client = get_client()
    account = client.get_account()

    result = []
    for b in account["balances"]:
        free = float(b["free"])
        locked = float(b["locked"])
        if free > 0 or locked > 0:
            result.append({
                "asset": b["asset"],
                "free": free,
                "locked": locked,
                "total": free + locked,
            })

    json_output(result)


@cli.command()
@click.argument("symbol")
@click.option("--tf", default="1h", help="Timeframe (1m, 5m, 15m, 1h, 4h, 1d)")
@click.option("--n", default=500, type=int, help="Number of bars")
def bars(symbol: str, tf: str, n: int) -> None:
    """Fetch historical klines (bars) for a symbol.

    Symbol format: BTCUSDT (not BTC/USD)
    """
    client = get_client()

    # Map timeframe strings to Binance interval constants
    tf_map = {
        "1m": Client.KLINE_INTERVAL_1MINUTE,
        "5m": Client.KLINE_INTERVAL_5MINUTE,
        "15m": Client.KLINE_INTERVAL_15MINUTE,
        "1h": Client.KLINE_INTERVAL_1HOUR,
        "4h": Client.KLINE_INTERVAL_4HOUR,
        "1d": Client.KLINE_INTERVAL_1DAY,
        "1w": Client.KLINE_INTERVAL_1WEEK,
    }

    if tf not in tf_map:
        raise click.ClickException(f"Invalid timeframe. Use: {', '.join(tf_map)}")

    interval = tf_map[tf]
    end = datetime.now(UTC)
    start = end - timedelta(days=30)  # Binance will return up to n bars

    # Fetch klines
    klines = client.get_historical_klines(
        symbol,
        interval,
        start_str=start.strftime("%d %b %Y %H:%M:%S"),
        end_str=end.strftime("%d %b %Y %H:%M:%S"),
        limit=n,
        klines_type=HistoricalKlinesType.SPOT,
    )

    result = []
    for k in klines[-n:]:
        result.append({
            "timestamp": k[0],  # Open time
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": k[6],
            "quote_volume": float(k[7]),
            "trades": int(k[8]),
        })

    json_output(result)


@cli.command()
@click.argument("symbol")
def price(symbol: str) -> None:
    """Get current price for a symbol.

    Symbol format: BTCUSDT (not BTC/USD)
    """
    client = get_client()

    ticker = client.get_symbol_ticker(symbol=symbol)

    result = {
        "symbol": ticker["symbol"],
        "price": float(ticker["price"]),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    json_output(result)


@cli.command()
@click.argument("symbol")
@click.option("--qty", type=float, required=True, help="Quantity to buy")
@click.option("--type", default="market", help="Order type (market, limit)")
@click.option("--price", type=float, help="Price (for limit orders)")
def buy(symbol: str, qty: float, type: str, price: float | None) -> None:
    """Submit buy order.

    Symbol format: BTCUSDT (not BTC/USD)
    """
    client = get_client()

    try:
        if type == "market":
            order = client.order_market_buy(symbol=symbol, quantity=qty)
        elif type == "limit":
            if not price:
                raise click.ClickException("Limit orders require --price")
            order = client.order_limit_buy(symbol=symbol, quantity=qty, price=str(price))
        else:
            raise click.ClickException(f"Invalid order type: {type}")

        result = {
            "symbol": order["symbol"],
            "order_id": order["orderId"],
            "client_order_id": order["clientOrderId"],
            "transact_time": order["transactTime"],
            "price": float(order.get("price", 0)) if order.get("price") else None,
            "qty": float(order.get("executedQty", 0)) if order.get("executedQty") else None,
            "cummulative_quote_qty": float(order["cummulativeQuoteQty"]),
            "status": order["status"],
            "side": order["side"],
            "type": order["type"],
        }
        json_output(result)
    except BinanceAPIException as e:
        raise click.ClickException(f"Order failed: {e}")


@cli.command()
@click.argument("symbol")
@click.option("--qty", type=float, required=True, help="Quantity to sell")
@click.option("--type", default="market", help="Order type (market, limit)")
@click.option("--price", type=float, help="Price (for limit orders)")
def sell(symbol: str, qty: float, type: str, price: float | None) -> None:
    """Submit sell order.

    Symbol format: BTCUSDT (not BTC/USD)
    """
    client = get_client()

    try:
        if type == "market":
            order = client.order_market_sell(symbol=symbol, quantity=qty)
        elif type == "limit":
            if not price:
                raise click.ClickException("Limit orders require --price")
            order = client.order_limit_sell(symbol=symbol, quantity=qty, price=str(price))
        else:
            raise click.ClickException(f"Invalid order type: {type}")

        result = {
            "symbol": order["symbol"],
            "order_id": order["orderId"],
            "client_order_id": order["clientOrderId"],
            "transact_time": order["transactTime"],
            "price": float(order.get("price", 0)) if order.get("price") else None,
            "qty": float(order.get("executedQty", 0)) if order.get("executedQty") else None,
            "cummulative_quote_qty": float(order["cummulativeQuoteQty"]),
            "status": order["status"],
            "side": order["side"],
            "type": order["type"],
        }
        json_output(result)
    except BinanceAPIException as e:
        raise click.ClickException(f"Order failed: {e}")


@cli.command()
@click.option("--all", is_flag=True, help="Cancel all open orders")
@click.argument("symbol", required=False, default=None)
@click.argument("order_id", required=False, default=None)
def cancel(all: bool, symbol: str | None, order_id: str | None) -> None:
    """Cancel orders.

    With --all: cancel all orders for a symbol
    With symbol only: list open orders for that symbol
    With symbol and order_id: cancel specific order
    """
    client = get_client()

    if all:
        if not symbol:
            raise click.ClickException("--all requires a symbol")
        result = client.cancel_open_orders(symbol=symbol)
        json_output({"canceled_count": len(result), "orders": result})
    elif order_id:
        if not symbol:
            raise click.ClickException("order_id requires a symbol")
        result = client.cancel_order(symbol=symbol, orderId=order_id)
        json_output({"canceled": order_id, "symbol": symbol})
    else:
        # List orders
        if not symbol:
            # List all open orders
            orders = client.get_open_orders()
        else:
            orders = client.get_open_orders(symbol=symbol)

        result = [
            {
                "symbol": o["symbol"],
                "order_id": o["orderId"],
                "client_order_id": o["clientOrderId"],
                "price": float(o["price"]),
                "qty": float(o["origQty"]),
                "executed_qty": float(o["executedQty"]),
                "status": o["status"],
                "side": o["side"],
                "type": o["type"],
                "time": o["time"],
            }
            for o in orders
        ]
        json_output(result)


if __name__ == "__main__":
    cli()
