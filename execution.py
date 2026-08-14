# ==============================================================================
# EXECUTION.PY - Order Execution Engine for Binance Testnet
# ==============================================================================
import json
import os
from binance.client import Client
from binance.exceptions import BinanceAPIException
from config import API_KEY, SECRET_KEY, TRADE_QTY, TRADING_MODE
from logger import log_trade

if TRADING_MODE == "PAPER":
    client = None
else:
    client = Client(API_KEY, SECRET_KEY, testnet=True)
ACTIVE_TRADES_FILE = "active_trades.json"

def _load_active_trades():
    if not os.path.exists(ACTIVE_TRADES_FILE):
        return []
    with open(ACTIVE_TRADES_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return []

def _save_active_trades(trades):
    temp_file = ACTIVE_TRADES_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(trades, f)
    os.replace(temp_file, ACTIVE_TRADES_FILE)

def get_open_orders(symbol):
    """Returns the count of locally tracked active trades for a symbol."""
    active_trades = _load_active_trades()
    count = sum(1 for t in active_trades if t["symbol"] == symbol)
    return count

def place_market_order(strategy_name, side, symbol, quantity=TRADE_QTY, sl=None, tp=None):
    """Places a market order and immediately sets SL/TP via an OCO order."""
    if TRADING_MODE == "PAPER":
        raise RuntimeError("CRITICAL ERROR: PAPER mode attempted to place a real Binance order.")
        
    if os.environ.get("RESEARCH_MODE") == "1":
        raise RuntimeError("CRITICAL ERROR: Real execution attempted from a research script.")
        
    try:
        # 1. Place the entry Market order
        order_side = Client.SIDE_BUY if side == "BUY" else Client.SIDE_SELL
        order = client.create_order(
            symbol=symbol,
            side=order_side,
            type=Client.ORDER_TYPE_MARKET,
            quantity=quantity
        )
        order_id = order.get("orderId", "N/A")
        price = float(order.get("fills", [{}])[0].get("price", 0)) if order.get("fills") else 0
        print(f"[{strategy_name}] ✅ {side} order placed! Price: {price:.2f}")

        oco_id = None
        # 2. Place the OCO order for Take Profit and Stop Loss
        if sl and tp:
            oco_side = Client.SIDE_SELL if side == "BUY" else Client.SIDE_BUY
            tp_price = f"{tp:.2f}"
            sl_price = f"{sl:.2f}"
            
            try:
                oco_order = client.create_oco_order(
                    symbol=symbol,
                    side=oco_side,
                    quantity=quantity,
                    price=tp_price,            # Take profit price
                    stopPrice=sl_price,        # Stop loss trigger
                    stopLimitPrice=sl_price,   # Stop loss execution price
                    stopLimitTimeInForce=Client.TIME_IN_FORCE_GTC
                )
                oco_id = oco_order.get("orderListId")
                print(f"[{strategy_name}] 🛡️  SL/TP OCO order placed! SL: {sl_price} | TP: {tp_price}")
                
                # Save to active trades for monitoring
                active = _load_active_trades()
                active.append({
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "entry_price": price,
                    "oco_id": oco_id,
                    "tp_price": tp,
                    "sl_price": sl
                })
                _save_active_trades(active)
            except BinanceAPIException as e:
                print(f"[EXEC] 🚨 CRITICAL: OCO Order Failed! Attempting emergency close. Error: {e}")
                # EMERGENCY CLOSE: Close the unprotected market position immediately
                try:
                    close_side = Client.SIDE_SELL if side == "BUY" else Client.SIDE_BUY
                    client.create_order(
                        symbol=symbol,
                        side=close_side,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=quantity
                    )
                    print(f"[{strategy_name}] 🛡️ Emergency close successful.")
                except Exception as ce:
                    print(f"[EXEC] 🚨 FATAL: Emergency close failed! Unprotected position active. Error: {ce}")
                return None

        log_trade(strategy_name, symbol, side, quantity, price, sl, tp, order_id, "FILLED")
        return order
    except BinanceAPIException as e:
        print(f"[EXEC] ❌ Binance API Error placing entry order: {e}")
        return None
    except Exception as e:
        print(f"[EXEC] ❌ Unexpected error placing entry order: {e}")
        return None

def monitor_open_trades():
    """Checks active OCO orders to see if SL or TP was hit."""
    if TRADING_MODE == "PAPER":
        return

    active = _load_active_trades()
    if not active:
        return
        
    remaining_trades = []
    
    for t in active:
        try:
            oco = client.get_oco_order(orderListId=t["oco_id"])
            status = oco.get("listOrderStatus")
            
            if status in ["ALL_DONE", "DONE"]:
                # The OCO was triggered. Let's find out if it was TP or SL
                tp_filled = False
                sl_filled = False
                close_price = 0
                
                for o in oco.get("orders", []):
                    details = client.get_order(symbol=t["symbol"], orderId=o["orderId"])
                    if details["status"] == "FILLED":
                        close_price = float(details.get("price", 0))
                        if details["type"] == "STOP_LOSS_LIMIT":
                            sl_filled = True
                        else:
                            tp_filled = True
                
                result = "WIN" if tp_filled else "LOSS"
                pnl = (close_price - t["entry_price"]) * t["quantity"] if t["side"] == "BUY" else (t["entry_price"] - close_price) * t["quantity"]
                
                print(f"[MONITOR] Trade Closed: {result}! PnL: ${pnl:.2f}")
                # Log to CSV
                log_trade(t["strategy"], t["symbol"], f"{t['side']}_CLOSE_{result}", t["quantity"], close_price, t["sl_price"], t["tp_price"], t["oco_id"], f"CLOSED_{result}")
            
            elif status in ["REJECT", "CANCELED", "EXPIRED"]:
                print(f"[MONITOR] 🚨 OCO Order {t['oco_id']} was {status}! Position may be unprotected.")
                log_trade(t["strategy"], t["symbol"], f"{t['side']}_UNKNOWN_{status}", t["quantity"], t["entry_price"], t["sl_price"], t["tp_price"], t["oco_id"], f"OCO_{status}")
                # Do not keep it in remaining trades, but user needs to manually resolve
            
            else:
                remaining_trades.append(t)
        except BinanceAPIException as e:
            if "Order does not exist" in str(e):
                 print(f"[MONITOR] OCO {t['oco_id']} missing from exchange. Purging from local state.")
                 log_trade(t["strategy"], t["symbol"], f"{t['side']}_UNKNOWN", t["quantity"], t["entry_price"], t["sl_price"], t["tp_price"], t["oco_id"], "MISSING_EXCHANGE")
            else:
                 remaining_trades.append(t)
        except Exception as e:
            print(f"[MONITOR] Error checking OCO {t['oco_id']}: {e}")
            remaining_trades.append(t)
            
    _save_active_trades(remaining_trades)

def get_account_balance():
    """Returns the USDT and BTC balance from testnet account."""
    if TRADING_MODE == "PAPER":
        return {"USDT": 10000.0}

    try:
        account = client.get_account()
        balances = {b["asset"]: float(b["free"]) for b in account["balances"] if float(b["free"]) > 0}
        return balances
    except Exception as e:
        print(f"[EXEC] Error fetching balance: {e}")
        return {}
