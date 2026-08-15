import json
import os
import shutil
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from config import API_KEY, SECRET_KEY, TRADE_QTY, TRADING_MODE, PAPER_SAFE_MODE, TESTNET_ENABLED, LIVE_TRADING_ENABLED
from logger import log_trade, get_logger
from enum import Enum
from paper_engine.exceptions import StateCorruptionError

sys_logger = get_logger("execution")
ACTIVE_TRADES_FILE = os.getenv("ACTIVE_TRADES_FILE", "active_trades.json")

class OrderState(str, Enum):
    SIGNAL = "SIGNAL"
    APPROVED = "APPROVED"
    ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
    ENTRY_FILLED = "ENTRY_FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTION_FAILED = "PROTECTION_FAILED"
    PROTECTED = "PROTECTED"
    CLOSING = "CLOSING"
    EMERGENCY_CLOSE = "EMERGENCY_CLOSE"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

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
# CLIENT INITIALIZATION
# ==============================================================================
def get_exchange_client():
    """Lazily evaluates ExecutionPolicy to construct and return the Binance Client."""
    if TRADING_MODE == "PAPER":
        return None
        
    allowed, reason = ExecutionPolicy.can_place_order()
    
    if not allowed:
        if "TESTNET_DISABLED" in reason:
            raise RuntimeError("CRITICAL ERROR: TESTNET execution attempted but TESTNET_ENABLED is false.")
        if "LIVE_DISABLED" in reason:
            raise RuntimeError("CRITICAL ERROR: LIVE execution attempted but LIVE_TRADING_ENABLED is false.")
        if "RESEARCH_BLOCKED" in reason:
            return None # Must return None so data.py doesn't crash on import, but client won't be created
        if "PAPER_BLOCKED" in reason:
            return None # Safe to return None in Paper path
            
        raise RuntimeError(f"CRITICAL ERROR: Client creation blocked. ({reason})")

    if TRADING_MODE == "TESTNET":
        return Client(API_KEY, SECRET_KEY, testnet=True)
    elif TRADING_MODE == "LIVE":
        return Client(API_KEY, SECRET_KEY)
        
    return None

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
def _validate_trade_schema(trade: dict):
    required_fields = ["strategy", "symbol", "side", "quantity", "entry_price", "oco_id", "tp_price", "sl_price", "state", "entry_client_id"]
    for field in required_fields:
        if field not in trade:
            raise StateCorruptionError(f"Missing required field '{field}' in active trade.")
    
    if not isinstance(trade["strategy"], str) or not trade["strategy"].strip():
        raise StateCorruptionError("Invalid strategy: must be a non-empty string.")
        
    if not isinstance(trade["symbol"], str) or not trade["symbol"].strip():
        raise StateCorruptionError("Invalid symbol: must be a non-empty string.")
        
    if trade["side"] not in ["BUY", "SELL"]:
        raise StateCorruptionError(f"Invalid side '{trade['side']}' in active trade.")
        
    try:
        qty = float(trade["quantity"])
        if not math.isfinite(qty) or qty <= 0:
            raise StateCorruptionError(f"Invalid quantity {trade['quantity']} in active trade.")
    except (ValueError, TypeError):
        raise StateCorruptionError("Quantity must be a positive finite number.")
        
    try:
        ep = float(trade["entry_price"])
        if not math.isfinite(ep) or ep <= 0:
            raise StateCorruptionError(f"Invalid entry_price {trade['entry_price']}.")
    except (ValueError, TypeError):
        raise StateCorruptionError("entry_price must be a positive finite number.")
        
    for p_field in ["tp_price", "sl_price"]:
        val = trade[p_field]
        if val is not None:
            try:
                f_val = float(val)
                if not math.isfinite(f_val) or f_val <= 0:
                    raise StateCorruptionError(f"Invalid {p_field} {val}.")
            except (ValueError, TypeError):
                raise StateCorruptionError(f"Field {p_field} must be a positive finite number or None.")
                
    if trade.get("oco_id") is None:
        if trade.get("tp_price") is not None or trade.get("sl_price") is not None:
            raise StateCorruptionError("oco_id cannot be None if tp_price or sl_price are set.")

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
        if t["oco_id"] is not None:
            if t["oco_id"] in seen_ids:
                raise StateCorruptionError(f"Duplicate OCO ID {t['oco_id']} found in active trades.")
            seen_ids.add(t["oco_id"])
        
        if t.get("entry_client_id"):
            if t["entry_client_id"] in seen_ids:
                raise StateCorruptionError(f"Duplicate Client ID {t['entry_client_id']} found in active trades.")
            seen_ids.add(t["entry_client_id"])
            
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
    # We DO NOT catch StateCorruptionError here. It must propagate.
    active_trades = _load_active_trades()
    count = sum(1 for t in active_trades if t["symbol"] == symbol)
    return count

