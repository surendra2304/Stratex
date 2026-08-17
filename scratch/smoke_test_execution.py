import os
import sys
import time
import math
from pprint import pprint

os.environ["SMOKE_TEST_OVERRIDE"] = "TRUE"

from config import TRADING_MODE
import config

if TRADING_MODE != "TESTNET":
    print("CRITICAL: This test can ONLY be run in TESTNET mode.")
    sys.exit(1)

from execution import get_exchange_client
from binance.client import Client

def format_quantity(qty, step_size):
    """Formats quantity to exact string precision based on stepSize to avoid float precision errors."""
    precision = 0
    if '.' in str(step_size):
        precision = len(str(step_size).rstrip('0').split('.')[1])
    return f"{qty:.{precision}f}"

def run_smoke_test():
    print("=== BINANCE TESTNET EXECUTION SMOKE TEST ===")
    
    client = get_exchange_client()
    if not client:
        print("Failed to instantiate Binance client.")
        sys.exit(1)
        
    print("1. Client instantiated successfully.")
    
    # 2. Account Balance
    try:
        account = client.get_account()
        usdt_balance = next((item for item in account['balances'] if item['asset'] == 'USDT'), None)
        free_usdt = float(usdt_balance['free']) if usdt_balance else 0.0
        print(f"2. Available USDT: {free_usdt}")
    except Exception as e:
        print(f"Failed to fetch account: {e}")
        sys.exit(1)
        
    if free_usdt < 20:
        print("Insufficient Testnet USDT for smoke test.")
        sys.exit(1)
        
    # 3. Symbol Validation
    symbol = "LINKUSDT"
    print(f"3. Fetching Exchange Info for {symbol}...")
    try:
        info = client.get_exchange_info()
        symbol_info = next((s for s in info['symbols'] if s['symbol'] == symbol), None)
        
        min_qty = 0.0
        step_size = 1.0
        min_notional = 10.0
        
        for f in symbol_info['filters']:
            if f['filterType'] == 'LOT_SIZE':
                min_qty = float(f['minQty'])
                step_size = float(f['stepSize'])
            elif f['filterType'] == 'NOTIONAL':
                min_notional = float(f['minNotional'])
                
        print(f"   LOT_SIZE: minQty={min_qty}, stepSize={step_size}")
        print(f"   NOTIONAL: minNotional={min_notional}")
    except Exception as e:
        print(f"Failed to fetch exchange info: {e}")
        sys.exit(1)
        
    # 4. Order Parameters
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        price = float(ticker['price'])
        
        target_notional = max(min_notional * 1.5, 15.0) # $15 test order
        target_qty = target_notional / price
        
        # Floor to step size
        qty = math.floor(target_qty / step_size) * step_size
        qty_str = format_quantity(qty, step_size)
        
        actual_notional = float(qty_str) * price
        
        print(f"4. Target Order: Symbol={symbol}, Price={price}, Qty={qty_str}, Notional={actual_notional}")
        
        if actual_notional < min_notional:
            print("Calculated notional is too low.")
            sys.exit(1)
            
    except Exception as e:
        print(f"Failed to prep order: {e}")
        sys.exit(1)
        
    # 5. Execute BUY
    print("5. Submitting MARKET BUY order...")
    try:
        order = client.create_order(
            symbol=symbol,
            side=Client.SIDE_BUY,
            type=Client.ORDER_TYPE_MARKET,
            quantity=qty_str
        )
        print("   SUCCESS! Order response:")
        print(f"   OrderID: {order.get('orderId')}")
        print(f"   Status: {order.get('status')}")
        
        fills = order.get('fills', [])
        executed_qty = float(order.get('executedQty', 0))
        cummulative_quote_qty = float(order.get('cummulativeQuoteQty', 0))
        actual_price = cummulative_quote_qty / executed_qty if executed_qty > 0 else 0
        
        print(f"   Fills: {len(fills)}")
        print(f"   Executed Qty: {executed_qty}")
        print(f"   Avg Price: {actual_price}")
        
    except Exception as e:
        import traceback
        print(f"EXECUTION BUY FAILED: {e}")
        traceback.print_exc()
        sys.exit(1)
        
    time.sleep(2)
    
    # 6. Verify Position and Sell
    print("6. Verifying position and executing MARKET SELL...")
    try:
        # Check actual balance
        account = client.get_account()
        asset_balance = next((item for item in account['balances'] if item['asset'] == symbol.replace('USDT', '')), None)
        free_asset = float(asset_balance['free']) if asset_balance else 0.0
        print(f"   Free {symbol.replace('USDT', '')}: {free_asset}")
        
        sell_qty = math.floor(free_asset / step_size) * step_size
        sell_qty_str = format_quantity(sell_qty, step_size)
        
        print(f"   Submitting MARKET SELL for {sell_qty_str}...")
        sell_order = client.create_order(
            symbol=symbol,
            side=Client.SIDE_SELL,
            type=Client.ORDER_TYPE_MARKET,
            quantity=sell_qty_str
        )
        
        print("   SUCCESS! Sell response:")
        print(f"   OrderID: {sell_order.get('orderId')}")
        print(f"   Status: {sell_order.get('status')}")
        
    except Exception as e:
        print(f"EXECUTION SELL FAILED: {e}")
        sys.exit(1)
        
    print("=== SMOKE TEST COMPLETE: PASS ===")

if __name__ == "__main__":
    run_smoke_test()
