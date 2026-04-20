"""Bybit V5 trading CLI.

All subcommands print JSON to stdout for easy piping.
"""

import json
import os
import sys
from datetime import UTC, datetime

import click
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

load_dotenv()

# Bybit V5 endpoints
TESTNET_URL = "https://api-testnet.bybit.com"
MAINNET_URL = "https://api.bybit.com"

# Timeframe constants matching Binance format
KLINE_INTERVAL_1MINUTE = "1"
KLINE_INTERVAL_5MINUTE = "5"
KLINE_INTERVAL_15MINUTE = "15"
KLINE_INTERVAL_1HOUR = "60"
KLINE_INTERVAL_4HOUR = "240"
KLINE_INTERVAL_1DAY = "D"
KLINE_INTERVAL_1WEEK = "W"


class BybitClient:
    """Wrapper around pybit HTTP client with Binance-compatible interface."""

    def __init__(self, testnet: bool = True, api_key: str | None = None, api_secret: str | None = None):
        """Initialize Bybit client."""
        if testnet and api_key and api_secret:
            self._client = HTTP(api_key=api_key, api_secret=api_secret, demo=True, testnet=True)
        elif testnet:
            self._client = HTTP(demo=True, testnet=True)
        else:
            self._client = HTTP(testnet=False)
        self._testnet = testnet

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> list:
        """Get kline/candlestick data (Binance-compatible)."""
        result = self._client.get_kline(
            category="spot",
            symbol=symbol,
            interval=interval,
            limit=limit,
        )

        klines = []
        if result.get("retCode") == 0 and "result" in result:
            for k in result["result"].get("list", []):
                # Convert to Binance format [timestamp, open, high, low, close, volume, ...]
                klines.append([
                    int(k[0]),  # timestamp
                    k[1],  # open
                    k[2],  # high
                    k[3],  # low
                    k[4],  # close
                    k[5],  # volume
                    int(k[0]) + 900_000,  # close time (approximate for 15m)
                    k[6] if len(k) > 6 else "0",  # quote volume
                    k[8] if len(k) > 8 else "0",  # trades
                ])

            # Reverse to get chronological order (oldest first) like Binance
            klines.reverse()

        return klines

    def get_symbol_ticker(self, symbol: str) -> dict:
        """Get symbol ticker price (Binance-compatible)."""
        result = self._client.get_tickers(category="spot", symbol=symbol)

        if result.get("retCode") == 0 and "result" in result:
            ticker_list = result["result"].get("list", [])
            if ticker_list:
                ticker = ticker_list[0]
                return {
                    "symbol": ticker["symbol"],
                    "price": ticker.get("lastPrice", "0"),
                }

        raise Exception(f"Could not fetch ticker for {symbol}")

    def get_account(self) -> dict:
        """Get account information (Binance-compatible)."""
        result = self._client.get_wallet_balance(accountType="UNIFIED")

        if result.get("retCode") != 0:
            return {"balances": []}

        balances = []
        if "result" in result:
            for coin in result["result"].get("list", [{}])[0].get("coin", []):
                free = float(coin.get("walletBalance", 0))
                locked = float(coin.get("locked", 0))
                total = free + locked

                if total > 0:
                    balances.append({
                        "asset": coin["coin"],
                        "free": str(free),
                        "locked": str(locked),
                        "total": str(total),
                    })

        return {"balances": balances}

    def get_symbol_info(self, symbol: str) -> dict | None:
        """Get symbol trading info (Binance-compatible)."""
        result = self._client.get_instruments_info(category="spot", symbol=symbol)

        if result.get("retCode") == 0 and "result" in result:
            instruments = result["result"].get("list", [])
            if instruments:
                info = instruments[0]
                lot_size = info.get("lotSizeFilter", {})

                filters = []
                if lot_size:
                    filters.append({
                        "filterType": "LOT_SIZE",
                        "minQty": lot_size.get("minOrderQty", "0"),
                        "maxQty": lot_size.get("maxOrderQty", "1000000"),
                        "stepSize": lot_size.get("qtyStep", "0.001"),
                    })

                    filters.append({
                        "filterType": "MIN_NOTIONAL",
                        "minNotional": lot_size.get("minOrderAmount", "5"),
                    })

                return {"filters": filters}

        return None

    def order_market_buy(self, symbol: str, quantity: float) -> dict:
        """Place market buy order (Binance-compatible)."""
        result = self._client.place_order(
            category="spot",
            symbol=symbol,
            side="Buy",
            orderType="Market",
            qty=str(quantity),
        )

        if result.get("retCode") != 0:
            raise Exception(f"Order failed: {result.get('retMsg', 'Unknown error')}")

        order_result = result["result"]

        # Extract fills info if available
        fills = []
        if order_result.get("avgPrice"):
            fills.append({
                "price": order_result.get("avgPrice"),
            })

        return {
            "symbol": symbol,
            "orderId": order_result.get("orderId"),
            "clientOrderId": order_result.get("orderLinkId"),
            "transactTime": int(order_result.get("createdTime", 0)),
            "price": order_result.get("avgPrice", "0"),
            "executedQty": order_result.get("cumExecQty", "0"),
            "cummulativeQuoteQty": order_result.get("cumExecValue", "0"),
            "status": self._map_order_status(order_result.get("orderStatus", "")),
            "side": "BUY",
            "type": "MARKET",
            "fills": fills,
        }

    def order_market_sell(self, symbol: str, quantity: float) -> dict:
        """Place market sell order (Binance-compatible)."""
        result = self._client.place_order(
            category="spot",
            symbol=symbol,
            side="Sell",
            orderType="Market",
            qty=str(quantity),
        )

        if result.get("retCode") != 0:
            raise Exception(f"Order failed: {result.get('retMsg', 'Unknown error')}")

        order_result = result["result"]

        fills = []
        if order_result.get("avgPrice"):
            fills.append({
                "price": order_result.get("avgPrice"),
            })

        return {
            "symbol": symbol,
            "orderId": order_result.get("orderId"),
            "clientOrderId": order_result.get("orderLinkId"),
            "transactTime": int(order_result.get("createdTime", 0)),
            "price": order_result.get("avgPrice", "0"),
            "executedQty": order_result.get("cumExecQty", "0"),
            "cummulativeQuoteQty": order_result.get("cumExecValue", "0"),
            "status": self._map_order_status(order_result.get("orderStatus", "")),
            "side": "SELL",
            "type": "MARKET",
            "fills": fills,
        }

    def create_order(self, symbol: str, side: str, type: str, quantity: float, **kwargs) -> dict:
        """Create generic order (for SL/TP, Binance-compatible)."""
        order_type_map = {
            "STOP_MARKET": "Market",
            "TAKE_PROFIT_MARKET": "Market",
            "STOP_LOSS_LIMIT": "Limit",
            "TAKE_PROFIT_LIMIT": "Limit",
        }

        # Map Binance types to Bybit
        bybit_side = "Buy" if side.upper() == "BUY" else "Sell"

        # Build order params
        order_params: dict = {
            "category": "spot",
            "symbol": symbol,
            "side": bybit_side,
            "orderType": order_type_map.get(type.upper(), type.upper()),
            "qty": str(quantity),
            "timeInForce": "GTC",
        }

        # Handle stop loss / take profit
        if "stopPrice" in kwargs:
            stop_price = str(kwargs["stopPrice"])
            if "STOP" in type.upper():
                order_params["orderType"] = "Market"
                order_params["stopLoss"] = stop_price
                order_params["triggerBy"] = "MarkPrice"
            elif "TAKE_PROFIT" in type.upper():
                order_params["orderType"] = "Market"
                order_params["takeProfit"] = stop_price
                order_params["triggerBy"] = "MarkPrice"

        result = self._client.place_order(**order_params)

        if result.get("retCode") != 0:
            raise Exception(f"Order failed: {result.get('retMsg', 'Unknown error')}")

        order_result = result["result"]
        return {
            "symbol": symbol,
            "orderId": order_result.get("orderId"),
            "clientOrderId": order_result.get("orderLinkId"),
            "transactTime": int(order_result.get("createdTime", 0)),
            "status": self._map_order_status(order_result.get("orderStatus", "")),
            "type": type,
        }

    def cancel_order(self, symbol: str, orderId: str) -> dict:
        """Cancel order (Binance-compatible)."""
        result = self._client.cancel_order(
            category="spot",
            symbol=symbol,
            orderId=orderId,
        )

        if result.get("retCode") != 0:
            raise Exception(f"Cancel failed: {result.get('retMsg', 'Unknown error')}")

        return {"symbol": symbol, "orderId": orderId}

    def get_open_orders(self, symbol: str | None = None) -> list:
        """Get open orders (Binance-compatible)."""
        if symbol:
            result = self._client.get_open_orders(category="spot", symbol=symbol)
        else:
            result = self._client.get_open_orders(category="spot")

        orders = []
        if result.get("retCode") == 0:
            for o in result.get("result", {}).get("list", []):
                orders.append({
                    "symbol": o.get("symbol"),
                    "orderId": o.get("orderId"),
                    "clientOrderId": o.get("orderLinkId"),
                    "price": o.get("price", "0"),
                    "origQty": o.get("qty", "0"),
                    "executedQty": o.get("cumExecQty", "0"),
                    "status": self._map_order_status(o.get("orderStatus", "")),
                    "side": o.get("side"),
                    "type": o.get("orderType"),
                    "time": int(o.get("createdTime", 0)),
                })

        return orders

    def cancel_open_orders(self, symbol: str) -> list:
        """Cancel all open orders for a symbol (Binance-compatible)."""
        result = self._client.cancel_all_orders(category="spot", symbol=symbol)
        return []  # Bybit returns success info, not order list

    def _map_order_status(self, bybit_status: str) -> str:
        """Map Bybit order status to Binance format."""
        status_map = {
            "New": "NEW",
            "PartiallyFilled": "PARTIALLY_FILLED",
            "Filled": "FILLED",
            "Cancelled": "CANCELED",
            "Rejected": "REJECTED",
            "PartiallyFilledCanceled": "EXPIRED",
        }
        return status_map.get(bybit_status, bybit_status)

    def get_tickers(self, category: str = "spot", symbol: str | None = None) -> dict:
        """Get tickers (for CLI)."""
        if symbol:
            return self._client.get_tickers(category=category, symbol=symbol)
        return self._client.get_tickers(category=category)

    def get_wallet_balance(self, accountType: str = "SPOT") -> dict:
        """Get wallet balance (for CLI)."""
        return self._client.get_wallet_balance(accountType=accountType)

    def get_instruments_info(self, category: str = "spot", symbol: str | None = None) -> dict:
        """Get instruments info (for CLI)."""
        if symbol:
            return self._client.get_instruments_info(category=category, symbol=symbol)
        return self._client.get_instruments_info(category=category)

    def get_open_orders_raw(self, category: str = "spot", symbol: str | None = None) -> dict:
        """Get open orders raw response (for CLI)."""
        if symbol:
            return self._client.get_open_orders(category=category, symbol=symbol)
        return self._client.get_open_orders(category=category)

    def cancel_all_orders_raw(self, category: str = "spot", symbol: str = "") -> dict:
        """Cancel all orders raw response (for CLI)."""
        return self._client.cancel_all_orders(category=category, symbol=symbol)

    def cancel_order_raw(self, category: str = "spot", symbol: str = "", orderId: str = "") -> dict:
        """Cancel order raw response (for CLI)."""
        return self._client.cancel_order(category=category, symbol=symbol, orderId=orderId)

    def place_order(self, **kwargs) -> dict:
        """Place order raw response (for CLI)."""
        return self._client.place_order(**kwargs)

    def get_kline_raw(self, category: str = "spot", symbol: str = "", interval: str = "", limit: int = 200) -> dict:
        """Get kline raw response (for CLI)."""
        return self._client.get_kline(category=category, symbol=symbol, interval=interval, limit=limit)

    # Timeframe constants (class attributes for compatibility)
    KLINE_INTERVAL_1MINUTE = KLINE_INTERVAL_1MINUTE
    KLINE_INTERVAL_5MINUTE = KLINE_INTERVAL_5MINUTE
    KLINE_INTERVAL_15MINUTE = KLINE_INTERVAL_15MINUTE
    KLINE_INTERVAL_1HOUR = KLINE_INTERVAL_1HOUR
    KLINE_INTERVAL_4HOUR = KLINE_INTERVAL_4HOUR
    KLINE_INTERVAL_1DAY = KLINE_INTERVAL_1DAY
    KLINE_INTERVAL_1WEEK = KLINE_INTERVAL_1WEEK


