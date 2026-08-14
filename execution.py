# ==============================================================================
# EXECUTION.PY - Order Execution Engine for Binance Testnet
# ==============================================================================
from binance.client import Client
from binance.exceptions import BinanceAPIException
from config import API_KEY, SECRET_KEY, SYMBOL, TRADE_QTY
from logger import log_trade

client = Client(API_KEY, SECRET_KEY, testnet=True)

def get_open_orders(symbol=SYMBOL):
    """Returns count of open positions/orders."""
    try:
        orders = client.get_open_orders(symbol=symbol)
        return len(orders)
    except Exception as e:
        print(f"[EXEC] Error fetching open orders: {e}")
        return 0

def place_market_order(strategy_name, side, symbol=SYMBOL, quantity=TRADE_QTY, sl=None, tp=None):
    """Places a market order on Binance Testnet."""
    try:
        order_side = Client.SIDE_BUY if side == "BUY" else Client.SIDE_SELL
        order = client.create_order(
            symbol=symbol,
            side=order_side,
            type=Client.ORDER_TYPE_MARKET,
            quantity=quantity
        )
        order_id = order.get("orderId", "N/A")
        price = float(order.get("fills", [{}])[0].get("price", 0)) if order.get("fills") else 0
        
        print(f"[{strategy_name}] ✅ {side} order placed! OrderID: {order_id} | Price: {price:.2f}")
        log_trade(strategy_name, symbol, side, quantity, price, sl, tp, order_id, "FILLED")
        return order
    except BinanceAPIException as e:
        print(f"[EXEC] ❌ Binance API Error: {e}")
        return None
    except Exception as e:
        print(f"[EXEC] ❌ Unexpected error: {e}")
        return None

def get_account_balance():
    """Returns the USDT and BTC balance from testnet account."""
    try:
        account = client.get_account()
        balances = {b["asset"]: float(b["free"]) for b in account["balances"] if float(b["free"]) > 0}
        return balances
    except Exception as e:
        print(f"[EXEC] Error fetching balance: {e}")
        return {}
