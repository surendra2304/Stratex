# ==============================================================================
# EXECUTION.PY - Order Execution Engine
# ==============================================================================
import json
import os
import shutil
from binance.client import Client
from binance.exceptions import BinanceAPIException
from config import API_KEY, SECRET_KEY, TRADE_QTY, TRADING_MODE, PAPER_SAFE_MODE, TESTNET_ENABLED, LIVE_TRADING_ENABLED
from logger import log_trade, get_logger
from paper_engine.exceptions import StateCorruptionError

sys_logger = get_logger("execution")
ACTIVE_TRADES_FILE = "active_trades.json"

# ==============================================================================
# CLIENT INITIALIZATION
# ==============================================================================
if TRADING_MODE == "PAPER":
    client = None
elif TRADING_MODE == "TESTNET":
    client = Client(API_KEY, SECRET_KEY, testnet=True)
elif TRADING_MODE == "LIVE":
    client = Client(API_KEY, SECRET_KEY)
else:
    client = None

# ==============================================================================
# EXECUTION POLICY
# ==============================================================================
class ExecutionPolicy:
    @staticmethod
    def can_place_order() -> tuple[bool, str]:
        """Returns (is_allowed, reason) for placing a real order."""
        if TRADING_MODE == "PAPER" or PAPER_SAFE_MODE:
            return False, "PAPER_BLOCKED"
            
        if os.environ.get("RESEARCH_MODE") == "1":
            return False, "RESEARCH_BLOCKED"
            
        if TRADING_MODE == "TESTNET":
            if not TESTNET_ENABLED:
                return False, "TESTNET_DISABLED"
            return True, "ALLOWED_TESTNET"
            
        if TRADING_MODE == "LIVE":
            if not LIVE_TRADING_ENABLED:
                return False, "LIVE_DISABLED"
            return True, "ALLOWED_LIVE"
            
        return False, "UNKNOWN_MODE"

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
def _validate_trade_schema(trade: dict):
    required_fields = ["strategy", "symbol", "side", "quantity", "entry_price", "oco_id", "tp_price", "sl_price"]
    for field in required_fields:
        if field not in trade:
            raise StateCorruptionError(f"Missing required field '{field}' in active trade.")
    
    if trade["side"] not in ["BUY", "SELL"]:
        raise StateCorruptionError(f"Invalid side '{trade['side']}' in active trade.")
        
    try:
        qty = float(trade["quantity"])
        if qty <= 0:
            raise StateCorruptionError(f"Invalid quantity {qty} in active trade.")
    except ValueError:
        raise StateCorruptionError("Quantity must be a number.")
        
    for p_field in ["entry_price", "tp_price", "sl_price"]:
        try:
            val = float(trade[p_field])
            if val < 0:
                raise StateCorruptionError(f"Negative price {val} not allowed for {p_field}.")
        except ValueError:
            pass # allow None if not strictly numbers, but actually we require floats or None for sl/tp
            if trade[p_field] is not None:
                raise StateCorruptionError(f"Field {p_field} must be a number.")