def get_client(mainnet: bool = False) -> BybitClient:
    """Get Bybit V5 client.

    Args:
        mainnet: If True, use mainnet (public data only, no keys needed).
                If False, use testnet (authenticated, for trade execution).
    """
    if mainnet:
        # Public mainnet client — no API keys needed for market data
        return BybitClient(testnet=False)

    api_key = os.getenv("BYBIT_API_KEY")
    api_secret = os.getenv("BYBIT_API_SECRET")

    if not api_key or not api_secret:
        raise click.ClickException("BYBIT_API_KEY and BYBIT_API_SECRET must be set in .env")

    # Bybit Testnet
    return BybitClient(testnet=True, api_key=api_key, api_secret=api_secret)


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
@click.version_option(package_name="claude-trader")
def cli() -> None:
    """Bybit trading CLI - JSON output only."""
    pass


@cli.command()
def account() -> None:
    """Show account information."""
    client = get_client()
    result = client.get_wallet_balance(accountType="UNIFIED")

    # Parse account info
    balances = []
    usdt_free = 0.0

    if result.get("retCode") == 0 and "result" in result:
        for coin in result["result"].get("list", [{}])[0].get("coin", []):
            free = float(coin.get("walletBalance", 0))
            locked = float(coin.get("locked", 0))
            total = free + locked

            if total > 0:
                balances.append({
                    "asset": coin["coin"],
                    "free": free,
                    "locked": locked,
                    "total": total,
                })
                if coin["coin"] == "USDT":
                    usdt_free = free

    output = {
        "usdt_available": usdt_free,
        "balances": balances,
    }
    json_output(output)


