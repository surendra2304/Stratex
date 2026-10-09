import datetime
import json
import math
import os
import time
from enum import Enum

from binance.client import Client
from binance.exceptions import BinanceAPIException

import config
from config import (
    API_KEY,
    LIVE_TRADING_ENABLED,
    PAPER_SAFE_MODE,
    SECRET_KEY,
    TESTNET_ENABLED,
    TRADE_QTY,
    TRADING_MODE,
)
from atomic_io import (
    append_jsonl,
    atomic_write_bytes,
    atomic_write_text,
    load_json_state,
    locked_path,
)
from logger import get_logger, log_trade
from numeric_safety import finite_float, positive_float
from paper_engine.exceptions import StateCorruptionError, ZeroFillError
from testnet_engine.protection import (
    check_futures_bracket_status,
    emergency_futures_market_close,
    emergency_market_close,
    place_futures_bracket_protection,
    place_oco_protection,
)
from audit.audit_manager import get_audit_manager, get_idempotency_store

sys_logger = get_logger("execution")
ACTIVE_TRADES_FILE = os.getenv("ACTIVE_TRADES_FILE", "active_trades.json")


def _check_panic_and_kill_switch():
    """Verifies that neither panic nor emergency kill-switch lock is active.

    Uses the unified panic reader: both schema keys (``active`` written by
    /api/panic, ``panic_active`` written by FRIDAY) block, and an unreadable flag
    file fails CLOSED instead of being silently ignored.
    """
    from panic_state import is_kill_switch_locked, is_panic_active

    if is_panic_active():
        raise RuntimeError("CRITICAL ERROR: Emergency Panic Kill-Switch is active. All order submission is blocked.")
    if is_kill_switch_locked():
        raise RuntimeError("CRITICAL ERROR: Emergency Kill Switch lock file is active. All order submission is blocked.")



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
def _resolve_execution_flags():
    """Resolves execution flags checking both execution globals (for unit test monkeypatching) and config."""
    cfg_mode = getattr(config, "TRADING_MODE", None)
    exec_mode = globals().get("TRADING_MODE", None)
    if exec_mode == "LIVE" or cfg_mode == "LIVE":
        mode = "LIVE"
    elif exec_mode == "PAPER" or cfg_mode == "PAPER":
        mode = "PAPER"
    elif exec_mode == "TESTNET" or cfg_mode == "TESTNET":
        mode = "TESTNET"
    elif exec_mode == "FUTURES" or cfg_mode == "FUTURES":
        mode = "FUTURES"
    else:
        mode = exec_mode or cfg_mode or "UNKNOWN"

    paper_safe = bool(globals().get("PAPER_SAFE_MODE", False) or getattr(config, "PAPER_SAFE_MODE", False))
    live_enabled = bool(globals().get("LIVE_TRADING_ENABLED", False) or getattr(config, "LIVE_TRADING_ENABLED", False))
    
    exec_testnet = globals().get("TESTNET_ENABLED", True)
    cfg_testnet = getattr(config, "TESTNET_ENABLED", True)
    testnet_enabled = bool(exec_testnet and cfg_testnet)
    
    return mode, paper_safe, live_enabled, testnet_enabled


class ExecutionPolicy:
    @staticmethod
    def can_place_order() -> tuple[bool, str]:
        """Returns (is_allowed, reason) for placing a real order. LIVE trading is permanently impossible by design."""
        mode, paper_safe, live_enabled, testnet_enabled = _resolve_execution_flags()
        if mode == "PAPER" or paper_safe:
            return False, "PAPER_BLOCKED"
            
        if os.environ.get("RESEARCH_MODE") == "1":
            return False, "RESEARCH_BLOCKED"

        if mode in ["TESTNET", "FUTURES"]:
            if not testnet_enabled:
                return False, "TESTNET_DISABLED"
            return True, f"ALLOWED_{mode}"

        if mode == "LIVE" or live_enabled:
            return False, "LIVE_FORBIDDEN_BY_DESIGN"

        return False, "UNKNOWN_MODE"

