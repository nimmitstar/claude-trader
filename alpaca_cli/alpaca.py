"""Alpaca trading CLI.

All subcommands print JSON to stdout for easy piping.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
from alpaca.broker import BrokerClient
from alpaca.common import APIError
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.models import BarSet
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from dotenv import load_dotenv

load_dotenv()


def get_broker() -> BrokerClient:
    """Get authenticated broker client."""
    api_key = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_API_SECRET")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if not api_key or not api_secret:
        raise click.ClickException("ALPACA_API_KEY and ALPACA_API_SECRET must be set in .env")

    return BrokerClient(api_key=api_key, secret_key=api_secret, url=base_url)


def get_data_client() -> CryptoHistoricalDataClient:
    """Get crypto data client."""
    api_key = os.getenv("ALPACA_API_KEY")
    api_secret = os.getenv("ALPACA_API_SECRET")

    if not api_key or not api_secret:
        raise click.ClickException("ALPACA_API_KEY and ALPACA_API_SECRET must be set in .env")

    return CryptoHistoricalDataClient(api_key=api_key, secret_key=api_secret)


def json_output(data: object) -> None:
    """Print JSON to stdout."""
    def serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        if isinstance(obj, (list, tuple)):
            return [serialize(x) for x in obj]
        if isinstance(obj, dict):
            return {k: serialize(v) for k, v in obj.items()}
        return obj

    json.dump(serialize(data), sys.stdout, default=serialize, indent=2)


import sys


@click.group()
@click.version_option(package_name="alpaca-claude-trader")
def cli() -> None:
    """Alpaca trading CLI - JSON output only."""
    pass


@cli.command()
def account() -> None:
    """Show account information."""
    broker = get_broker()
    acct = broker.get_account()

    result = {
        "id": acct.id,
        "account_number": acct.account_number,
        "buying_power": float(acct.buying_power),
        "cash": float(acct.cash),
        "portfolio_value": float(acct.portfolio_value),
        "equity": float(acct.equity),
        "long_market_value": float(acct.long_market_value),
        "short_market_value": float(acct.short_market_value),
        "status": acct.status,
        "currency": acct.currency,
        "daytrade_count": acct.daytrade_count,
        "trading_blocked": acct.trading_blocked,
        "transfers_blocked": acct.transfers_blocked,
        "account_blocked": acct.account_blocked,
        "created_at": acct.created_at.isoformat() if acct.created_at else None,
    }
    json_output(result)


@cli.command()
def positions() -> None:
    """List open positions."""
    broker = get_broker()
    pos_list = broker.get_all_positions()

    result = []
    for p in pos_list:
        result.append({
            "symbol": p.symbol,
            "qty": float(p.qty),
            "side": p.side,
            "market_value": float(p.market_value),
            "cost_basis": float(p.cost_basis),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc),
            "current_price": float(p.current_price),
            "entry_price": float(p.avg_entry_price),
        })
    json_output(result)


@cli.command()
@click.argument("symbol")
@click.option("--tf", default="1Hour", help="Timeframe (1Min, 5Min, 15Min, 1Hour, 1Day)")
@click.option("--n", default=500, type=int, help="Number of bars")
def bars(symbol: str, tf: str, n: int) -> None:
    """Fetch historical bars for a symbol."""
    client = get_data_client()

    tf_map: dict[str, TimeFrame] = {
        "1Min": TimeFrame(amount=1, unit=TimeFrameUnit.Minute),
        "5Min": TimeFrame(amount=5, unit=TimeFrameUnit.Minute),
        "15Min": TimeFrame(amount=15, unit=TimeFrameUnit.Minute),
        "1Hour": TimeFrame(amount=1, unit=TimeFrameUnit.Hour),
        "1Day": TimeFrame(amount=1, unit=TimeFrameUnit.Day),
    }

    if tf not in tf_map:
        raise click.ClickException(f"Invalid timeframe. Use: {', '.join(tf_map)}")

    timeframe = tf_map[tf]
    end = datetime.now(UTC)
    start = end - timedelta(days=30)  # Alpaca will return up to n bars

    bars_data = client.get_crypto_bars(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        limit=n,
    )

    if isinstance(bars_data, dict):
        bars_data = bars_data.get(symbol, [])

    result = []
    for bar in bars_data:
        result.append({
            "timestamp": bar.timestamp.isoformat(),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": int(bar.volume),
            "vwap": float(bar.vwap) if bar.vwap else None,
            "trade_count": int(bar.trade_count) if bar.trade_count else None,
        })

    json_output(result[-n:])


@cli.command()
@click.argument("symbol")
def quote(symbol: str) -> None:
    """Get current quote for a symbol."""
    client = get_data_client()

    quotes = client.get_crypto_latest_quote(symbol_or_symbols=symbol)

    if isinstance(quotes, dict):
        quote = quotes.get(symbol)
    else:
        quote = quotes

    if quote is None:
        raise click.ClickException(f"No quote found for {symbol}")

    result = {
        "symbol": symbol,
        "bid_price": float(quote.bid_price) if quote.bid_price else None,
        "ask_price": float(quote.ask_price) if quote.ask_price else None,
        "bid_size": float(quote.bid_size) if quote.bid_size else None,
        "ask_size": float(quote.ask_size) if quote.ask_size else None,
        "timestamp": quote.timestamp.isoformat() if quote.timestamp else None,
    }
    json_output(result)


@cli.command()
@click.argument("symbol")
@click.option("--qty", type=float, required=True, help="Quantity to buy")
@click.option("--notional", type=float, help="Dollar amount to buy")
@click.option("--type", default="market", help="Order type (market, limit)")
@click.option("--limit-price", type=float, help="Limit price (for limit orders)")
def buy(symbol: str, qty: float, notional: float | None, type: str, limit_price: float | None) -> None:
    """Submit buy order."""
    from alpaca.trading import OrderSide, OrderType, TimeInForce

    broker = get_broker()

    side = OrderSide.BUY
    order_type = OrderType.MARKET if type == "market" else OrderType.LIMIT

    kwargs: dict = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "time_in_force": TimeInForce.IOC,
    }

    if notional is not None:
        kwargs["notional"] = notional
    else:
        kwargs["qty"] = qty

    if type == "limit" and limit_price:
        kwargs["limit_price"] = limit_price

    try:
        order = broker.submit_order(**kwargs)
        result = {
            "id": order.id,
            "symbol": order.symbol,
            "qty": float(order.qty) if order.qty else None,
            "notional": float(order.notional) if order.notional else None,
            "side": order.side.value,
            "type": order.type.value,
            "status": order.status.value,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        }
        json_output(result)
    except APIError as e:
        raise click.ClickException(f"Order failed: {e}")


@cli.command()
@click.argument("symbol")
@click.option("--qty", type=float, required=True, help="Quantity to sell")
@click.option("--notional", type=float, help="Dollar amount to sell")
@click.option("--type", default="market", help="Order type (market, limit)")
@click.option("--limit-price", type=float, help="Limit price (for limit orders)")
def sell(symbol: str, qty: float, notional: float | None, type: str, limit_price: float | None) -> None:
    """Submit sell order."""
    from alpaca.trading import OrderSide, OrderType, TimeInForce

    broker = get_broker()

    side = OrderSide.SELL
    order_type = OrderType.MARKET if type == "market" else OrderType.LIMIT

    kwargs: dict = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "time_in_force": TimeInForce.IOC,
    }

    if notional is not None:
        kwargs["notional"] = notional
    else:
        kwargs["qty"] = qty

    if type == "limit" and limit_price:
        kwargs["limit_price"] = limit_price

    try:
        order = broker.submit_order(**kwargs)
        result = {
            "id": order.id,
            "symbol": order.symbol,
            "qty": float(order.qty) if order.qty else None,
            "notional": float(order.notional) if order.notional else None,
            "side": order.side.value,
            "type": order.type.value,
            "status": order.status.value,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        }
        json_output(result)
    except APIError as e:
        raise click.ClickException(f"Order failed: {e}")


@cli.command()
@click.option("--all", is_flag=True, help="Cancel all open orders")
@click.argument("order_id", required=False, default=None)
def cancel(all: bool, order_id: str | None) -> None:
    """Cancel orders."""
    broker = get_broker()

    if all:
        canceled = broker.cancel_all_orders()
        json_output({"canceled_count": len(canceled) if canceled else 0})
    elif order_id:
        try:
            broker.cancel_order_by_id(order_id)
            json_output({"canceled": order_id})
        except APIError as e:
            raise click.ClickException(f"Cancel failed: {e}")
    else:
        # List orders instead
        orders = broker.get_orders()
        result = []
        for o in orders:
            result.append({
                "id": o.id,
                "symbol": o.symbol,
                "qty": float(o.qty) if o.qty else None,
                "side": o.side.value,
                "type": o.type.value,
                "status": o.status.value,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            })
        json_output(result)


if __name__ == "__main__":
    cli()
