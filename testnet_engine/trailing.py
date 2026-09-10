"""
trailing.py — Real-market trailing-stop / breakeven lock-in engine (v3).

Works on top of the exchange-side protective orders (spot OCO lists and
futures bracket orders). When a position moves into profit:

  Stage BREAKEVEN — at TRAIL_BREAKEVEN_TRIGGER_R × initial risk, the SL is
  moved to entry (+ fee buffer). The worst case becomes a scratch instead
  of a full loss.

  Stage TRAILING  — at TRAIL_TRIGGER_R × initial risk, the SL trails the
  market at (price ∓ TRAIL_ATR_MULT × ATR), monotonically in the trade's
  favor, keeping the original TP in place.

This converts trades that would have round-tripped into full losses into
breakeven or winning exits — directly raising realized win rate and net
expectancy without touching signal logic.

Safety contract (mirrors protection.py):
  * An UNPROTECTED position is never left behind. If cancellation of the
    old protection succeeds but re-placement fails, the position is
    emergency market-closed.
  * Every modification is throttled per symbol (TRAIL_MIN_REARM_SECONDS),
    logged, and persisted in active_trades.json.
  * Only real market data is used: live ticker price + ATR from the
    scanner's real candle cache.
"""

import json
import math
import os
import time
import datetime

import config
from logger import get_logger
from testnet_engine.signal_quality import compute_atr_from_df

logger = get_logger("trailing")


def _cfg(name, default):
    import config
    return getattr(config, name, default)


# ---------------------------------------------------------------------------
# Pure math helpers (unit-testable, no I/O)
# ---------------------------------------------------------------------------

def compute_trail_target(side, entry_price, current_price, current_sl, tp_price,
                         initial_risk, stage, atr_value=None):
    """Compute the new protective-stop target for a position.

    Returns (new_sl: float | None, stage_reached: str | None).
    Pure function — no I/O, no exchange calls.
    """
    if initial_risk is None or initial_risk <= 0 or entry_price <= 0 or current_price <= 0:
        return None, None

    if side == "BUY":
        r_multiple = (current_price - entry_price) / initial_risk
    else:
        r_multiple = (entry_price - current_price) / initial_risk

    be_trigger = float(_cfg("TRAIL_BREAKEVEN_TRIGGER_R", 1.0))
    trail_trigger = float(_cfg("TRAIL_TRIGGER_R", 1.5))
    fee_buffer = float(_cfg("TRAIL_FEE_BUFFER_PCT", 0.002))
    atr_mult = float(_cfg("TRAIL_ATR_MULT", 2.0))

    candidate = None
    reached = None

    if r_multiple >= trail_trigger and atr_value and atr_value > 0:
        if side == "BUY":
            candidate = current_price - atr_mult * atr_value
        else:
            candidate = current_price + atr_mult * atr_value
        reached = "TRAILING"
    elif r_multiple >= be_trigger:
        if side == "BUY":
            candidate = entry_price * (1 + fee_buffer)
        else:
            candidate = entry_price * (1 - fee_buffer)
        reached = "BREAKEVEN"

    if candidate is None:
        return None, None

    # Monotonic improvement only; never cross the TP; keep distance from market.
    if side == "BUY":
        if current_sl and candidate <= current_sl:
            return None, None
        if tp_price and candidate >= float(tp_price) * 0.999:
            candidate = float(tp_price) * 0.999
        if candidate >= current_price:
            return None, None
    else:
        if current_sl and candidate >= current_sl:
            return None, None
        if tp_price and candidate <= float(tp_price) * 1.001:
            candidate = float(tp_price) * 1.001
        if candidate <= current_price:
            return None, None

    return candidate, reached


def stage_for_r_multiple(r_multiple):
    """Which stage a given R-multiple reaches (pure)."""
    if r_multiple >= float(_cfg("TRAIL_TRIGGER_R", 1.5)):
        return "TRAILING"
    if r_multiple >= float(_cfg("TRAIL_BREAKEVEN_TRIGGER_R", 1.0)):
        return "BREAKEVEN"
    return None


def _price_friendly(value):
    """Guard against float noise propagating into order prices."""
    return float(f"{float(value):.10g}")


# ---------------------------------------------------------------------------
# Exchange interaction
# ---------------------------------------------------------------------------

