import os
import time
import json
import uuid
import datetime
import threading
import queue
import importlib
from config import TRADING_MODE, ACTIVE_STRATEGY, TIMEFRAME
from logger import get_logger
from data import add_indicators

# Dynamically load the get_signal function from the active strategy
strategy_module = importlib.import_module(f"strategy_{ACTIVE_STRATEGY}")
get_signal = strategy_module.get_signal

from execution import place_market_order, get_exchange_client
from binance.exceptions import BinanceAPIException
from paper_engine.exceptions import ZeroFillError
from research_phase9.cost_engine import CostEngine
from testnet_engine.discovery import SymbolDiscoveryService
from testnet_engine.market_scanner import MarketScanner
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate

logger = get_logger("service")

TESTNET_LEDGER_FILE = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
TESTNET_OPPORTUNITY_LOG = os.getenv("TESTNET_OPPORTUNITY_LOG", "testnet_opportunity_log.jsonl")
TESTNET_PORTFOLIO_FILE = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")

class TestnetService:
    def __init__(self):
        # 1. TRADING_MODE strictly validated
        if TRADING_MODE != "TESTNET":
            raise RuntimeError(f"CRITICAL ERROR: Refusing to start TestnetService because TRADING_MODE={TRADING_MODE}. Must be TESTNET.")
            
        if os.getenv("TESTNET_ONLY", "FALSE").upper() != "TRUE":
            raise RuntimeError("CRITICAL ERROR: TESTNET_ONLY=TRUE is required to run the Testnet execution mode safely.")
            
        print("========================================")
        print("24/7 TESTNET EXECUTION SERVICE")
        print("========================================")
        print("EXECUTION ENVIRONMENT: TESTNET")
        print("LIVE ORDERS: BLOCKED")
        print(f"Strategy: {ACTIVE_STRATEGY}")
        
        self.client = get_exchange_client()
        if self.client is None:
            raise RuntimeError("CRITICAL ERROR: Binance Client could not be instantiated for TESTNET.")
            
        # Reconcile Initial Balance
        try:
            account = self.client.get_account()
            usdt_balance = next((item for item in account['balances'] if item['asset'] == 'USDT'), None)
            if not usdt_balance:
                raise RuntimeError("No USDT balance found on Testnet.")
            actual_binance_balance = float(usdt_balance['free']) + float(usdt_balance['locked'])
            
            # Load or initialize our authoritative initial deposit
            self.initial_deposit = actual_binance_balance
            if os.path.exists(TESTNET_PORTFOLIO_FILE):
                try:
                    with open(TESTNET_PORTFOLIO_FILE, "r") as f:
                        state = json.load(f)
                        if "initial_deposit" in state:
                            self.initial_deposit = state["initial_deposit"]
                except:
                    pass
                    
            self.service_start_time = datetime.datetime.utcnow().isoformat() + "Z"
            self.starting_equity = self.initial_deposit # Keep for compatibility
            self.last_equity_snapshot = 0.0
            logger.info(f"[SERVICE] Actual Binance Balance: {actual_binance_balance} | Local Initial Deposit: {self.initial_deposit}")
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: Failed to fetch Testnet account balance. Valid Testnet credentials are REQUIRED. Reason: {e}")

        # Initialize core components
        self.cost_engine = CostEngine.get_binance_taker_config()
        self.profitability_gate = ProfitabilityGate(cost_engine=self.cost_engine)
        self.risk_gate = RiskGate(starting_balance=self.starting_equity)
        
        # State
        self.current_equity = self.starting_equity
        self.service_start_time = datetime.datetime.utcnow().isoformat() + "Z"
        self.active_positions = {} # Keep track of open OCOs per symbol
        self.symbol_filters = {}
        self.last_evaluation = {}
        self.safety_halt = False
        self.observe_only = False
        self.cooldowns = {} # symbol -> timestamp (float)
        
        # Phase 3: Restore daily risk state from ledger if restarting mid-day
        self._restore_daily_risk_state()
        
        self.sync_exchange_state(account)
        
        # Thread safety for concurrent websocket callbacks
        self.lock = threading.Lock()
        
        # Phase 6: Multi-Asset Opportunity Ranking Queue
        self.opportunity_pool = queue.Queue()
        self.pool_event = threading.Event()
        self._execution_thread = threading.Thread(target=self.execution_loop, daemon=True)
        self._execution_thread.start()
        
        # Stats for dashboard (Strict Signal Funnel)
        self.stats = {
            "TOTAL_SIGNALS": 0,
            "PROFITABILITY_REJECTED": 0,
            "RISK_REJECTED": 0,
            "COOLDOWN_REJECTED": 0,
            "JIT_REJECTED": 0,
            "OTHER_REJECTED": 0,
            "QUALIFIED": 0,
            "ORDERS_SUBMITTED": 0,
            "EXECUTION_REJECTED": 0,
            "ORDERS_FILLED": 0,
            "ORDERS_FAILED": 0,
            "symbols_scanned": 0,
            "ticks_received_per_symbol": {},
            "candles_constructed_per_symbol": {},
            "latest_candle_timestamp": {},
            "strategy_evaluations": 0,
            "buy_predictions": 0,
            "sell_predictions": 0,
            "TOTAL_CANDLES": 0
        }
        self.stats.update({
            "BUY_SIGNALS": 0,
            "SELL_SIGNALS": 0,
            "HOLD_SIGNALS": 0
        })

        if os.path.exists(TESTNET_PORTFOLIO_FILE):
            try:
                with open(TESTNET_PORTFOLIO_FILE, "r") as f:
                    state = json.load(f)
                    if "scanner_stats" in state:
                        saved_stats = state["scanner_stats"]
                        for k in self.stats.keys():
                            if k in saved_stats and isinstance(saved_stats[k], (int, float)):
                                self.stats[k] = saved_stats[k]
            except Exception as e:
                logger.error(f"[SERVICE] Failed to restore persistent stats: {e}")

    def _restore_daily_risk_state(self):
        """Parse today's ledger entries to accurately restore the daily realized PnL limit with strict provenance checking."""
        try:
            ledger_file = os.getenv("TESTNET_LEDGER_FILE", TESTNET_LEDGER_FILE)
            if not os.path.exists(ledger_file):
                return
            today_str = datetime.datetime.utcnow().date().isoformat()
            daily_loss = 0.0
            seen_exit_ids = set()
            
            with open(ledger_file, 'r') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        record = json.loads(line)
                        source = record.get("source", "")
                        strategy = record.get("strategy", "")
                        entry_oid = record.get("entry_order_id")
                        exit_oid = record.get("exit_order_id")

                        # Reject synthetic/test/paper records
                        if source == "TEST" or strategy == "TEST" or source == "PAPER":
                            continue
                        if not (entry_oid or exit_oid):
                            continue
                        if source not in ["BINANCE_EXECUTION", "RECOVERY_FROM_BINANCE"]:
                            if "RECOVERED" in str(record.get("signal_id", "")) or "RECOVERED" in str(strategy):
                                source = "RECOVERY_FROM_BINANCE"
                            else:
                                continue

                        # Prevent duplicate accounting of identical completed trades
                        exit_id = str(exit_oid) if exit_oid else (str(record.get("exit_client_id")) if record.get("exit_client_id") else None)
                        if exit_id:
                            if exit_id in seen_exit_ids:
                                continue
                            seen_exit_ids.add(exit_id)

                        if "CLOSE" in record.get("action", ""):
                            # Parse date from timestamp
                            trade_date = record.get("timestamp", "").split("T")[0]
                            if trade_date == today_str:
                                pnl = record.get("pnl", record.get("net_pnl", 0.0))
                                daily_loss += pnl
                    except Exception:
                        pass
                        
            if daily_loss != 0:
                self.risk_gate.daily_realized_loss = daily_loss
                logger.info(f"[SERVICE] Restored daily realized PnL: ${daily_loss:.2f}")
        except Exception as e:
            logger.error(f"[SERVICE] Error restoring daily risk state: {e}")

    def sync_exchange_state(self, account):
        """Phase 2: Reconcile local active_trades with Binance open orders and balances."""
        try:
            open_orders = self.client.get_open_orders()
            # Find any non-USDT balances
            assets = [item for item in account['balances'] if float(item['free']) > 0 or float(item['locked']) > 0]
            open_symbols_from_assets = set([a['asset'] + "USDT" for a in assets if a['asset'] != "USDT"])
            
            from execution import _load_active_trades
            try:
                active_trades = _load_active_trades()
            except Exception as e:
                logger.error(f"[RECONCILIATION] Failed to load active trades: {e}")
                active_trades = []
                
            local_symbols = set([t['symbol'] for t in active_trades])
            protected_symbols = set([o['symbol'] for o in open_orders])
            
            # 1. Cancel any floating OCOs (orders open, but no position balance)
            floating_ocos = protected_symbols - open_symbols_from_assets
            if floating_ocos:
                for sym in floating_ocos:
                    logger.warning(f"[RECOVERY] 🚨 Floating OCO detected without position for {sym}. Cancelling to prevent accidental entry.")
                    sym_orders = [o for o in open_orders if o['symbol'] == sym]
                    for o in sym_orders:
                        if o.get('orderListId', -1) > 0:
                            try:
                                self.client.cancel_order(symbol=sym, orderId=o['orderId'])
                            except Exception as e:
                                pass

            # 2. Reconstruct missing local state for open, protected positions
            missing_local_but_has_position_and_oco = protected_symbols.intersection(open_symbols_from_assets) - local_symbols
            if missing_local_but_has_position_and_oco:
                from execution import _save_active_trades
                for sym in missing_local_but_has_position_and_oco:
                    logger.info(f"[RECOVERY] 🔄 Discovered orphaned open position on exchange for {sym}. Reconstructing local state...")
                    
                    sym_orders = [o for o in open_orders if o['symbol'] == sym]
                    oco_id, tp_id, sl_id = None, None, None
                    tp_price, sl_price = 0.0, 0.0
                    
                    for o in sym_orders:
                        if o.get('orderListId', -1) > 0:
                            oco_id = o['orderListId']
                            if o['type'] == 'LIMIT_MAKER':
                                tp_id = o['orderId']
                                tp_price = float(o['price'])
                            elif o['type'] == 'STOP_LOSS_LIMIT':
                                sl_id = o['orderId']
                                sl_price = float(o['stopPrice'])
                                
                    if not oco_id:
                        continue
                        
                    # Find the entry order
                    try:
                        recent_trades = self.client.get_my_trades(symbol=sym, limit=50)
                        # Assume the most recent trade not associated with the OCO is the entry
                        entry_trade = None
                        for t in reversed(recent_trades):
                            if t['orderId'] not in (tp_id, sl_id):
                                entry_trade = t
                                break
                                
                        if not entry_trade:
                            logger.error(f"[RECOVERY] No recent trades found for {sym}, cannot determine entry.")
                            continue
                            
                        entry_price = float(entry_trade['price'])
                        entry_qty = float(entry_trade['qty'])
                        entry_side = "BUY" if entry_trade['isBuyer'] else "SELL"
                        
                        recovered_trade = {
                            "strategy": ACTIVE_STRATEGY,
                            "symbol": sym,
                            "side": entry_side,
                            "quantity": entry_qty,
                            "entry_price": entry_price,
                            "sl_price": sl_price,
                            "tp_price": tp_price,
                            "oco_id": oco_id,
                            "tp_order_id": tp_id,
                            "sl_order_id": sl_id,
                            "status": "OPEN",
                            "state": 1,
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                            "entry_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                            "signal_id": "RECOVERED_" + str(uuid.uuid4())[:8],
                            "entry_client_id": "RECOVERED_" + str(uuid.uuid4())[:8]
                        }
                        active_trades.append(recovered_trade)
                        logger.info(f"[RECOVERY] ✅ Successfully reconstructed state for {sym}: {recovered_trade}")
                    except Exception as e:
                        logger.error(f"[RECOVERY] Failed to reconstruct state for {sym}: {e}")
                        
                # Save the recovered state
                _save_active_trades(active_trades)

            # 3. Ensure assets we manage have an open OCO or limit order protecting them
            unprotected = open_symbols_from_assets - protected_symbols - floating_ocos
            managed_unprotected = unprotected.intersection(set([t['symbol'] for t in active_trades]))
            
            if managed_unprotected:
                logger.critical(f"[RECONCILIATION] 🚨 SAFETY HALT: Mismatch detected! Managed assets unprotected: {managed_unprotected}")
                self.safety_halt = True
            elif unprotected:
                logger.warning(f"[RECONCILIATION] Unmanaged assets found (dust/manual): {unprotected}")
                
            # 4. Sync local active_positions dictionary
            for t in active_trades:
                self.active_positions[t['symbol']] = t
                
        except Exception as e:
            logger.critical(f"[RECONCILIATION] 🚨 SAFETY HALT: Failed to sync exchange state: {e}")
            self.safety_halt = True

    def _save_state(self):
        # Format datetime safely
        last_m = {}
        if hasattr(self, 'scanner'):
            for k, v in self.scanner.last_market_update.items():
                last_m[k] = v.isoformat() + "Z" if isinstance(v, datetime.datetime) else str(v)
            
            # Populate diagnostics
            self.stats["ticks_received_per_symbol"] = getattr(self.scanner, 'tick_counts', {})
            self.stats["candles_constructed_per_symbol"] = {k: len(v) for k, v in self.scanner.candle_cache.items()}
            self.stats["latest_candle_timestamp"] = {k: str(v.index[-1] if not v.empty else "") for k, v in self.scanner.candle_cache.items()}
                
        state = {
            "initial_deposit": self.initial_deposit,
            "service_start_time": getattr(self, 'service_start_time', datetime.datetime.utcnow().isoformat() + "Z"),
            "cash": self.current_equity,
            "equity": self.current_equity,
            "realized_pnl": getattr(self, 'total_reconciled_pnl', self.risk_gate.daily_realized_loss),
            "used_margin": sum([p.get('quantity', 0) * p.get('entry_price', 0) for p in self.active_positions.values()]),
            "fees": getattr(self, 'total_reconciled_fees', 0.0),
            "funding": 0.0,
            "open_positions": len(self.active_positions),
            "positions": self.active_positions,
            "max_drawdown": (self.starting_equity - self.current_equity) / self.starting_equity if self.starting_equity > 0 else 0,
            "scanner_stats": {
                **self.stats,
                "symbols": self.scanner.symbols if hasattr(self, 'scanner') else [],
                "last_market_update": last_m,
                "last_evaluation": self.last_evaluation
            }
        }
        try:
            tmp_file = TESTNET_PORTFOLIO_FILE + ".tmp"
            with open(tmp_file, "w") as f:
                json.dump(state, f)
            os.replace(tmp_file, TESTNET_PORTFOLIO_FILE)
            
            # Record periodic equity history snapshot (at most once every 60s)
            now_ts = time.time()
            if now_ts - getattr(self, 'last_equity_snapshot', 0) >= 60:
                self.last_equity_snapshot = now_ts
                hist_file = os.getenv("TESTNET_EQUITY_HISTORY_FILE", "testnet_equity_history.jsonl")
                snap = {
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "equity": self.current_equity,
                    "balance": self.current_equity
                }
                with open(hist_file, "a") as hf:
                    hf.write(json.dumps(snap) + "\n")
        except Exception as e:
            logger.error(f"[SERVICE] Failed to save state atomically: {e}")

    def on_candle_closed(self, symbol, df, data_health_status="OK"):
        """Callback invoked by MarketScanner when a new candle closes."""
        if self.safety_halt:
            logger.warning(f"[SERVICE] Scanning halted due to RECONCILIATION MISMATCH. Rejecting {symbol} signal.")
            return

        with self.lock:
            try:
                self.stats["TOTAL_CANDLES"] += 1
                if df.empty or len(df) < 20:
                    return
                    
                df = add_indicators(df)
                current_price = df['close'].iloc[-1]
                
                self.last_evaluation[symbol] = datetime.datetime.utcnow().isoformat() + "Z"
                self.stats["strategy_evaluations"] += 1
                
                signal_result = get_signal(df)
                # Unpack from SignalResult namedtuple or legacy tuple — never assign fake confidence
                side = getattr(signal_result, 'side', signal_result[0] if signal_result else None)
                sl   = getattr(signal_result, 'sl',   signal_result[1] if signal_result else None)
                tp   = getattr(signal_result, 'tp',   signal_result[2] if signal_result else None)

                if not side:
                    self.stats["HOLD_SIGNALS"] += 1
                    return

                if side == "BUY":
                    self.stats["BUY_SIGNALS"] += 1
                    self.stats["buy_predictions"] += 1
                elif side == "SELL":
                    self.stats["SELL_SIGNALS"] += 1
                    self.stats["sell_predictions"] += 1

                # Generate deterministic client order ID for duplicate protection
                candle_timestamp = df.index[-1]
                deterministic_str = f"{symbol}_{side}_{candle_timestamp}"
                signal_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, deterministic_str))

                self.stats["TOTAL_SIGNALS"] += 1

                # 1. Profitability Gate — pass the full signal_result so the gate can
                #    read strategy_type and win_rate_prior; never invent a confidence float.
                passed_profit, p_metrics = self.profitability_gate.evaluate_signal(
                    symbol, side, current_price, sl, tp, signal_result
                )
                
                if not passed_profit:
                    self.stats["PROFITABILITY_REJECTED"] += 1
                    self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", p_metrics["reason"])
                    return
                    
                # Phase 6: Push to Opportunity Pool
                candidate = {
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "side": side,
                    "sl": sl,
                    "tp": tp,
                    "metrics": p_metrics,
                    "signal_result": signal_result,   # preserved for JIT re-validation
                    "timestamp": datetime.datetime.utcnow().timestamp()
                }
                self.opportunity_pool.put(candidate)
                self.pool_event.set()
                self.log_opportunity(signal_id, symbol, side, p_metrics, "QUALIFIED", "ADDED_TO_POOL")
                
            except Exception as e:
                logger.error(f"[SERVICE] Error processing signal for {symbol}: {e}")
            finally:
                self._save_state()

    def execution_loop(self):
        """Phase 6: Multi-Asset Opportunity Ranking and Execution"""
        while True:
            self.pool_event.wait()
            self.pool_event.clear()
            
            # Tiny batch window to allow concurrent websocket threads to finish queueing
            time.sleep(0.05) 
            
            candidates = []
            while not self.opportunity_pool.empty():
                try:
                    candidates.append(self.opportunity_pool.get_nowait())
                except queue.Empty:
                    break
                    
            if not candidates:
                continue
                
            # Rank candidates by Expected Net Edge (Descending)
            candidates.sort(key=lambda x: x["metrics"]["expected_net_return"], reverse=True)
            
            for candidate in candidates:
                symbol = candidate["symbol"]
                side = candidate["side"]
                signal_id = candidate["signal_id"]
                p_metrics = candidate["metrics"]
                sl = candidate["sl"]
                tp = candidate["tp"]
                
                with self.lock:
                    # Enforce per-symbol cooldown (60 seconds)
                    now_ts = datetime.datetime.utcnow().timestamp()
                    if symbol in self.cooldowns and now_ts - self.cooldowns[symbol] < 60:
                        self.stats["COOLDOWN_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "ON_COOLDOWN")
                        continue
                        
                    # Re-validate with absolute latest price
                    if not hasattr(self, 'scanner') or symbol not in self.scanner.candle_cache:
                        self.stats["JIT_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "NO_MARKET_DATA")
                        continue
                        
                    df = self.scanner.candle_cache[symbol]
                    if df.empty:
                        self.stats["JIT_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "EMPTY_MARKET_DATA")
                        continue
                        
                    current_price = df['close'].iloc[-1]
                    data_health = self.scanner.data_health_status.get(symbol, "OK")
                    
                    # Re-validate Profitability Gate (Price may have moved)
                    # Pass signal_result from original candidate — preserves strategy_type metadata.
                    passed_profit, fresh_metrics = self.profitability_gate.evaluate_signal(
                        symbol, side, current_price, sl, tp,
                        candidate.get("signal_result", p_metrics["prob_win"])
                    )
                    
                    if not passed_profit:
                        self.stats["JIT_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "REJECTED", f"REVALIDATION_FAILED: {fresh_metrics['reason']}")
                        continue
                    
                    # Sizing
                    filters = self.symbol_filters.get(symbol, {})
                    qty = self.risk_gate.calculate_position_size(self.current_equity, current_price, sl, filters)
                    
                    if qty < 0.00000001:
                        self.stats["RISK_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "REJECTED", "MIN_NOTIONAL_OR_ZERO_QTY")
                        continue
                        
                    # Risk Gate
                    passed_risk, r_reason, r_details = self.risk_gate.evaluate_risk(
                        symbol, side, self.current_equity, self.active_positions, qty, current_price, data_health
                    )
                    
                    if not passed_risk:
                        self.stats["RISK_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "REJECTED", r_reason)
                        continue
                        
                    # Execute
                    if self.observe_only:
                        self.stats["OTHER_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "ACCEPTED (OBSERVE ONLY)", "DEGRADED_PERFORMANCE_HALT")
                        continue
                        
                    self.stats["QUALIFIED"] += 1
                    self.log_opportunity(signal_id, symbol, side, fresh_metrics, "ACCEPTED", "ALL_GATES_PASSED")
                    logger.info(f"[SERVICE] Executing {side} {qty} {symbol}")
                    try:
                        order_res = place_market_order(ACTIVE_STRATEGY, side, symbol, quantity=qty, sl=sl, tp=tp, client_order_id=signal_id)
                        if order_res:
                            self.stats["ORDERS_SUBMITTED"] += 1
                            self.stats["ORDERS_FILLED"] += 1
                            actual_price = order_res.get("_actual_price", current_price)
                            executed_qty = order_res.get("_executed_qty", qty)
                            
                            self.active_positions[symbol] = {
                                "strategy": ACTIVE_STRATEGY,
                                "symbol": symbol,
                                "side": side,
                                "quantity": executed_qty,
                                "entry_price": actual_price,
                                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                                "sl": sl,
                                "tp": tp,
                                "status": "OPEN",
                                "entry_client_id": signal_id
                            }
                            self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                        else:
                            self.stats["EXECUTION_REJECTED"] += 1
                            self.log_opportunity(signal_id, symbol, side, fresh_metrics, "FAILED", "LOCAL_ORDER_BLOCKED", current_price=current_price)
                            self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                    except ZeroFillError as zfe:
                        self.stats["ORDERS_SUBMITTED"] += 1
                        self.stats["ORDERS_FAILED"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "FAILED", "ZERO_FILL", current_price=current_price)
                        self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                    except BinanceAPIException as e:
                        self.stats["ORDERS_SUBMITTED"] += 1
                        self.stats["ORDERS_FAILED"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "FAILED", f"BINANCE_API_ERROR_{e.status_code}", current_price=current_price)
                        self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                    except Exception as e:
                        # Unhandled execution errors
                        self.stats["ORDERS_SUBMITTED"] += 1
                        self.stats["ORDERS_FAILED"] += 1
                        logger.error(f"[SERVICE] Unhandled execution error: {e}")
                        self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                        
                    self._save_state()

    def log_opportunity(self, signal_id, symbol, side, metrics, decision, reason, current_price=0.0):
        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "signal_id": signal_id,
            "symbol": symbol,
            "side": side,
            "current_price": current_price or metrics.get("current_price", 0.0),
            "confidence": metrics.get("confidence"),
            "predicted_move": metrics.get("predicted_move"),
            "holding_horizon": metrics.get("holding_horizon"),
            "expected_gross_return": metrics.get("gross_edge") or metrics.get("expected_gross_return"),
            "expected_net_return": metrics.get("expected_net_return"),
            "estimated_fees": metrics.get("estimated_fees", 0.0),
            "decision": decision,
            "reason": reason
        }
        try:
            with open(TESTNET_OPPORTUNITY_LOG, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except:
            pass

    def position_monitor_loop(self):
        """Continuously reconciles active positions against Binance."""
        while True:
            try:
                # 1. Update Equity
                account = self.client.get_account()
                usdt_balance = next((item for item in account['balances'] if item['asset'] == 'USDT'), None)
                actual_binance_balance = 0.0
                if usdt_balance:
                    actual_binance_balance = float(usdt_balance['free']) + float(usdt_balance['locked'])
                    
                # 2. Reconcile Positions
                from execution import monitor_open_trades, _load_active_trades
                # This queries Binance for OCO status and updates active_trades.json and ledger
                monitor_open_trades()
                
                # 3. Calculate total reconstructable PnL from the Ledger
                total_reconstructable_pnl = 0.0
                if os.path.exists(TESTNET_LEDGER_FILE):
                    with open(TESTNET_LEDGER_FILE, "r") as f:
                        for line in f:
                            if not line.strip(): continue
                            try:
                                record = json.loads(line)
                                total_reconstructable_pnl += float(record.get("net_pnl", 0.0))
                            except:
                                pass
                
                self.local_portfolio_balance = self.initial_deposit + total_reconstructable_pnl
                
                import config
                tolerance = getattr(config, "RECONCILIATION_TOLERANCE", 1.0)
                mismatch = abs(self.local_portfolio_balance - actual_binance_balance)
                
                if mismatch > tolerance:
                    if not self.safety_halt:
                        logger.critical(f"[SERVICE] 🚨 SAFETY HALT: Balance Mismatch = {mismatch:.4f} USDT "
                                      f"(Local: {self.local_portfolio_balance:.4f} vs Binance: {actual_binance_balance:.4f}). "
                                      f"Stopping new entries.")
                    self.safety_halt = True
                    # NEVER silently overwrite the authoritative balance! 
                    # Maintain local tracking for metrics.
                    self.current_equity = self.local_portfolio_balance 
                else:
                    self.current_equity = actual_binance_balance # Update smoothly if within tolerance
                    
                # Sync memory with disk
                try:
                    active = _load_active_trades()
                    new_active_positions = {t['symbol']: t for t in active}
                    
                    # Call exact authoritative reconstruction
                    self._rebuild_testnet_state()
                    
                    # Update local risk gate if a trade closed
                    for sym in list(self.active_positions.keys()):
                        if sym not in new_active_positions:
                            logger.info(f"[SERVICE] Position for {sym} closed. Risk bounds will update on next PnL parse.")
                            self._restore_daily_risk_state()
                            
                    self.active_positions = new_active_positions
                except Exception as e:
                    logger.error(f"[SERVICE] Failed to sync active positions: {e}")
                
                # Phase 5: Degradation Control
                self._check_degradation()
                
                with self.lock:
                    self._save_state()
            except Exception as e:
                logger.error(f"[MONITOR] Error: {e}")
            time.sleep(30)

    def _rebuild_testnet_state(self):
        """Authoritatively reconstructs exact trade history, PnL, and fees directly from Binance API"""
        try:
            from config_strategy import ADX_EMA_STRATEGY
            strategy_assets = ADX_EMA_STRATEGY.get("OOS_VALIDATED_ASSETS", ["BTCUSDT"])
            symbols_to_check = set(list(self.active_positions.keys()) + strategy_assets)

            all_filled_orders = []
            all_trades_by_order = {}

            # Fetch orders and trades for all relevant symbols
            for sym in symbols_to_check:
                try:
                    orders = self.client.get_all_orders(symbol=sym, limit=500)
                    all_filled_orders.extend([o for o in orders if o['status'] == 'FILLED'])
                    
                    trades = self.client.get_my_trades(symbol=sym, limit=500)
                    for t in trades:
                        oid = str(t['orderId'])
                        if oid not in all_trades_by_order:
                            all_trades_by_order[oid] = []
                        all_trades_by_order[oid].append(t)
                except Exception as sym_err:
                    logger.error(f"[SERVICE] Error fetching history for {sym}: {sym_err}")

            total_gross_pnl = 0.0
            total_fees = 0.0
            completed_trades = []

            # Reconstruct ledger per symbol
            for sym in symbols_to_check:
                sym_orders = sorted([o for o in all_filled_orders if o['symbol'] == sym], key=lambda x: x['time'])
                
                current_position_qty = 0.0
                current_position_side = None
                open_entries = []

                for o in sym_orders:
                    oid = str(o['orderId'])
                    side = o['side']
                    qty = float(o['executedQty'])
                    quote_qty = float(o['cummulativeQuoteQty'])
                    avg_price = quote_qty / qty if qty > 0 else 0.0
                    
                    order_fees = sum(float(f['commission']) for f in all_trades_by_order.get(oid, []))
                    total_fees += order_fees
                    
                    if current_position_qty == 0.0:
                        current_position_side = side
                        current_position_qty = qty
                        open_entries.append({"time": o['time'], "order_id": oid, "side": side, "qty": qty, "price": avg_price, "fees": order_fees})
                    else:
                        if side == current_position_side:
                            current_position_qty += qty
                            open_entries.append({"time": o['time'], "order_id": oid, "side": side, "qty": qty, "price": avg_price, "fees": order_fees})
                        else:
                            close_qty_remaining = qty
                            while close_qty_remaining > 1e-8 and len(open_entries) > 0:
                                entry = open_entries[0]
                                match_qty = min(entry['qty'], close_qty_remaining)
                                
                                gross_pnl = (avg_price - entry['price']) * match_qty if entry['side'] == 'BUY' else (entry['price'] - avg_price) * match_qty
                                total_gross_pnl += gross_pnl
                                
                                completed_trades.append({
                                    "signal_id": f"RECOVERED_{entry['order_id']}",
                                    "symbol": sym,
                                    "strategy": "RECOVERED",
                                    "source": "RECOVERY_FROM_BINANCE",
                                    "side": entry['side'],
                                    "entry_order_id": entry['order_id'],
                                    "entry_price": entry['price'],
                                    "entry_executed_quantity": match_qty,
                                    "entry_fee": entry['fees'] * (match_qty / entry['qty']),
                                    "exit_order_id": oid,
                                    "exit_price": avg_price,
                                    "exit_executed_quantity": match_qty,
                                    "exit_fee": order_fees * (match_qty / qty),
                                    "exit_reason": "RECOVERED",
                                    "gross_pnl": gross_pnl,
                                    "total_fees": (entry['fees'] * (match_qty / entry['qty'])) + (order_fees * (match_qty / qty)),
                                    "net_pnl": gross_pnl - (entry['fees'] * (match_qty / entry['qty'])) - (order_fees * (match_qty / qty)),
                                    "pnl": gross_pnl - (entry['fees'] * (match_qty / entry['qty'])) - (order_fees * (match_qty / qty)),
                                    "fees": (entry['fees'] * (match_qty / entry['qty'])) + (order_fees * (match_qty / qty)),
                                    "entry_timestamp": datetime.datetime.fromtimestamp(entry['time']/1000).isoformat() + "Z",
                                    "exit_timestamp": datetime.datetime.fromtimestamp(o['time']/1000).isoformat() + "Z",
                                    "timestamp": datetime.datetime.fromtimestamp(o['time']/1000).isoformat() + "Z",
                                    "action": f"CLOSED_{'WIN' if gross_pnl > 0 else 'LOSS'}",
                                    "quantity": match_qty
                                })
                                
                                entry['qty'] -= match_qty
                                close_qty_remaining -= match_qty
                                if entry['qty'] < 1e-8: open_entries.pop(0)
                                    
                            current_position_qty -= qty
                            if current_position_qty < 1e-8:
                                current_position_qty = 0.0
                                current_position_side = None

            # 1. Append missing trades to the ledger (do not overwrite)
            import os
            ledger_file = os.getenv("TESTNET_LEDGER_FILE", TESTNET_LEDGER_FILE)
            existing_exit_ids = set()
            if os.path.exists(ledger_file):
                with open(ledger_file, "r") as f:
                    for line in f:
                        if not line.strip(): continue
                        try:
                            record = json.loads(line)
                            if "exit_order_id" in record and record["exit_order_id"]:
                                existing_exit_ids.add(str(record["exit_order_id"]))
                            elif "exit_client_id" in record and record["exit_client_id"]:
                                existing_exit_ids.add(str(record["exit_client_id"]))
                        except:
                            pass
            
            # Atomic append
            from testnet_engine.protection import LEDGER_WRITE_LOCK
            with LEDGER_WRITE_LOCK:
                with open(ledger_file, "a") as f:
                    for ct in completed_trades:
                        if str(ct["exit_order_id"]) not in existing_exit_ids:
                            f.write(json.dumps(ct) + "\n")
                            existing_exit_ids.add(str(ct["exit_order_id"]))
                    
            # 2. Update Risk bounds mathematically
            self.risk_gate.daily_realized_loss = sum(t['net_pnl'] for t in completed_trades)
            self.total_reconciled_fees = total_fees
            self.total_reconciled_pnl = total_gross_pnl - total_fees
            
        except Exception as e:
            logger.error(f"[SERVICE] Authoritative Rebuild Failed: {e}")

    def _check_degradation(self):
        """Phase 5: Automatically switch to OBSERVE-ONLY if strategy degrades."""
        import config
        window = getattr(config, "DEGRADATION_WINDOW", 20)
        min_win_rate = getattr(config, "MIN_WIN_RATE_THRESHOLD", 0.35)
        max_pred_err = getattr(config, "MAX_PREDICTION_ERROR", 0.02)
        
        if not hasattr(self, 'ledger_file'):
            self.ledger_file = TESTNET_LEDGER_FILE
            
        if not os.path.exists(self.ledger_file):
            return
            
        closed_trades = []
        try:
            with open(self.ledger_file, 'r') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        record = json.loads(line)
                        if "CLOSE" in record.get("action", ""):
                            closed_trades.append(record)
                    except: pass
        except: return
        
        if len(closed_trades) < window:
            return
            
        recent = closed_trades[-window:]
        wins = sum(1 for t in recent if t.get("pnl", 0) > 0)
        win_rate = wins / window
        
        # Calculate prediction error if logged
        # Expected vs Actual return (assuming 1% = 0.01)
        # Note: We need expected_net_return logged in the ledger during close
        
        if win_rate < min_win_rate:
            if not self.observe_only:
                logger.warning(f"[SERVICE] 🚨 STRATEGY DEGRADATION DETECTED. Win rate {win_rate:.2%} < {min_win_rate:.2%}. Switching to OBSERVE-ONLY mode.")
                self.observe_only = True
                
        # Additional checks can be added if ledger captures the initial expected_net_return

    def run(self):
        # 1. Discover Symbols
        discovery = SymbolDiscoveryService()
        self.symbol_filters = discovery.discover_eligible_symbols(min_quote_volume=1_000_000)
        symbol_list = list(self.symbol_filters.keys())
        self.stats["symbols_scanned"] = len(symbol_list)
        
        # 2. Train ML Strategy
        if ACTIVE_STRATEGY == "ml":
            try:
                from strategy_ml import train
                from data import get_candles, add_indicators
                logger.info("[SERVICE] Pre-training ML strategy on BTCUSDT...")
                
                # Fetch 2500 candles and strictly split to eliminate look-ahead data leakage
                all_df = add_indicators(get_candles("BTCUSDT", TIMEFRAME, 2500))
                train_df = all_df.iloc[:-500]
                val_df = all_df.iloc[-500:]
                
                train(train_df, val_df)
                logger.info("[SERVICE] ML strategy trained successfully.")
            except Exception as e:
                logger.error(f"[SERVICE] Failed to train ML strategy: {e}")
                
        # 3. Start Scanner
        self.scanner = MarketScanner(symbol_list, timeframe=TIMEFRAME)
        self.scanner.register_callback(self.on_candle_closed)
        self.scanner.start()
        
        # 3. Start Position Monitor Thread
        monitor_thread = threading.Thread(target=self.position_monitor_loop, daemon=True)
        monitor_thread.start()
        
        # 4. Keep Main Thread Alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("[SERVICE] Shutting down...")
            self.scanner.stop()

if __name__ == "__main__":
    service = TestnetService()
    service.run()
