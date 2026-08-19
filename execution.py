import json
import os
import shutil
import math
import datetime
import time
from binance.client import Client
from binance.exceptions import BinanceAPIException
from config import API_KEY, SECRET_KEY, TRADE_QTY, TRADING_MODE, PAPER_SAFE_MODE, TESTNET_ENABLED, LIVE_TRADING_ENABLED
from logger import log_trade, get_logger
from paper_engine.exceptions import StateCorruptionError, ZeroFillError
from enum import Enum
from testnet_engine.protection import place_oco_protection, emergency_market_close

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
# EXECUTION POLICY (TESTNET ONLY - LIVE TRADING FORBIDDEN BY DESIGN)
# ==============================================================================
class ExecutionPolicy:
    @staticmethod
    def can_place_order() -> tuple[bool, str]:
        """Returns (is_allowed, reason) for placing a real order. LIVE trading is permanently impossible by design."""
        if TRADING_MODE == "PAPER" or PAPER_SAFE_MODE:
            return False, "PAPER_BLOCKED"
            
        if os.environ.get("RESEARCH_MODE") == "1":
            return False, "RESEARCH_BLOCKED"

        if TRADING_MODE == "TESTNET":
            if not TESTNET_ENABLED:
                return False, "TESTNET_DISABLED"
            return True, "ALLOWED_TESTNET"

        if TRADING_MODE == "LIVE" or LIVE_TRADING_ENABLED:
            return False, "LIVE_FORBIDDEN_BY_DESIGN"
            
        return False, "UNKNOWN_MODE"