@cli.command()
def positions() -> None:
    """List open positions."""
    client = get_client()

    result = client.get_wallet_balance(accountType="UNIFIED")

    positions = []

    if result.get("retCode") == 0 and "result" in result:
        for coin in result["result"].get("list", [{}])[0].get("coin", []):
            free = float(coin.get("walletBalance", 0))
            locked = float(coin.get("locked", 0))
            total = free + locked

            if total > 0:
                positions.append({
                    "asset": coin["coin"],
                    "free": free,
                    "locked": locked,
                    "total": total,
                })

    json_output(positions)


@cli.command()
@click.argument("symbol")
@click.option("--tf", default="1h", help="Timeframe (1m, 5m, 15m, 1h, 4h, 1d)")
@click.option("--n", default=500, type=int, help="Number of bars")
def bars(symbol: str, tf: str, n: int) -> None:
    """Fetch historical klines (bars) for a symbol.

    Symbol format: BTCUSDT (not BTC/USD)
    """
    client = get_client(mainnet=True)

    # Map timeframe strings to Bybit interval constants
    tf_map = {
        "1m": "1",
        "5m": "5",
        "15m": "15",
        "1h": "60",
        "4h": "240",
        "1d": "D",
        "1w": "W",
    }

    if tf not in tf_map:
        raise click.ClickException(f"Invalid timeframe. Use: {', '.join(tf_map)}")

    interval = tf_map[tf]

    # Fetch klines from Bybit V5
    result = client.get_kline_raw(
        category="spot",
        symbol=symbol,
        interval=interval,
        limit=n,
    )

    output = []
    if result.get("retCode") == 0 and "result" in result:
        for k in result["result"].get("list", []):
            # Bybit returns data in reverse chronological order (newest first)
            output.append({
                "timestamp": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[0]) + get_interval_ms(tf) - 1,
                "quote_volume": float(k[6]) if len(k) > 6 else 0.0,
                "trades": int(k[8]) if len(k) > 8 else 0,
            })

        # Reverse to get chronological order (oldest first) like Binance
        output.reverse()

    json_output(output[-n:])