def _load_active_trades():
    if not os.path.exists(ACTIVE_TRADES_FILE):
        return []
    
    try:
        with open(ACTIVE_TRADES_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        sys_logger.error(f"Failed to load JSON from {ACTIVE_TRADES_FILE}: {e}")
        raise StateCorruptionError("Active trades JSON is corrupt.")
        
    if not isinstance(data, list):
        raise StateCorruptionError("Active trades state must be a list.")
        
    seen_ids = set()
    for t in data:
        _validate_trade_schema(t)
        if t["oco_id"] in seen_ids and t["oco_id"] is not None:
            raise StateCorruptionError(f"Duplicate OCO ID {t['oco_id']} found in active trades.")
        if t["oco_id"] is not None:
            seen_ids.add(t["oco_id"])
            
    return data

def _save_active_trades(trades):
    if os.path.exists(ACTIVE_TRADES_FILE):
        backup_dir = "backup"
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy(ACTIVE_TRADES_FILE, os.path.join(backup_dir, "active_trades.json.bak"))
        
    temp_file = ACTIVE_TRADES_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(trades, f)
    os.replace(temp_file, ACTIVE_TRADES_FILE)

def get_open_orders(symbol):
    """Returns the count of locally tracked active trades for a symbol."""
    try:
        active_trades = _load_active_trades()
        count = sum(1 for t in active_trades if t["symbol"] == symbol)
        return count
    except StateCorruptionError as e:
        sys_logger.critical(f"State corruption detected in get_open_orders: {e}")
        return 0

# ==============================================================================
# EXECUTION
# ==============================================================================
def place_market_order(strategy_name, side, symbol, quantity=TRADE_QTY, sl=None, tp=None):
    """Places a market order and immediately sets SL/TP via an OCO order."""
    allowed, reason = ExecutionPolicy.can_place_order()
    
    if not allowed:
        if "PAPER" in reason or "SAFE_MODE" in reason:
            raise RuntimeError(f"CRITICAL ERROR: PAPER mode attempted to place a real Binance order. ({reason})")
        if "RESEARCH" in reason:
            raise RuntimeError(f"CRITICAL ERROR: Real execution attempted from a research script. ({reason})")
        if "TESTNET_DISABLED" in reason:
            raise RuntimeError("CRITICAL ERROR: TESTNET execution attempted but TESTNET_ENABLED is false.")
        if "LIVE_DISABLED" in reason:
            raise RuntimeError("CRITICAL ERROR: LIVE execution attempted but LIVE_TRADING_ENABLED is false.")
        raise RuntimeError(f"CRITICAL ERROR: Order blocked. ({reason})")

    try:
        # Validate state BEFORE placing the order to prevent saving into a broken state file
        _load_active_trades()
    except StateCorruptionError as e:
        sys_logger.critical(f"State corruption prevents new orders: {e}")
        return None

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
        sys_logger.info(f"[{strategy_name}] ✅ {side} order placed! Price: {price:.2f}", extra={"strategy": strategy_name, "symbol": symbol})

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
                sys_logger.info(f"[{strategy_name}] 🛡️  SL/TP OCO order placed! SL: {sl_price} | TP: {tp_price}", extra={"strategy": strategy_name, "symbol": symbol})
                
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
                sys_logger.error(f"[EXEC] 🚨 CRITICAL: OCO Order Failed! Attempting emergency close. Error: {e}", extra={"strategy": strategy_name, "symbol": symbol})
                # EMERGENCY CLOSE: Close the unprotected market position immediately
                try:
                    close_side = Client.SIDE_SELL if side == "BUY" else Client.SIDE_BUY
                    client.create_order(
                        symbol=symbol,
                        side=close_side,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=quantity
                    )
                    sys_logger.info(f"[{strategy_name}] 🛡️ Emergency close successful.", extra={"strategy": strategy_name, "symbol": symbol})
                except Exception as ce:
                    sys_logger.critical(f"[EXEC] 🚨 FATAL: Emergency close failed! Unprotected position active. Error: {ce}", extra={"strategy": strategy_name, "symbol": symbol})
                return None

        log_trade(strategy_name, symbol, side, quantity, price, sl, tp, order_id, "FILLED")
        return order
    except BinanceAPIException as e:
        sys_logger.error(f"[EXEC] ❌ Binance API Error placing entry order: {e}", extra={"strategy": strategy_name, "symbol": symbol})
        return None
    except Exception as e:
        sys_logger.error(f"[EXEC] ❌ Unexpected error placing entry order: {e}", extra={"strategy": strategy_name, "symbol": symbol})
        return None

def monitor_open_trades():
    """Checks active OCO orders to see if SL or TP was hit."""
    if TRADING_MODE == "PAPER":
        return

    try:
        active = _load_active_trades()
    except StateCorruptionError as e:
        sys_logger.critical(f"[MONITOR] State corrupted! Cannot monitor trades: {e}")
        return

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
                
                sys_logger.info(f"[MONITOR] Trade Closed: {result}! PnL: ${pnl:.2f}", extra={"strategy": t["strategy"], "symbol": t["symbol"]})
                # Log to CSV
                log_trade(t["strategy"], t["symbol"], f"{t['side']}_CLOSE_{result}", t["quantity"], close_price, t["sl_price"], t["tp_price"], t["oco_id"], f"CLOSED_{result}")
            
            elif status in ["REJECT", "CANCELED", "EXPIRED"]:
                sys_logger.warning(f"[MONITOR] 🚨 OCO Order {t['oco_id']} was {status}! Position may be unprotected.", extra={"strategy": t["strategy"], "symbol": t["symbol"]})
                log_trade(t["strategy"], t["symbol"], f"{t['side']}_UNKNOWN_{status}", t["quantity"], t["entry_price"], t["sl_price"], t["tp_price"], t["oco_id"], f"OCO_{status}")
                # Do not keep it in remaining trades, but user needs to manually resolve
            
            else:
                remaining_trades.append(t)
        except BinanceAPIException as e:
            if "Order does not exist" in str(e):
                 sys_logger.warning(f"[MONITOR] OCO {t['oco_id']} missing from exchange. Purging from local state.", extra={"strategy": t["strategy"], "symbol": t["symbol"]})
                 log_trade(t["strategy"], t["symbol"], f"{t['side']}_UNKNOWN", t["quantity"], t["entry_price"], t["sl_price"], t["tp_price"], t["oco_id"], "MISSING_EXCHANGE")
            else:
                 remaining_trades.append(t)
        except Exception as e:
            sys_logger.error(f"[MONITOR] Error checking OCO {t['oco_id']}: {e}", extra={"strategy": t["strategy"], "symbol": t["symbol"]})
            remaining_trades.append(t)
            
    try:
        _save_active_trades(remaining_trades)
    except Exception as e:
        sys_logger.error(f"[MONITOR] Failed to save active trades: {e}")

def get_account_balance():
    """Returns the USDT and BTC balance from account."""
    if TRADING_MODE == "PAPER":
        return {"USDT": 10000.0}

    try:
        account = client.get_account()
        balances = {b["asset"]: float(b["free"]) for b in account["balances"] if float(b["free"]) > 0}
        return balances
    except Exception as e:
        sys_logger.error(f"[EXEC] Error fetching balance: {e}")
        return {}