# ==============================================================================
# CLIENT INITIALIZATION (TESTNET ONLY)
# ==============================================================================
def get_exchange_client():
    """Lazily evaluates ExecutionPolicy to construct and return the Binance Testnet Client."""
    if TRADING_MODE == "LIVE" or LIVE_TRADING_ENABLED:
        raise RuntimeError("SECURITY CRITICAL: LIVE trading is permanently disabled by design in this repository.")

    if TRADING_MODE == "PAPER":
        return None
        
    allowed, reason = ExecutionPolicy.can_place_order()
    
    if not allowed:
        if "TESTNET_DISABLED" in reason:
            raise RuntimeError("CRITICAL ERROR: TESTNET execution attempted but TESTNET_ENABLED is false.")
        if "LIVE" in reason or "FORBIDDEN" in reason:
            raise RuntimeError("SECURITY CRITICAL: LIVE trading is permanently disabled by design in this repository.")
        if "RESEARCH_BLOCKED" in reason:
            return None # Must return None so data.py doesn't crash on import, but client won't be created
        if "PAPER_BLOCKED" in reason:
            return None # Safe to return None in Paper path
            
        raise RuntimeError(f"CRITICAL ERROR: Client creation blocked. ({reason})")

    if TRADING_MODE == "TESTNET":
        client = Client(API_KEY, SECRET_KEY, testnet=True)
        client.API_URL = "https://testnet.binance.vision/api"
        return client
        
    return None

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
def _validate_trade_schema(trade: dict):
    required_fields = [
        "strategy", "symbol", "side", "quantity", 
        "entry_price", "oco_id", "tp_price", "sl_price", 
        "state", "signal_id", "entry_timestamp"
    ]
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
        if "LIVE" in reason or "FORBIDDEN" in reason:
            raise RuntimeError("SECURITY CRITICAL: LIVE trading is permanently disabled by design in this repository.")
        raise RuntimeError(f"CRITICAL ERROR: Order blocked. ({reason})")

    try:
        active_trades = _load_active_trades()
        if client_order_id:
            for t in active_trades:
                if t.get("signal_id") == client_order_id or t.get("entry_client_id") == client_order_id or t.get("trade_id") == client_order_id or str(t.get("entry_order_id")) == str(client_order_id):
                    sys_logger.warning(f"[{strategy_name}] 🚫 Duplicate Client/Signal ID {client_order_id} rejected.")
                    return None
            # Also check recent ledger records for deduplication
            ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
            if os.path.exists(ledger_file):
                try:
                    with open(ledger_file, "r", encoding="utf-8") as lf:
                        for line in lf:
                            if not line.strip(): continue
                            rec = json.loads(line)
                            if rec.get("signal_id") == client_order_id or rec.get("entry_client_id") == client_order_id or rec.get("trade_id") == client_order_id or str(rec.get("entry_order_id")) == str(client_order_id):
                                sys_logger.warning(f"[{strategy_name}] 🚫 Duplicate signal already executed in ledger: {client_order_id}")
                                return None
                except Exception:
                    pass
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
            raise ZeroFillError(f"Order {order_id} filled 0 quantity.")

        sys_logger.info(
            f"[{strategy_name}] \u2705 {side} order {state}! "
            f"Avg Price: {actual_price:.2f}, Executed Qty: {executed_qty}, Fees: {total_fee}",
            extra={"strategy": strategy_name, "symbol": symbol}
        )

        oco_order_list_id = None
        tp_order_id = None
        sl_order_id = None

        # 2. Place OCO protection (TP + SL) immediately after fill
        if sl and tp:
            state = OrderState.PROTECTION_PENDING
            protection_client_id = f"p-{client_order_id[:33]}" if client_order_id else None  # max 35 chars; Binance limit is 36

            try:
                prot = place_oco_protection(
                    client=client,
                    symbol=symbol,
                    entry_side=side,
                    executed_qty=executed_qty,
                    actual_fill_price=actual_price,
                    sl_price=sl,
                    tp_price=tp,
                    list_client_order_id=protection_client_id,
                )
                oco_order_list_id = prot["oco_order_list_id"]
                tp_order_id       = prot["tp_order_id"]
                sl_order_id       = prot["sl_order_id"]
                state             = OrderState.PROTECTED

                sys_logger.info(
                    f"[PROTECTION_PLACED] [{strategy_name}] {symbol} | "
                    f"OCO_ListId={oco_order_list_id} "
                    f"TP_orderId={tp_order_id} (@ {prot['tp_price_sent']}) "
                    f"SL_orderId={sl_order_id} (@ {prot['sl_price_sent']})",
                    extra={"strategy": strategy_name, "symbol": symbol}
                )

                # Save to active trades including both order IDs and entry fee
                active = _load_active_trades()
                active.append({
                    "strategy":          strategy_name,
                    "symbol":            symbol,
                    "side":              side,
                    "quantity":          executed_qty,
                    "entry_price":       actual_price,
                    "entry_fee":         total_fee,
                    "entry_timestamp":   datetime.datetime.utcnow().isoformat() + "Z",
                    "signal_id":         client_order_id or f"MANUAL_{int(time.time())}",
                    "oco_id":            oco_order_list_id,
                    "tp_order_id":       tp_order_id,
                    "sl_order_id":       sl_order_id,
                    "tp_price":          tp,
                    "sl_price":          sl,
                    "state":             state.value,
                    "entry_client_id":   client_order_id,
                    "entry_order_id":    order_id
                })
                _save_active_trades(active)

            except (BinanceAPIException, ValueError, Exception) as e:
                state = OrderState.PROTECTION_FAILED
                sys_logger.error(
                    f"[PROTECTION_FAILED] [{strategy_name}] {symbol} | "
                    f"Error: {e}. Attempting emergency MARKET close.",
                    extra={"strategy": strategy_name, "symbol": symbol}
                )
                # EMERGENCY CLOSE: verified market close
                try:
                    ec = emergency_market_close(client, symbol, side, executed_qty)
                    ec_qty = float(ec.get("executedQty", 0))
                    state = OrderState.EMERGENCY_CLOSE
                    sys_logger.info(
                        f"[{strategy_name}] Emergency close FILLED: qty={ec_qty}.",
                        extra={"strategy": strategy_name, "symbol": symbol}
                    )
                    log_trade(strategy_name, symbol, f"{side}_EMERGENCY_CLOSE",
                              ec_qty, actual_price, sl, tp, order_id, state)
                except Exception as ce:
                    sys_logger.critical(
                        f"[EXEC] 🚨 FATAL: Emergency close also failed! "
                        f"UNPROTECTED POSITION ACTIVE for {symbol}. Error: {ce}",
                        extra={"strategy": strategy_name, "symbol": symbol}
                    )
                return None

        log_trade(strategy_name, symbol, side, executed_qty, actual_price, sl, tp, order_id, state)
        # Attach our custom metrics
        order["_actual_price"] = actual_price
        order["_executed_qty"] = executed_qty
        order["_total_fee"] = total_fee
        order["_final_state"] = state
        return order
    except BinanceAPIException as e:
        est_price = actual_price if 'actual_price' in locals() and actual_price > 0 else (sl or tp or 0.0)
        sys_logger.error(
            f"[EXECUTION_FAILED] Binance API Error | Code: {e.code} | Message: {e.message} | "
            f"Symbol: {symbol} | Side: {side} | Quantity: {quantity} | Price: {est_price} | "
            f"Order Type: {Client.ORDER_TYPE_MARKET} | Client Order ID: {client_order_id}",
            extra={"strategy": strategy_name, "symbol": symbol, "api_code": e.code, "api_message": e.message}
        )
        raise
    except Exception as e:
        est_price = actual_price if 'actual_price' in locals() and actual_price > 0 else (sl or tp or 0.0)
        sys_logger.error(
            f"[EXECUTION_FAILED] Unexpected Exception: {e} | Symbol: {symbol} | Side: {side} | "
            f"Quantity: {quantity} | Price: {est_price} | Order Type: {Client.ORDER_TYPE_MARKET} | "
            f"Client Order ID: {client_order_id}",
            extra={"strategy": strategy_name, "symbol": symbol}
        )
        raise