# ==============================================================================
# CLIENT INITIALIZATION (TESTNET ONLY)
# ==============================================================================
def get_exchange_client():
    """Lazily evaluates ExecutionPolicy to construct and return the Binance Testnet Client."""
    mode, paper_safe, live_enabled, testnet_enabled = _resolve_execution_flags()
    if mode == "LIVE" or live_enabled:
        raise RuntimeError("SECURITY CRITICAL: LIVE trading is permanently disabled by design in this repository.")

    if mode == "PAPER":
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

    if mode in ["TESTNET", "FUTURES"]:
        client = Client(API_KEY, SECRET_KEY, testnet=True, ping=False)
        if mode == "TESTNET":
            client.API_URL = "https://testnet.binance.vision/api"
        try:
            st = client.futures_time()['serverTime'] if mode == "FUTURES" else client.get_server_time()['serverTime']
            client.TIME_OFFSET = st - int(time.time() * 1000)
        except Exception:
            pass
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
                # Allow zero as a sentinel for 'not set'
                if f_val == 0:
                    continue
                if not math.isfinite(f_val) or f_val < 0:
                    raise StateCorruptionError(f"Invalid {p_field} {val}.")
            except (ValueError, TypeError):
                raise StateCorruptionError(f"Field {p_field} must be a positive finite number or None.")
                
    if not trade.get("is_futures") and trade.get("oco_id") is None:
        if trade.get("tp_price") is not None or trade.get("sl_price") is not None:
            raise StateCorruptionError("oco_id cannot be None if tp_price or sl_price are set.")

class InvalidOrderRequest(ValueError):
    """An entry request is malformed; refused before anything reaches the venue."""


def _validate_entry_request(side, quantity, sl, tp, *, require_protection: bool = True):
    """Validate an entry *before* the market order is submitted.

    Previously quantity/SL/TP were only checked inside the protection step,
    i.e. after the entry had already filled: a NaN or wrong-side stop cost a
    full round trip (entry fill, failed OCO, emergency market close), and
    ``if sl and tp`` treated ``NaN`` as present while a half-specified bracket
    (only SL or only TP) silently produced an unprotected, untracked position.

    Returns ``(quantity, sl, tp)`` as floats (sl/tp ``None`` only when
    protection is not required and neither was supplied).
    """
    if side not in ("BUY", "SELL"):
        raise InvalidOrderRequest(f"side must be 'BUY' or 'SELL', got {side!r}")
    qty = positive_float(quantity)
    if qty is None:
        raise InvalidOrderRequest(f"quantity must be a finite number > 0, got {quantity!r}")
    if sl is None and tp is None and not require_protection:
        return qty, None, None
    if sl is None or tp is None:
        raise InvalidOrderRequest(
            f"UNPROTECTED_ENTRY_REFUSED: both stop-loss and take-profit are required (sl={sl!r}, tp={tp!r})"
        )
    sl_value = positive_float(sl)
    tp_value = positive_float(tp)
    if sl_value is None or tp_value is None:
        raise InvalidOrderRequest(f"sl/tp must be finite numbers > 0 (sl={sl!r}, tp={tp!r})")
    if side == "BUY" and not sl_value < tp_value:
        raise InvalidOrderRequest(f"BUY entry requires sl < tp (sl={sl_value}, tp={tp_value})")
    if side == "SELL" and not sl_value > tp_value:
        raise InvalidOrderRequest(f"SELL entry requires sl > tp (sl={sl_value}, tp={tp_value})")
    return qty, sl_value, tp_value


def _load_active_trades():
    if not os.path.exists(ACTIVE_TRADES_FILE):
        return []

    # Strict load (NaN/Infinity tokens rejected) without quarantine: the file
    # stays in place for the operator and the caller fails closed.
    with locked_path(ACTIVE_TRADES_FILE):
        result = load_json_state(ACTIVE_TRADES_FILE, expected_type=(list, dict), default_factory=list, quarantine=False)
    if result.status == "missing":
        return []
    if result.status != "ok":
        sys_logger.error(f"Failed to load JSON from {ACTIVE_TRADES_FILE}: {result.error}")
        raise StateCorruptionError("Active trades JSON is corrupt.")
    data = result.data

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

def _strict_json_copy(value, path="", replaced=None):
    """Copy ``value`` replacing non-finite floats by None; collect their paths."""
    if replaced is None:
        replaced = []
    if isinstance(value, float) and not math.isfinite(value):
        replaced.append(path or "<root>")
        return None, replaced
    if isinstance(value, dict):
        return {k: _strict_json_copy(v, f"{path}.{k}" if path else str(k), replaced)[0] for k, v in value.items()}, replaced
    if isinstance(value, (list, tuple)):
        return [_strict_json_copy(v, f"{path}[{i}]", replaced)[0] for i, v in enumerate(value)], replaced
    return value, replaced


