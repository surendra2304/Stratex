import os
import sys
import uuid
import time
import urllib3

# Suppress insecure request warnings for proxy setups
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# FORCE TESTNET MODE BEFORE IMPORTS
os.environ["TRADING_MODE"] = "TESTNET"
os.environ["TESTNET_ENABLED"] = "true"

from config import TRADING_MODE
from execution import place_market_order, get_exchange_client
from testnet_engine.service import TestnetService

def verify_testnet_safety():
    """STRICT CHECK: Absolutely prevent execution on Mainnet."""
    if TRADING_MODE != "TESTNET":
        print(f"[FATAL] TRADING_MODE is {TRADING_MODE}. Smoke test CAN ONLY RUN ON TESTNET.")
        sys.exit(1)
        
    client = get_exchange_client()
    if not client.API_URL.startswith("https://testnet.binance.vision"):
        print("[FATAL] Binance client is NOT pointed to Testnet URL. Aborting.")
        sys.exit(1)
        
    print("[SAFE] Verified Testnet configuration.")
    return client

def run_smoke_test():
    print("==================================================")
    print("   REAL TESTNET EXECUTION SMOKE TEST")
    print("==================================================")
    
    client = verify_testnet_safety()
    
    # We will test on TRXUSDT as it is very cheap
    symbol = "TRXUSDT"
    side = "BUY"
    
    # Get current price
    ticker = client.get_symbol_ticker(symbol=symbol)
    current_price = float(ticker["price"])
    print(f"[{symbol}] Current Price: {current_price}")
    
    # Get exchange info for sizing rules
    info = client.get_symbol_info(symbol)
    filters = {f['filterType']: f for f in info['filters']}
    min_qty = float(filters.get('LOT_SIZE', {}).get('minQty', 1.0))
    step_size = float(filters.get('LOT_SIZE', {}).get('stepSize', 1.0))
    min_notional = float(filters.get('NOTIONAL', {}).get('minNotional', 5.0))
    
    print(f"[{symbol}] Rules - minQty: {min_qty}, minNotional: {min_notional}, stepSize: {step_size}")
    
    # Calculate required quantity to just meet min_notional (with a 5% buffer)
    qty = (min_notional * 1.05) / current_price
    
    # Round to step size
    precision = 0
    if '.' in str(step_size):
        precision = len(str(step_size).rstrip('0').split('.')[1])
    qty_str = f"{qty:.{precision}f}"
    actual_qty = float(qty_str)
    
    print(f"[{symbol}] Target Qty: {actual_qty} (Notional: ${actual_qty * current_price:.2f})")
    
    # 1.5% stop loss, 3.0% take profit
    sl_price = current_price * 0.985
    tp_price = current_price * 1.030
    
    client_oid = f"SMOKE_{uuid.uuid4().hex[:8]}"
    
    print("\n[EXECUTION] Submitting production 'place_market_order'...")
    try:
        order_res = place_market_order(
            strategy_name="smoke_test",
            side=side,
            symbol=symbol,
            quantity=qty_str,
            sl=sl_price,
            tp=tp_price,
            client_order_id=client_oid
        )
        
        if not order_res:
            print("[FAIL] place_market_order returned False. Execution blocked locally.")
            return
            
        print("[SUCCESS] Order filled!")
        print(f"  Order ID: {order_res.get('orderId')}")
        print(f"  Executed Qty: {order_res.get('executedQty')}")
        print(f"  Cumulative Quote Qty: {order_res.get('cummulativeQuoteQty')}")
        
        actual_price = float(order_res.get('cummulativeQuoteQty')) / float(order_res.get('executedQty'))
        print(f"  Average Fill Price: {actual_price:.4f}")
        
    except Exception as e:
        print(f"[FAIL] Execution exception: {e}")
        return
        
    print("\n[VERIFICATION] Checking Open Orders for TP/SL OCO bracket...")
    time.sleep(2)  # Give Binance a moment
    
    open_orders = client.get_open_orders(symbol=symbol)
    oco_orders = [o for o in open_orders if o.get('orderListId', -1) > 0]
    
    if len(oco_orders) >= 2:
        print(f"[SUCCESS] Found {len(oco_orders)} OCO orders protecting position!")
        for o in oco_orders:
            print(f"  Type: {o['type']}, Price: {o['price']}, StopPrice: {o.get('stopPrice', 'N/A')}")
    else:
        print(f"[FAIL] OCO bracket not found! Open orders: {open_orders}")
        
    print("\n[CLEANUP] Closing position...")
    # Cancel all open orders for symbol
    for o in open_orders:
        try:
            client.cancel_order(symbol=symbol, orderId=o['orderId'])
            print(f"  Cancelled order {o['orderId']}")
        except Exception as e:
            print(f"  Failed to cancel {o['orderId']}: {e}")
            
    # Sell market to close
    try:
        close_res = client.create_order(
            symbol=symbol,
            side="SELL",
            type="MARKET",
            quantity=qty_str
        )
        print(f"[SUCCESS] Position closed. Sell Order ID: {close_res.get('orderId')}")
    except Exception as e:
        print(f"[FAIL] Failed to close position: {e}")
        
    print("\n[SMOKE TEST COMPLETE]")

if __name__ == "__main__":
    run_smoke_test()