def get_interval_ms(tf: str) -> int:
    """Get interval in milliseconds for timeframe string."""
    tf_ms = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
        "1d": 86_400_000,
        "1w": 604_800_000,
    }
    return tf_ms.get(tf, 60_000)


@cli.command()
@click.argument("symbol")
def price(symbol: str) -> None:
    """Get current price for a symbol.

    Symbol format: BTCUSDT (not BTC/USD)
    """
    client = get_client(mainnet=True)

    result = client.get_tickers(category="spot", symbol=symbol)

    if result.get("retCode") == 0 and "result" in result:
        ticker_list = result["result"].get("list", [])
        if ticker_list:
            ticker = ticker_list[0]
            output = {
                "symbol": ticker["symbol"],
                "price": float(ticker.get("lastPrice", 0)),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            json_output(output)
            return

    raise click.ClickException(f"Could not fetch price for {symbol}")


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
            order = client.place_order(
                category="spot",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=str(qty),
            )
        elif type == "limit":
            if not price:
                raise click.ClickException("Limit orders require --price")
            order = client.place_order(
                category="spot",
                symbol=symbol,
                side="Buy",
                orderType="Limit",
                qty=str(qty),
                price=str(price),
                timeInForce="GTC",
            )
        else:
            raise click.ClickException(f"Invalid order type: {type}")

        if order.get("retCode") != 0:
            raise click.ClickException(f"Order failed: {order.get('retMsg', 'Unknown error')}")

        result = order["result"]
        output = {
            "symbol": result.get("symbol"),
            "order_id": result.get("orderId"),
            "client_order_id": result.get("orderLinkId"),
            "price": float(result.get("avgPrice", 0)) if result.get("avgPrice") else None,
            "qty": float(result.get("qty", 0)) if result.get("qty") else None,
            "status": result.get("orderStatus"),
            "side": result.get("side"),
            "type": result.get("orderType"),
        }
        json_output(output)
    except Exception as e:
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
            order = client.place_order(
                category="spot",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=str(qty),
            )
        elif type == "limit":
            if not price:
                raise click.ClickException("Limit orders require --price")
            order = client.place_order(
                category="spot",
                symbol=symbol,
                side="Sell",
                orderType="Limit",
                qty=str(qty),
                price=str(price),
                timeInForce="GTC",
            )
        else:
            raise click.ClickException(f"Invalid order type: {type}")

        if order.get("retCode") != 0:
            raise click.ClickException(f"Order failed: {order.get('retMsg', 'Unknown error')}")

        result = order["result"]
        output = {
            "symbol": result.get("symbol"),
            "order_id": result.get("orderId"),
            "client_order_id": result.get("orderLinkId"),
            "price": float(result.get("avgPrice", 0)) if result.get("avgPrice") else None,
            "qty": float(result.get("qty", 0)) if result.get("qty") else None,
            "status": result.get("orderStatus"),
            "side": result.get("side"),
            "type": result.get("orderType"),
        }
        json_output(output)
    except Exception as e:
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
        result = client.cancel_all_orders_raw(
            category="spot",
            symbol=symbol,
        )
        json_output({"canceled_count": len(result.get("result", {}).get("list", []))})
    elif order_id:
        if not symbol:
            raise click.ClickException("order_id requires a symbol")
        result = client.cancel_order_raw(
            category="spot",
            symbol=symbol,
            orderId=order_id,
        )
        json_output({"canceled": order_id, "symbol": symbol})
    else:
        # List orders - use the wrapper method that returns a list
        orders = client.get_open_orders(symbol if symbol else None)
        json_output(orders)


if __name__ == "__main__":
    cli()