def _append_ledger_record(path, entry):
    """Append one trade-ledger record durably: a single complete, fsynced line
    (a torn tail from an earlier crash is sealed off first).

    Non-finite numbers are written as null and flagged with ``numeric_fault``
    so a closed trade is never dropped and the ledger stays strict JSON.
    """
    clean, replaced = _strict_json_copy(entry)
    if replaced:
        clean["numeric_fault"] = True
        clean["numeric_fault_fields"] = replaced
        sys_logger.error(f"[LEDGER] Non-finite values {replaced} in ledger record for {entry.get('symbol')}; stored as null")
    append_jsonl(path, clean, fsync=True)


def _save_active_trades(trades):
    # Serialize first: a record with NaN/inf (or an unserializable value) must
    # fail loudly here instead of becoming a restart state that cannot load.
    try:
        payload = json.dumps(trades, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StateCorruptionError(f"Refusing to persist active trades: {exc}") from exc
    with locked_path(ACTIVE_TRADES_FILE):
        if os.path.exists(ACTIVE_TRADES_FILE):
            backup_dir = "backup"
            with open(ACTIVE_TRADES_FILE, "rb") as current:
                previous = current.read()
            # Atomic backup: a crash mid-copy can no longer leave a torn .bak.
            atomic_write_bytes(os.path.join(backup_dir, "active_trades.json.bak"), previous)
        atomic_write_text(ACTIVE_TRADES_FILE, payload)

# ==============================================================================
# LEDGER DEDUP CACHE
# The duplicate-signal scan must consult the whole trade ledger, but re-reading
# an unbounded JSONL file on every order is O(history) per order. We therefore
# keep an incremental index of every dedup ID seen so far and only parse the
# bytes appended since the last scan. Identity is (dev, inode): rotation or
# replacement of the file forces a clean full re-scan, and a shrink (size <
# offset) is treated as truncation and re-scanned from the start. Semantics are
# identical to a full scan: a record matches when signal_id, entry_client_id,
# trade_id or str(entry_order_id) equals the candidate ID. Malformed lines are
# skipped without aborting the check (strictly safer than the old all-or-nothing
# try/except).
# ==============================================================================
_LEDGER_ID_CACHE = {"identity": None, "offset": 0, "ids": set()}
_LEDGER_ID_FIELDS = ("signal_id", "entry_client_id", "trade_id")


def _ledger_identity_and_size(ledger_file):
    try:
        st = os.stat(ledger_file)
    except OSError:
        return None, 0
    return (st.st_dev, st.st_ino), st.st_size


def _extract_record_ids(rec):
    ids = set()
    for field in _LEDGER_ID_FIELDS:
        val = rec.get(field)
        if val is not None and str(val) != "":
            ids.add(str(val))
    entry_order_id = rec.get("entry_order_id")
    if entry_order_id is not None:
        ids.add(str(entry_order_id))
    return ids


def ledger_contains_id(ledger_file, client_order_id):
    """O(new-lines) duplicate check against the trade ledger.

    Returns True when any ledger record already carries ``client_order_id``
    as signal_id / entry_client_id / trade_id / entry_order_id.
    """
    identity, size = _ledger_identity_and_size(ledger_file)
    if identity is None:
        return False

    cache = _LEDGER_ID_CACHE
    if cache["identity"] != identity or size < cache["offset"]:
        # New/rotated/truncated file: rebuild from the beginning.
        cache["identity"] = identity
        cache["offset"] = 0
        cache["ids"] = set()

    if size > cache["offset"]:
        try:
            with open(ledger_file, "r", encoding="utf-8") as lf:
                lf.seek(cache["offset"])
                for line in lf:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        rec = json.loads(stripped)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(rec, dict):
                        cache["ids"].update(_extract_record_ids(rec))
                cache["offset"] = lf.tell()
        except OSError:
            # Unreadable right now: fall through with whatever we have indexed.
            pass

    return str(client_order_id) in cache["ids"]


def reset_ledger_id_cache():
    """Test hook: drop the incremental ledger index."""
    _LEDGER_ID_CACHE["identity"] = None
    _LEDGER_ID_CACHE["offset"] = 0
    _LEDGER_ID_CACHE["ids"] = set()

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
    _check_panic_and_kill_switch()
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

    # Check Idempotency Store
    if client_order_id:
        is_dup, cached = get_idempotency_store().check_and_record(client_order_id, request_type="SPOT_ORDER")
        if is_dup:
            sys_logger.warning(f"[{strategy_name}] 🚫 Idempotent duplicate order rejected for client_order_id '{client_order_id}'.")
            return cached.get("response") if cached else None


    try:
        active_trades = _load_active_trades()
        if client_order_id:
            for t in active_trades:
                if t.get("signal_id") == client_order_id or t.get("entry_client_id") == client_order_id or t.get("trade_id") == client_order_id or str(t.get("entry_order_id")) == str(client_order_id):
                    sys_logger.warning(f"[{strategy_name}] 🚫 Duplicate Client/Signal ID {client_order_id} rejected.")
                    return None
            # Also check recent ledger records for deduplication
            # (incremental index — O(new lines) instead of O(whole history)).
            ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
            if os.path.exists(ledger_file) and ledger_contains_id(ledger_file, client_order_id):
                sys_logger.warning(f"[{strategy_name}] 🚫 Duplicate signal already executed in ledger: {client_order_id}")
                return None
    except StateCorruptionError as e:
        sys_logger.critical(f"State corruption prevents new orders: {e}")
        raise

    try:
        quantity, sl, tp = _validate_entry_request(side, quantity, sl, tp)
    except InvalidOrderRequest as e:
        sys_logger.error(f"[{strategy_name}] 🚫 Entry refused before submission: {e}")
        if client_order_id:
            get_idempotency_store().remove(client_order_id)
        raise

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
        # Every submission must carry a deterministic client id and be reconciled.
        if not client_order_id:
            client_order_id = f"stx-{strategy_name}-{int(time.time_ns())}"
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
                    "status":            "OPEN",
                    "entry_client_id":   client_order_id,
                    "entry_order_id":    order_id
                })
                _save_active_trades(active)

            except Exception as e:
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
                    residual = abs(executed_qty - ec_qty)
                    # Re-query venue position after emergency close; never assume flatness.
                    # Persist UNKNOWN state when residual quantity cannot be proven zero.
                    if residual > 1e-8 or not ec.get("_is_flat", True):
                        state = OrderState.UNKNOWN
                        sys_logger.critical(
                            f"[EMERGENCY_REVIEW] [{strategy_name}] {symbol} emergency close partial residual: {residual}. Status: UNKNOWN",
                            extra={"strategy": strategy_name, "symbol": symbol}
                        )
                    else:
                        state = OrderState.EMERGENCY_CLOSE
                        sys_logger.info(
                            f"[{strategy_name}] Emergency close FILLED: qty={ec_qty}.",
                            extra={"strategy": strategy_name, "symbol": symbol}
                        )
                    log_trade(strategy_name, symbol, f"{side}_EMERGENCY_CLOSE",
                              ec_qty, actual_price, sl, tp, order_id, state)
                except Exception as ce:
                    state = OrderState.UNKNOWN
                    sys_logger.critical(
                        f"[EXEC] 🚨 FATAL: Emergency close also failed! "
                        f"UNPROTECTED POSITION ACTIVE for {symbol}. Error: {ce}",
                        extra={"strategy": strategy_name, "symbol": symbol}
                    )
                # The entry DID reach the venue. Record a terminal result so a
                # retry of this client_order_id returns it instead of the PENDING
                # record going stale after 60s and a duplicate entry being sent.
                if client_order_id:
                    get_idempotency_store().complete_request(client_order_id, {
                        "status": "PROTECTION_FAILED",
                        "orderId": order_id,
                        "_final_state": state.value,
                        "_executed_qty": executed_qty,
                    })
                return None


        log_trade(strategy_name, symbol, side, executed_qty, actual_price, sl, tp, order_id, state)
        # Attach our custom metrics
        order["_actual_price"] = actual_price
        order["_executed_qty"] = executed_qty
        order["_total_fee"] = total_fee
        order["_final_state"] = state
        if client_order_id:
            get_idempotency_store().complete_request(client_order_id, order)
        return order
    except BinanceAPIException as e:
        if client_order_id:
            get_idempotency_store().remove(client_order_id)
        est_price = actual_price if 'actual_price' in locals() and actual_price > 0 else (sl or tp or 0.0)
        sys_logger.error(
            f"[EXECUTION_FAILED] Binance API Error | Code: {e.code} | Message: {e.message} | "
            f"Symbol: {symbol} | Side: {side} | Quantity: {quantity} | Price: {est_price} | "
            f"Order Type: {Client.ORDER_TYPE_MARKET} | Client Order ID: {client_order_id}",
            extra={"strategy": strategy_name, "symbol": symbol, "api_code": e.code, "api_message": e.message}
        )
        raise
    except Exception as e:
        if client_order_id:
            get_idempotency_store().remove(client_order_id)
        est_price = actual_price if 'actual_price' in locals() and actual_price > 0 else (sl or tp or 0.0)
        sys_logger.error(
            f"[EXECUTION_FAILED] Unexpected Exception: {e} | Symbol: {symbol} | Side: {side} | "
            f"Quantity: {quantity} | Price: {est_price} | Order Type: {Client.ORDER_TYPE_MARKET} | "
            f"Client Order ID: {client_order_id}",
            extra={"strategy": strategy_name, "symbol": symbol}
        )
        raise