def monitor_open_trades():
    """Checks active OCO orders to see if SL or TP was hit. Uses actual fill prices for PnL."""
    if TRADING_MODE == "PAPER":
        return

    from testnet_engine.protection import check_oco_status, compute_net_pnl, LEDGER_WRITE_LOCK
    import datetime

    try:
        active = _load_active_trades()
    except StateCorruptionError as e:
        sys_logger.critical(f"[MONITOR] State corrupted! Cannot monitor trades: {e}")
        return

    if not active:
        return

    client = get_exchange_client()
    remaining_trades = []
    ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")

    for t in active:
        oco_id = t.get("oco_id")
        if not oco_id:
            remaining_trades.append(t)
            continue
        try:
            result = check_oco_status(client, t["symbol"], oco_id)
            status = result["list_status"]

            if status in ("ALL_DONE", "DONE"):
                close_price = result["close_avg_price"]   # actual average fill price
                close_qty   = result["close_qty"]
                tp_filled   = result["tp_filled"]
                sl_filled   = result["sl_filled"]
                outcome     = "WIN" if tp_filled else "LOSS"

                entry_price = float(t.get("entry_price", 0))
                entry_qty   = float(t.get("quantity", close_qty))
                entry_fee   = float(t.get("entry_fee", 0.0))
                # Estimate exit fee at 0.1% of exit notional if not provided
                exit_fee    = close_price * close_qty * 0.001

                gross_pnl, net_pnl = compute_net_pnl(
                    t["side"], entry_qty, entry_price, entry_fee,
                    close_qty, close_price, exit_fee
                )

                sys_logger.info(
                    f"[POSITION_CLOSED] {t['symbol']} {outcome} | Qty: {close_qty} @ {close_price:.4f} (Entry: {entry_price:.4f})",
                    extra={"strategy": t["strategy"], "symbol": t["symbol"]}
                )
                sys_logger.info(
                    f"[PNL_RECORDED] {t['symbol']} | Gross PnL: ${gross_pnl:.4f} | Total Fees: ${(entry_fee + exit_fee):.4f} | Net PnL: ${net_pnl:.4f}",
                    extra={"strategy": t["strategy"], "symbol": t["symbol"]}
                )

                source = t.get("source", "TEST" if t.get("strategy") == "TEST" else "BINANCE_EXECUTION")
                ledger_entry = {
                    "signal_id":      t.get("signal_id", "UNKNOWN"),
                    "symbol":         t["symbol"],
                    "strategy":       t["strategy"],
                    "source":         source,
                    "side":           t["side"],
                    "entry_order_id": t.get("entry_order_id"),
                    "entry_price":    entry_price,
                    "entry_executed_quantity": entry_qty,
                    "entry_fee":      entry_fee,
                    "exit_order_id":  result.get("tp_order_id") if tp_filled else result.get("sl_order_id"),
                    "exit_price":     close_price,
                    "exit_executed_quantity": close_qty,
                    "exit_fee":       exit_fee,
                    "exit_reason":    outcome,
                    "gross_pnl":      gross_pnl,
                    "total_fees":     entry_fee + exit_fee,
                    "net_pnl":        net_pnl,
                    "pnl":            net_pnl,      # dashboard compat
                    "fees":           entry_fee + exit_fee, # dashboard compat
                    "entry_timestamp": t.get("entry_timestamp"),
                    "exit_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "timestamp":      datetime.datetime.utcnow().isoformat() + "Z", # dashboard compat
                    "action":         f"CLOSE_{outcome}", # dashboard compat
                    "quantity":       close_qty, # dashboard compat
                    "oco_id":         oco_id
                }
                # Atomic append to ledger
                with LEDGER_WRITE_LOCK:
                    with open(ledger_file, "a") as lf:
                        lf.write(json.dumps(ledger_entry) + "\n")
                    if t.get("strategy") == "adx_ema":
                        try:
                            with open("adx_ema_forward_ledger.jsonl", "a") as fwd_f:
                                fwd_entry = dict(ledger_entry)
                                fwd_entry["strategy_version"] = "ADX_EMA_4H_V1"
                                fwd_f.write(json.dumps(fwd_entry) + "\n")
                        except Exception:
                            pass

                log_trade(
                    t["strategy"], t["symbol"],
                    f"{t['side']}_CLOSE_{outcome}",
                    close_qty, close_price,
                    t.get("sl_price"), t.get("tp_price"),
                    oco_id, f"CLOSED_{outcome}"
                )
                # Trade is closed — do NOT add to remaining_trades

            elif status in ("REJECT", "CANCELED", "EXPIRED"):
                sys_logger.warning(
                    f"[MONITOR] \U0001f6a8 OCO {oco_id} for {t['symbol']} is {status}! "
                    f"Position may be unprotected. Attempting emergency close.",
                    extra={"strategy": t["strategy"], "symbol": t["symbol"]}
                )
                try:
                    from testnet_engine.protection import emergency_market_close
                    ec = emergency_market_close(
                        client, t["symbol"], t["side"], float(t["quantity"])
                    )
                    ec_qty = float(ec.get("executedQty", 0))
                    ec_price = float(ec.get("cummulativeQuoteQty", 0)) / ec_qty if ec_qty > 0 else 0
                    ec_fee = ec_qty * ec_price * 0.001
                    
                    # Calculate PnL accurately
                    gross_pnl, net_pnl = compute_net_pnl(
                        t["side"], float(t.get("quantity", ec_qty)), float(t.get("entry_price", 0)), 
                        float(t.get("entry_fee", 0)), ec_qty, ec_price, ec_fee
                    )
                    
                    sys_logger.info(
                        f"[MONITOR] Emergency close after OCO {status}: filled {ec_qty}",
                        extra={"strategy": t["strategy"], "symbol": t["symbol"]}
                    )
                    
                    # Log to authoritative ledger
                    ledger_entry = {
                        "signal_id":      t.get("signal_id", "UNKNOWN"),
                        "symbol":         t["symbol"],
                        "strategy":       t["strategy"],
                        "side":           t["side"],
                        "entry_order_id": t.get("entry_order_id"),
                        "entry_price":    float(t.get("entry_price", 0)),
                        "entry_executed_quantity": float(t.get("quantity", ec_qty)),
                        "entry_fee":      float(t.get("entry_fee", 0)),
                        "exit_order_id":  ec.get("orderId", "EMERGENCY"),
                        "exit_price":     ec_price,
                        "exit_executed_quantity": ec_qty,
                        "exit_fee":       ec_fee,
                        "exit_reason":    "EMERGENCY",
                        "gross_pnl":      gross_pnl,
                        "total_fees":     float(t.get("entry_fee", 0)) + ec_fee,
                        "net_pnl":        net_pnl,
                        "pnl":            net_pnl,
                        "fees":           float(t.get("entry_fee", 0)) + ec_fee,
                        "entry_timestamp": t.get("entry_timestamp"),
                        "exit_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        "timestamp":      datetime.datetime.utcnow().isoformat() + "Z",
                        "action":         "EMERGENCY_CLOSE",
                        "quantity":       ec_qty,
                        "reason":         f"OCO_{status}"
                    }
                    with LEDGER_WRITE_LOCK:
                        with open(ledger_file, "a") as lf:
                            lf.write(json.dumps(ledger_entry) + "\n")
                            
                except Exception as ec_err:
                    sys_logger.critical(
                        f"[MONITOR] \U0001f6a8 FATAL: Emergency close failed for {t['symbol']}: {ec_err}",
                        extra={"strategy": t["strategy"], "symbol": t["symbol"]}
                    )
                log_trade(
                    t["strategy"], t["symbol"],
                    f"{t['side']}_OCO_{status}",
                    t["quantity"], t["entry_price"],
                    t.get("sl_price"), t.get("tp_price"),
                    oco_id, f"OCO_{status}"
                )
                # Remove from remaining regardless — OCO is dead
            else:
                # Still executing
                remaining_trades.append(t)

        except BinanceAPIException as e:
            if "Order does not exist" in str(e) or "-2013" in str(e):
                # Check balance to see if we missed a successful exit or if it's orphaned
                try:
                    asset = t['symbol'].replace("USDT", "")
                    asset_info = client.get_asset_balance(asset=asset)
                    asset_bal = float(asset_info['free']) + float(asset_info['locked'])
                    if asset_bal < 0.0001: # Essentially 0
                        sys_logger.warning(f"[MONITOR] OCO {oco_id} missing but balance is 0. Position closed.")
                    else:
                        sys_logger.critical(f"[MONITOR] OCO {oco_id} missing but balance > 0! Attempting emergency close.")
                        from testnet_engine.protection import emergency_market_close
                        emergency_market_close(client, t["symbol"], t["side"], float(t["quantity"]))
                except Exception as balance_err:
                    pass
                
                sys_logger.warning(
                    f"[MONITOR] OCO {oco_id} for {t['symbol']} missing from exchange. Purging.",
                    extra={"strategy": t["strategy"], "symbol": t["symbol"]}
                )
                log_trade(
                    t["strategy"], t["symbol"], f"{t['side']}_UNKNOWN",
                    t["quantity"], t["entry_price"],
                    t.get("sl_price"), t.get("tp_price"),
                    oco_id, "MISSING_EXCHANGE"
                )
            else:
                sys_logger.error(
                    f"[MONITOR] Binance error checking OCO {oco_id}: {e}",
                    extra={"strategy": t["strategy"], "symbol": t["symbol"]}
                )
                remaining_trades.append(t)
        except Exception as e:
            sys_logger.error(
                f"[MONITOR] Error checking OCO {oco_id}: {e}",
                extra={"strategy": t["strategy"], "symbol": t["symbol"]}
            )
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