def _cancel_spot_oco(client, symbol, oco_id) -> bool:
    """Cancel a spot OCO order list. Tries the SDK's v3 delete method, then raw request."""
    if not oco_id:
        return False
    try:
        fn = getattr(client, "v3_delete_order_list", None)
        if callable(fn):
            fn(symbol=symbol, orderListId=int(oco_id))
            return True
        fn = getattr(client, "_delete_order_list", None)
        if callable(fn):
            fn(symbol=symbol, orderListId=int(oco_id))
            return True
        client._request(
            "DELETE", "/api/v3/orderList", signed=True,
            data={"symbol": symbol, "orderListId": int(oco_id)},
        )
        return True
    except Exception as e:
        logger.error(f"[TRAIL] Failed to cancel OCO {oco_id} on {symbol}: {e}")
        return False


def _cancel_futures_order(client, symbol, order_id) -> bool:
    if not order_id:
        return False
    try:
        if hasattr(client, "futures_cancel_algo_order"):
            try:
                res = client.futures_cancel_algo_order(algoId=int(order_id))
                if isinstance(res, dict) and (res.get("code") == 200 or res.get("result") == "SUCCESS"):
                    return True
            except Exception:
                pass
        client.futures_cancel_order(symbol=symbol, orderId=int(order_id))
        return True
    except Exception as e:
        logger.debug(f"[TRAIL] Failed to cancel futures order {order_id} on {symbol}: {e}")
        return False


def _record_harvest_win_in_ledger(trade, exit_price, profit_pct):
    """Authoritative ledger recording for banked profit harvest wins."""
    ledger_file = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
    try:
        oid = trade.get("exit_order_id") or trade.get("order_id")
        entry_p = float(trade.get("entry_price", exit_price))
        qty = float(trade.get("quantity", 0))
        side = trade.get("side", "BUY")
        direction = "LONG" if side == "BUY" else "SHORT"
        gross_pnl = (entry_p - exit_price) * qty if side == "SELL" else (exit_price - entry_p) * qty
        fees = qty * (entry_p + exit_price) * 0.0004
        net_pnl = gross_pnl - fees
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        entry = {
            "symbol": trade.get("symbol"),
            "order_id": str(oid) if oid else "",
            "exit_order_id": str(oid) if oid else "",
            "action": "SELL" if side == "BUY" else "BUY",
            "direction": direction,
            "quantity": round(qty, 4),
            "entry_price": round(entry_p, 4),
            "exit_price": round(exit_price, 4),
            "gross_pnl": round(gross_pnl, 4),
            "pnl": round(gross_pnl, 4),
            "net_pnl": round(net_pnl, 4),
            "fees": round(fees, 4),
            "timestamp": now_iso,
            "exit_timestamp": now_iso,
            "exit_reason": "WIN",
            "strategy": trade.get("strategy", "SUPERTREND"),
            "source": "BINANCE_FUTURES_EXECUTION",
            "is_futures": True
        }
        with open(ledger_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"[TRAIL] 💰 Banked PROFIT_HARVEST_WIN for {trade.get('symbol')}! Net: +${net_pnl:.4f}")
    except Exception as e:
        logger.error(f"[TRAIL] Failed to record harvest win to ledger: {e}")


def _rebuild_spot_protection(client, symbol, side, qty, entry_price, new_sl, tp_price, list_client_order_id=None):
    """Cancel old OCO and place a fresh one with the trailed SL.

    Returns the new protection dict, or raises. If re-placement fails after a
    successful cancel the caller MUST emergency-close the position.
    """
    from testnet_engine.protection import place_oco_protection

    prot = place_oco_protection(
        client=client,
        symbol=symbol,
        entry_side=side,
        executed_qty=qty,
        actual_fill_price=entry_price,
        sl_price=new_sl,
        tp_price=tp_price,
        list_client_order_id=list_client_order_id,
    )
    return prot


def _rebuild_futures_protection(client, symbol, side, qty, entry_price, new_sl, tp_price):
    """Cancel old futures bracket orders and place a fresh one with the trailed SL."""
    from testnet_engine.protection import place_futures_bracket_protection

    prot = place_futures_bracket_protection(
        client=client,
        symbol=symbol,
        entry_side=side,
        executed_qty=qty,
        actual_fill_price=entry_price,
        sl_price=new_sl,
        tp_price=tp_price,
    )
    return prot