def set_futures_leverage_and_margin(client: Client, symbol: str, leverage: int = 5, margin_type: str = "ISOLATED"):
    """
    Configures leverage and margin type for a futures symbol.
    Fails safely if already configured.
    """
    try:
        # Set margin type (ISOLATED or CROSSED)
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
            sys_logger.info(f"[FUTURES] Margin type for {symbol} set to {margin_type}")
        except BinanceAPIException as me:
            if "No need to change margin type" in str(me) or "-4046" in str(me):
                pass
            else:
                sys_logger.warning(f"[FUTURES] Margin type setting for {symbol} warning: {me}")
                
        # Set leverage
        try:
            client.futures_change_leverage(symbol=symbol, leverage=leverage)
            sys_logger.info(f"[FUTURES] Leverage for {symbol} set to {leverage}x")
        except Exception as le:
            sys_logger.warning(f"[FUTURES] Leverage setting for {symbol} warning: {le}")
    except Exception as e:
        sys_logger.warning(f"[FUTURES] Error setting leverage/margin for {symbol}: {e}")


def place_futures_market_order(strategy_name, side, symbol, quantity=TRADE_QTY, sl=None, tp=None, client_order_id=None, leverage=5):
    """
    Places a Futures market order (BUY for Long, SELL for Short) with attached STOP_MARKET and TAKE_PROFIT_MARKET orders.
    """
    _check_panic_and_kill_switch()
    allowed, reason = ExecutionPolicy.can_place_order()
    if not allowed:
        raise RuntimeError(f"CRITICAL ERROR: Order blocked. ({reason})")

    # Check Idempotency Store
    if client_order_id:
        is_dup, cached = get_idempotency_store().check_and_record(client_order_id, request_type="FUTURES_ORDER")
        if is_dup:
            sys_logger.warning(f"[{strategy_name}] 🚫 Idempotent duplicate futures order rejected for client_order_id '{client_order_id}'.")
            return cached.get("response") if cached else None


    try:
        active_trades = _load_active_trades()
        if client_order_id:
            for t in active_trades:
                if t.get("signal_id") == client_order_id or t.get("entry_client_id") == client_order_id or t.get("trade_id") == client_order_id or str(t.get("entry_order_id")) == str(client_order_id):
                    sys_logger.warning(f"[{strategy_name}] 🚫 Duplicate Client/Signal ID {client_order_id} rejected.")
                    if client_order_id:
                        get_idempotency_store().remove(client_order_id)
                    return None
    except StateCorruptionError as e:
        sys_logger.critical(f"State corruption prevents new orders: {e}")
        if client_order_id:
            get_idempotency_store().remove(client_order_id)
        return None

    try:
        quantity, sl, tp = _validate_entry_request(side, quantity, sl, tp)
        lev_value = finite_float(leverage)
        if lev_value is None or not 1 <= lev_value <= 125 or not float(lev_value).is_integer():
            raise InvalidOrderRequest(f"leverage must be a whole number between 1 and 125, got {leverage!r}")
        leverage = int(lev_value)
    except InvalidOrderRequest as e:
        sys_logger.error(f"[{strategy_name}] 🚫 Futures entry refused before submission: {e}")
        if client_order_id:
            get_idempotency_store().remove(client_order_id)
        raise

    client = get_exchange_client()
    state = OrderState.ENTRY_SUBMITTED

    try:
        # Pre-Trade Margin Check
        try:
            acc = client.futures_account() if hasattr(client, "futures_account") else {}
            available_balance = float(acc.get("availableBalance", 0.0))
            est_entry_price = float(sl or tp or 0.0)
            if est_entry_price <= 0:
                try:
                    mark_ticker = client.futures_symbol_ticker(symbol=symbol) if hasattr(client, "futures_symbol_ticker") else None
                    if mark_ticker and "price" in mark_ticker:
                        est_entry_price = float(mark_ticker["price"])
                except Exception:
                    pass
            
            qty_float = float(quantity)
            lev = float(leverage) if leverage > 0 else 5.0
            required_margin = (qty_float * est_entry_price) / lev if (qty_float > 0 and est_entry_price > 0) else 0.0

            if required_margin > 0 and available_balance > 0 and required_margin > available_balance:
                sys_logger.warning(
                    f"[{strategy_name}] ⚠️ SKIPPED (INSUFFICIENT MARGIN) for {symbol} {side} {quantity}. "
                    f"Required: ${required_margin:.2f} USDT, Available: ${available_balance:.2f} USDT",
                    extra={"strategy": strategy_name, "symbol": symbol}
                )
                if client_order_id:
                    get_idempotency_store().remove(client_order_id)
                return None
        except Exception as margin_err:
            sys_logger.debug(f"[FUTURES] Pre-trade margin check bypassed: {margin_err}")

        # 1. Ensure isolated margin & leverage
        set_futures_leverage_and_margin(client, symbol, leverage=leverage, margin_type="ISOLATED")

        # 2. Place entry order via /fapi/v1/order
        order_params = {
            "symbol": symbol,
            "side": side,  # "BUY" (Long) or "SELL" (Short)
            "type": "MARKET",
            "quantity": quantity
        }
        if client_order_id:
            order_params["newClientOrderId"] = client_order_id

        order = client.futures_create_order(**order_params)
        order_id = order.get("orderId", "N/A")
        
        executed_qty = float(order.get("executedQty", quantity))
        cum_quote = float(order.get("cumQuote", 0.0))
        avg_price = float(order.get("avgPrice", 0.0))
        if avg_price == 0.0 and executed_qty > 0 and cum_quote > 0:
            avg_price = cum_quote / executed_qty

        actual_price = avg_price
        total_fee = (cum_quote or (actual_price * executed_qty)) * 0.0005  # 0.05% taker fee estimate for futures
        state = OrderState.ENTRY_FILLED

        sys_logger.info(
            f"[FUTURES] [{strategy_name}] ✅ {side} order {state}! "
            f"Avg Price: {actual_price:.2f}, Executed Qty: {executed_qty}, Fees: {total_fee}",
            extra={"strategy": strategy_name, "symbol": symbol}
        )

        tp_order_id = None
        sl_order_id = None

        # 3. Place conditional SL and TP bracket orders
        if sl and tp:
            state = OrderState.PROTECTION_PENDING
            try:
                prot = place_futures_bracket_protection(
                    client=client,
                    symbol=symbol,
                    entry_side=side,
                    executed_qty=executed_qty,
                    actual_fill_price=actual_price,
                    sl_price=sl,
                    tp_price=tp,
                )
                tp_order_id = prot["tp_order_id"]
                sl_order_id = prot["sl_order_id"]
                state = OrderState.PROTECTED

                sys_logger.info(
                    f"[FUTURES_PROTECTION_PLACED] [{strategy_name}] {symbol} | "
                    f"TP_orderId={tp_order_id} (@ {prot['tp_price_sent']}) "
                    f"SL_orderId={sl_order_id} (@ {prot['sl_price_sent']})",
                    extra={"strategy": strategy_name, "symbol": symbol}
                )

                # Save to active trades
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
                    "oco_id":            None,  # Futures uses distinct tp_order_id / sl_order_id
                    "tp_order_id":       tp_order_id,
                    "sl_order_id":       sl_order_id,
                    "tp_price":          tp,
                    "sl_price":          sl,
                    "state":             state.value,
                    "status":            "OPEN",
                    "entry_client_id":   client_order_id,
                    "entry_order_id":    order_id,
                    "is_futures":        True,
                    "leverage":          leverage,
                })
                _save_active_trades(active)

            except Exception as e:
                state = OrderState.PROTECTION_FAILED
                sys_logger.error(
                    f"[FUTURES_PROTECTION_FAILED] [{strategy_name}] {symbol} | "
                    f"Error: {e}. Attempting emergency Futures MARKET close.",
                    extra={"strategy": strategy_name, "symbol": symbol}
                )
                try:
                    emergency_futures_market_close(client, symbol, side, executed_qty)
                    state = OrderState.EMERGENCY_CLOSE
                except Exception as ce:
                    state = OrderState.UNKNOWN
                    sys_logger.critical(f"[FUTURES] 🚨 FATAL: Emergency close failed for {symbol}: {ce}")
                # The entry reached the venue: removing the key here let an
                # immediate retry of the same signal open a second position.
                if client_order_id:
                    get_idempotency_store().complete_request(client_order_id, {
                        "status": "PROTECTION_FAILED",
                        "orderId": order_id,
                        "_final_state": state.value,
                        "_executed_qty": executed_qty,
                    })
                return None

        log_trade(strategy_name, symbol, side, executed_qty, actual_price, sl, tp, order_id, state)
        order["_actual_price"] = actual_price
        order["_executed_qty"] = executed_qty
        order["_total_fee"] = total_fee
        order["_final_state"] = state
        if client_order_id:
            get_idempotency_store().complete_request(client_order_id, order)
        return order
    except Exception as e:
        if client_order_id:
            get_idempotency_store().remove(client_order_id)
        sys_logger.error(f"[FUTURES_EXECUTION_FAILED] Error: {e} | Symbol: {symbol} | Side: {side}")
        raise