# ==============================================================================
# EXECUTION
# ==============================================================================
def place_market_order(strategy_name, side, symbol, quantity=TRADE_QTY, sl=None, tp=None, client_order_id=None):
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
        active_trades = _load_active_trades()
        if client_order_id:
            for t in active_trades:
                if t.get("entry_client_id") == client_order_id:
                    sys_logger.warning(f"[{strategy_name}] 🚫 Duplicate Client ID {client_order_id} rejected.")
                    return None
    except StateCorruptionError as e:
        sys_logger.critical(f"State corruption prevents new orders: {e}")
        return None

    client = get_exchange_client()
    state = OrderState.ENTRY_SUBMITTED

    try:
        # 1. Place the entry Market order
        order_side = Client.SIDE_BUY if side == "BUY" else Client.SIDE_SELL
        order_params = {
            "symbol": symbol,
            "side": order_side,
            "type": Client.ORDER_TYPE_MARKET,
            "quantity": quantity
        }
        if client_order_id:
            order_params["newClientOrderId"] = client_order_id

        order = client.create_order(**order_params)
        
        order_id = order.get("orderId", "N/A")
        
        # Calculate precise actual entry price and executed quantity from fills
        fills = order.get("fills", [])
        executed_qty = float(order.get("executedQty", 0))
        cummulative_quote_qty = float(order.get("cummulativeQuoteQty", 0))
        
        actual_price = 0
        total_fee = 0
        if executed_qty > 0:
            actual_price = cummulative_quote_qty / executed_qty
            for fill in fills:
                total_fee += float(fill.get("commission", 0))
                
            if float(order.get("origQty", quantity)) > executed_qty:
                state = OrderState.PARTIALLY_FILLED
            else:
                state = OrderState.ENTRY_FILLED
        else:
            state = OrderState.FAILED
            sys_logger.warning(f"[{strategy_name}] ⚠️ Zero fill for entry order {order_id}.")
            return None

        sys_logger.info(f"[{strategy_name}] ✅ {side} order {state}! Avg Price: {actual_price:.2f}, Executed Qty: {executed_qty}, Fees: {total_fee}", extra={"strategy": strategy_name, "symbol": symbol})

        oco_id = None
        # 2. Place the OCO order for Take Profit and Stop Loss
        if sl and tp:
            state = OrderState.PROTECTION_PENDING
            oco_side = Client.SIDE_SELL if side == "BUY" else Client.SIDE_BUY
            
            try:
                # Recalculate SL/TP relative to the actual fill price to prevent relationship errors due to slippage
                # If BUY (Long), SL < entry, TP > entry.
                # If SELL (Short), SL > entry, TP < entry.
                # Original risk distances:
                # (We don't know the exact entry the strategy used, but we know SL/TP are absolute.)
                # However, since the strategy passes absolute SL/TP, if we filled worse than SL, it's immediately broken.
                # Let's adjust them by the slippage amount.
                # Wait, if we just adjust by slippage, it's safer. Or we can just calculate the percentage.
                # Actually, the simplest fix is to just check if SL/TP are on the correct side of actual_price, and if not, push them out.
                if side == "BUY":
                    if sl >= actual_price: sl = actual_price * 0.99
                    if tp <= actual_price: tp = actual_price * 1.01
                else:
                    if sl <= actual_price: sl = actual_price * 1.01
                    if tp >= actual_price: tp = actual_price * 0.99

                # Fetch tick size to ensure proper precision
                info = client.get_symbol_info(symbol)
                price_filter = next((f for f in info['filters'] if f['filterType'] == 'PRICE_FILTER'), None)
                tick_size = float(price_filter['tickSize']) if price_filter else 0.01
                precision = max(0, int(round(-math.log10(tick_size))))
                
                tp_price = f"{tp:.{precision}f}"
                sl_price = f"{sl:.{precision}f}"
                
                oco_params = {
                    "symbol": symbol,
                    "side": oco_side,
                    "quantity": executed_qty,
                }
                
                if side == "BUY":
                    # Closing a LONG: side=SELL.
                    # Limit Maker (Take Profit) is above current price -> `price`
                    # Stop Loss is below current price -> `stopPrice` & `stopLimitPrice`
                    oco_params.update({
                        "price": tp_price,
                        "stopPrice": sl_price,
                        "stopLimitPrice": sl_price,
                        "stopLimitTimeInForce": Client.TIME_IN_FORCE_GTC
                    })
                else:
                    # Closing a SHORT: side=BUY.
                    # Limit Maker (Take Profit) is below current price -> `price`
                    # Stop Loss is above current price -> `stopPrice` & `stopLimitPrice`
                    oco_params.update({
                        "price": tp_price,
                        "stopPrice": sl_price,
                        "stopLimitPrice": sl_price,
                        "stopLimitTimeInForce": Client.TIME_IN_FORCE_GTC
                    })

                oco_order = client.create_oco_order(**oco_params)
                oco_id = oco_order.get("orderListId")
                
                sys_logger.info(f"[{strategy_name}] 🛡️  SL/TP OCO order placed successfully! SL: {sl_price} | TP: {tp_price}", extra={"strategy": strategy_name, "symbol": symbol})
                state = OrderState.PROTECTED
                
                # Save to active trades for monitoring
                active = _load_active_trades()
                active.append({
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "side": side,
                    "quantity": executed_qty,
                    "entry_price": actual_price,
                    "oco_id": oco_id,
                    "tp_price": tp,
                    "sl_price": sl,
                    "state": state,
                    "entry_client_id": client_order_id
                })
                _save_active_trades(active)
            except Exception as e:
                state = OrderState.PROTECTION_FAILED
                sys_logger.error(f"[EXEC] 🚨 CRITICAL: OCO Order Failed! Attempting emergency close. Error: {e}", extra={"strategy": strategy_name, "symbol": symbol})
                # EMERGENCY CLOSE: Close the unprotected market position immediately
                try:
                    close_side = Client.SIDE_SELL if side == "BUY" else Client.SIDE_BUY
                    client.create_order(
                        symbol=symbol,
                        side=close_side,
                        type=Client.ORDER_TYPE_MARKET,
                        quantity=executed_qty
                    )
                    state = OrderState.EMERGENCY_CLOSE
                    sys_logger.info(f"[{strategy_name}] 🛡️ Emergency close successful.", extra={"strategy": strategy_name, "symbol": symbol})
                except Exception as ce:
                    sys_logger.critical(f"[EXEC] 🚨 FATAL: Emergency close failed! Unprotected position active. Error: {ce}", extra={"strategy": strategy_name, "symbol": symbol})
                return None

        log_trade(strategy_name, symbol, side, executed_qty, actual_price, sl, tp, order_id, state)
        # Attach our custom metrics
        order["_actual_price"] = actual_price
        order["_executed_qty"] = executed_qty
        order["_total_fee"] = total_fee
        order["_final_state"] = state
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
        
    client = get_exchange_client()
    remaining_trades = []
    
    for t in active:
        try:
            oco = client.v3_get_order_list(orderListId=t["oco_id"])
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

    client = get_exchange_client()
    try:
        account = client.get_account()
        balances = {b["asset"]: float(b["free"]) for b in account["balances"] if float(b["free"]) > 0}
        return balances
    except Exception as e:
        sys_logger.error(f"[EXEC] Error fetching balance: {e}")
        return {}