# ---------------------------------------------------------------------------
# Main trailing cycle
# ---------------------------------------------------------------------------

def _get_live_price(client, symbol, is_futures=False):
    """Get the current market price for a symbol from the exchange."""
    try:
        if is_futures:
            ticker = client.futures_symbol_ticker(symbol=symbol)
        else:
            ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except Exception as e:
        logger.error(f"[TRAIL] Failed to get live price for {symbol}: {e}")
        return None


def _get_atr_from_cache(service, symbol, tf="1h"):
    """Get ATR from the scanner's real candle cache."""
    try:
        scanner = getattr(service, "scanner", None)
        if scanner is None:
            return None
        with scanner._cache_lock:
            df = scanner.candle_cache.get((symbol, tf))
        if df is None or df.empty:
            # Try other timeframes
            for fallback_tf in ["4h", "15m", "30m"]:
                df = scanner.candle_cache.get((symbol, fallback_tf))
                if df is not None and not df.empty:
                    break
        if df is None or df.empty:
            return None
        return compute_atr_from_df(df)
    except Exception as e:
        logger.error(f"[TRAIL] Failed to get ATR from cache for {symbol}: {e}")
        return None


def trailing_cycle(service):
    """Evaluate all open positions and trail stops where appropriate.

    This is the main entry point, called periodically from the trailing loop.
    """
    if not bool(_cfg("TRAILING_STOP_ENABLED", True)):
        return

    try:
        from execution import _load_active_trades, _save_active_trades, get_exchange_client
        from testnet_engine.protection import emergency_market_close, emergency_futures_market_close
    except ImportError:
        logger.error("[TRAIL] Failed to import required modules")
        return

    try:
        active = _load_active_trades()
    except Exception as e:
        logger.error(f"[TRAIL] Failed to load active trades: {e}")
        return

    client = getattr(service, "client", None)
    if client is None:
        try:
            client = get_exchange_client()
        except Exception:
            client = None
    if client is None:
        return

    modified = False
    now = time.time()
    min_rearm = float(_cfg("TRAIL_MIN_REARM_SECONDS", 60))
    harvest_pct = float(_cfg("PROFIT_HARVEST_PCT", 0.015))  # 1.5% profit target

    for trade in active:
        try:
            # Only process open positions (status="OPEN" or state="PROTECTED")
            trade_status = trade.get("status") or trade.get("state")
            if trade_status not in ("OPEN", "PROTECTED"):
                continue

            symbol = trade.get("symbol")
            side = trade.get("side")
            entry_price = float(trade.get("entry_price", 0))
            current_sl = float(trade.get("sl_price", 0))
            tp_price = float(trade.get("tp_price", 0))
            qty = float(trade.get("quantity", 0))
            is_futures = trade.get("is_futures", False) or getattr(config, "TRADING_MODE", "TESTNET").upper() == "FUTURES"

            if not symbol or not side or entry_price <= 0:
                continue

            current_price = _get_live_price(client, symbol, is_futures)
            if current_price is None:
                continue

            # -----------------------------------------------------------------
            # 1. UNTHROTTLED Profit Harvest Check: Bank winning trades at once!
            # -----------------------------------------------------------------
            price_diff = (entry_price - current_price) if side == "SELL" else (current_price - entry_price)
            current_profit_pct = price_diff / entry_price if entry_price > 0 else 0.0

            if current_profit_pct >= harvest_pct:
                logger.info(
                    f"[PROFIT_HARVEST] 🎯 {symbol} {side} hit harvest target "
                    f"{current_profit_pct:.2%} >= {harvest_pct:.2%}. Closing to bank WIN!"
                )
                try:
                    close_res = None
                    if is_futures:
                        close_res = emergency_futures_market_close(client, symbol, side, qty)
                        old_tp_oid = trade.get("tp_order_id")
                        old_sl_oid = trade.get("sl_order_id")
                        _cancel_futures_order(client, symbol, old_tp_oid)
                        _cancel_futures_order(client, symbol, old_sl_oid)
                    else:
                        close_res = emergency_market_close(client, symbol, side, qty)
                    trade["status"] = "CLOSED"
                    trade["exit_reason"] = "PROFIT_HARVEST_WIN"
                    trade["exit_price"] = current_price
                    if close_res and isinstance(close_res, dict):
                        trade["exit_order_id"] = close_res.get("orderId")
                        trade["order_id"] = close_res.get("orderId")
                    modified = True
                    _record_harvest_win_in_ledger(trade, current_price, current_profit_pct)
                    continue
                except Exception as he:
                    logger.error(f"[PROFIT_HARVEST] Failed to harvest {symbol}: {he}")

            # -----------------------------------------------------------------
            # 2. Trailing Stop / Breakeven Arming (throttled by min_rearm)
            # -----------------------------------------------------------------
            if not all([current_sl > 0, tp_price > 0, qty > 0]):
                continue

            last_trail = trade.get("last_trail_time", 0)
            if now - last_trail < min_rearm:
                continue

            initial_risk = abs(entry_price - current_sl)
            if initial_risk <= 0:
                continue

            # Get ATR for trailing stage
            atr_value = _get_atr_from_cache(service, symbol)

            # Compute trail target
            new_sl, stage = compute_trail_target(
                side, entry_price, current_price, current_sl, tp_price,
                initial_risk, "TRAILING" if atr_value else "BREAKEVEN", atr_value
            )

            if new_sl is None:
                continue

            # Round the new SL to a friendly price
            new_sl = _price_friendly(new_sl)

            logger.info(
                f"[TRAIL] {symbol} {side} | Stage: {stage} | "
                f"Price: {current_price:.4f} | Old SL: {current_sl:.4f} | New SL: {new_sl:.4f} | "
                f"Entry: {entry_price:.4f} | TP: {tp_price:.4f}"
            )

            # Execute the trail
            if is_futures:
                # Cancel old orders and place new bracket
                old_tp_oid = trade.get("tp_order_id")
                old_sl_oid = trade.get("sl_order_id")
                _cancel_futures_order(client, symbol, old_tp_oid)
                _cancel_futures_order(client, symbol, old_sl_oid)

                try:
                    prot = _rebuild_futures_protection(client, symbol, side, qty, entry_price, new_sl, tp_price)
                    trade["sl_price"] = new_sl
                    trade["tp_order_id"] = prot["tp_order_id"]
                    trade["sl_order_id"] = prot["sl_order_id"]
                    trade["last_trail_time"] = now
                    trade["trail_stage"] = stage
                    modified = True
                    logger.info(f"[TRAIL] {symbol} futures bracket updated: SL={new_sl}")
                except Exception as e:
                    logger.critical(f"[TRAIL] {symbol} futures bracket rebuild failed: {e}. Emergency closing.")
                    emergency_futures_market_close(client, symbol, side, qty)
                    trade["status"] = "EMERGENCY_CLOSED"
                    modified = True
            else:
                # Cancel old OCO and place new one
                old_oco_id = trade.get("oco_id")
                if old_oco_id:
                    if not _cancel_spot_oco(client, symbol, old_oco_id):
                        logger.warning(f"[TRAIL] {symbol} failed to cancel old OCO, skipping")
                        continue

                try:
                    prot = _rebuild_spot_protection(client, symbol, side, qty, entry_price, new_sl, tp_price)
                    trade["sl_price"] = new_sl
                    trade["oco_id"] = prot["oco_order_list_id"]
                    trade["tp_order_id"] = prot["tp_order_id"]
                    trade["sl_order_id"] = prot["sl_order_id"]
                    trade["last_trail_time"] = now
                    trade["trail_stage"] = stage
                    modified = True
                    logger.info(f"[TRAIL] {symbol} OCO updated: SL={new_sl}")
                except Exception as e:
                    logger.critical(f"[TRAIL] {symbol} OCO rebuild failed: {e}. Emergency closing.")
                    emergency_market_close(client, symbol, side, qty)
                    trade["status"] = "EMERGENCY_CLOSED"
                    modified = True

        except Exception as e:
            logger.error(f"[TRAIL] Error processing trade {trade.get('symbol', 'unknown')}: {e}")
            continue

    if modified:
        try:
            _save_active_trades(active)
        except Exception as e:
            logger.error(f"[TRAIL] Failed to save active trades: {e}")