def monitor_open_trades():
    """Checks active OCO orders to see if SL or TP was hit. Uses actual fill prices for PnL."""
    if TRADING_MODE == "PAPER":
        return

    import datetime

    from testnet_engine.protection import (
        LEDGER_WRITE_LOCK,
        check_oco_status,
        compute_net_pnl,
    )

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
        is_fut = t.get("is_futures", False) or TRADING_MODE == "FUTURES"
        oco_id = t.get("oco_id")
        tp_oid = t.get("tp_order_id")
        sl_oid = t.get("sl_order_id")

        if is_fut:
            try:
                fut_res = check_futures_bracket_status(client, t["symbol"], tp_oid, sl_oid)
                if fut_res["position_closed"]:
                    close_price = fut_res["close_avg_price"]
                    close_qty = fut_res["close_qty"] or float(t.get("quantity", 0))
                    tp_filled = fut_res["tp_filled"]
                    outcome = "WIN" if tp_filled else "LOSS"

                    entry_price = float(t.get("entry_price", 0))
                    entry_qty = float(t.get("quantity", close_qty))
                    entry_fee = float(t.get("entry_fee", 0.0))
                    exit_fee = close_price * close_qty * 0.0005

                    gross_pnl, net_pnl = compute_net_pnl(
                        t["side"], entry_qty, entry_price, entry_fee,
                        close_qty, close_price, exit_fee
                    )

                    sys_logger.info(
                        f"[FUTURES_POSITION_CLOSED] {t['symbol']} {outcome} | Qty: {close_qty} @ {close_price:.4f} (Entry: {entry_price:.4f})",
                        extra={"strategy": t["strategy"], "symbol": t["symbol"]}
                    )
                    
                    ledger_entry = {
                        "signal_id":      t.get("signal_id", "UNKNOWN"),
                        "symbol":         t["symbol"],
                        "strategy":       t["strategy"],
                        "source":         "BINANCE_FUTURES_EXECUTION",
                        "side":           t["side"],
                        "entry_order_id": t.get("entry_order_id"),
                        "entry_price":    entry_price,
                        "entry_executed_quantity": entry_qty,
                        "entry_fee":      entry_fee,
                        "exit_order_id":  tp_oid if tp_filled else sl_oid,
                        "exit_price":     close_price,
                        "exit_executed_quantity": close_qty,
                        "exit_fee":       exit_fee,
                        "exit_reason":    outcome,
                        "gross_pnl":      gross_pnl,
                        "total_fees":     entry_fee + exit_fee,
                        "net_pnl":        net_pnl,
                        "pnl":            net_pnl,
                        "fees":           entry_fee + exit_fee,
                        "entry_timestamp": t.get("entry_timestamp"),
                        "exit_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                        "timestamp":      datetime.datetime.utcnow().isoformat() + "Z",
                        "action":         f"CLOSE_{outcome}",
                        "quantity":       close_qty,
                        "is_futures":     True
                    }
                    with LEDGER_WRITE_LOCK:
                        _append_ledger_record(ledger_file, ledger_entry)
                    log_trade(
                        t["strategy"], t["symbol"],
                        f"{t['side']}_FUTURES_CLOSE_{outcome}",
                        close_qty, close_price,
                        t.get("sl_price"), t.get("tp_price"),
                        tp_oid if tp_filled else sl_oid, f"CLOSED_{outcome}"
                    )
                    continue
                else:
                    remaining_trades.append(t)
                    continue
            except Exception as e:
                sys_logger.error(f"[MONITOR_FUTURES] Error checking {t['symbol']}: {e}")
                remaining_trades.append(t)
                continue

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
                outcome     = "WIN" if tp_filled else "LOSS"

                entry_price = float(t.get("entry_price", 0))
                entry_qty   = float(t.get("quantity", close_qty))
                entry_fee   = float(t.get("entry_fee", 0.0))
                fee_rate    = getattr(config, "BACKTEST_FEE_RATE", 0.001)
                exit_fee    = close_price * close_qty * fee_rate

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
                    _append_ledger_record(ledger_file, ledger_entry)
                    if t.get("strategy") == "adx_ema":
                        try:
                            fwd_entry = dict(ledger_entry)
                            fwd_entry["strategy_version"] = "ADX_EMA_4H_V1"
                            _append_ledger_record("adx_ema_forward_ledger.jsonl", fwd_entry)
                        except (OSError, TypeError, ValueError) as fwd_err:
                            # Secondary research ledger: never blocks the close,
                            # but a gap in forward evidence must be visible.
                            sys_logger.warning(f"[MONITOR] adx_ema forward ledger append failed: {fwd_err}")

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
                closed_flat = False
                try:
                    from testnet_engine.protection import emergency_market_close
                    ec = emergency_market_close(
                        client, t["symbol"], t["side"], float(t["quantity"])
                    )
                    ec_qty = positive_float(ec.get("executedQty"))
                    ec_quote = positive_float(ec.get("cummulativeQuoteQty"))
                    if ec_qty is None or ec_quote is None:
                        raise ValueError(f"emergency close returned no usable fill: {ec!r}")
                    ec_price = ec_quote / ec_qty
                    ec_fee = ec_qty * ec_price * getattr(config, "BACKTEST_FEE_RATE", 0.001)
                    closed_flat = bool(ec.get("_is_flat", True))
                    
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
                        _append_ledger_record(ledger_file, ledger_entry)

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
                if not closed_flat:
                    # The OCO is dead and flatness is NOT proven: the old code
                    # dropped the trade anyway, leaving an unprotected position
                    # that nothing tracked. Keep it, mark it, and block orders.
                    _retain_unprotected(t, remaining_trades, f"OCO {oco_id} {status}; emergency close not confirmed")
            else:
                # Still executing
                remaining_trades.append(t)

        except BinanceAPIException as e:
            if "Order does not exist" in str(e) or "-2013" in str(e):
                # Check balance to see if we missed a successful exit or if it's orphaned
                flat_confirmed = False
                try:
                    asset = t['symbol'].replace("USDT", "")
                    asset_info = client.get_asset_balance(asset=asset)
                    asset_bal = finite_float(asset_info['free'])
                    locked_bal = finite_float(asset_info['locked'])
                    if asset_bal is None or locked_bal is None:
                        raise ValueError(f"unreadable balance {asset_info!r}")
                    if asset_bal + locked_bal < 0.0001: # Essentially 0
                        sys_logger.warning(f"[MONITOR] OCO {oco_id} missing but balance is 0. Position closed.")
                        flat_confirmed = True
                    else:
                        sys_logger.critical(f"[MONITOR] OCO {oco_id} missing but balance > 0! Attempting emergency close.")
                        from testnet_engine.protection import emergency_market_close
                        ec = emergency_market_close(client, t["symbol"], t["side"], float(t["quantity"]))
                        flat_confirmed = bool(ec.get("_is_flat", False))
                except Exception as bal_err:
                    sys_logger.critical(f"[MONITOR] Could not verify/close orphaned position {t['symbol']}: {bal_err}")

                if not flat_confirmed:
                    _retain_unprotected(t, remaining_trades, f"OCO {oco_id} missing on exchange; flatness not confirmed")
                    continue

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

def _retain_unprotected(trade, remaining_trades, reason):
    """Keep tracking a position whose protection is gone and engage the
    durable order block so no new entries are made until an operator
    reconciles it."""
    trade["state"] = OrderState.UNKNOWN.value
    trade["protection_lost"] = True
    trade["protection_lost_reason"] = reason
    remaining_trades.append(trade)
    sys_logger.critical(f"[MONITOR] 🚨 UNPROTECTED POSITION RETAINED: {trade.get('symbol')} — {reason}")
    try:
        from panic_state import engage_order_block

        engage_order_block("execution.monitor_open_trades", f"Unprotected position {trade.get('symbol')}: {reason}")
    except Exception as block_err:  # the retained record still surfaces it
        sys_logger.critical(f"[MONITOR] Failed to engage order block: {block_err}")


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
