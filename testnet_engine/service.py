import os
import time
import json
import uuid
import datetime
import threading
import queue
from config import TRADING_MODE, ACTIVE_STRATEGY, TIMEFRAME
from logger import get_logger
from data import add_indicators
from strategy_ml import get_signal
from execution import place_market_order, get_exchange_client
from research_phase9.cost_engine import CostEngine
from testnet_engine.discovery import SymbolDiscoveryService
from testnet_engine.market_scanner import MarketScanner
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate

logger = get_logger("service")

TESTNET_LEDGER_FILE = "testnet_trade_ledger.jsonl"
TESTNET_OPPORTUNITY_LOG = "testnet_opportunity_log.jsonl"

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
            self.starting_equity = float(usdt_balance['free']) + float(usdt_balance['locked'])
            logger.info(f"[SERVICE] Starting Equity: {self.starting_equity}")
        except Exception as e:
            raise RuntimeError(f"CRITICAL ERROR: Failed to fetch Testnet account balance. Valid Testnet credentials are REQUIRED. Reason: {e}")

        # Initialize core components
        self.cost_engine = CostEngine.get_binance_taker_config()
        self.profitability_gate = ProfitabilityGate(cost_engine=self.cost_engine)
        self.risk_gate = RiskGate(starting_balance=self.starting_equity)
        
        # State
        self.current_equity = self.starting_equity
        self.active_positions = {} # Keep track of open OCOs per symbol
        self.symbol_filters = {}
        self.last_evaluation = {}
        self.safety_halt = False
        self.observe_only = False
        
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
        
        # Stats for dashboard
        self.stats = {
            "symbols_scanned": 0,
            "signals_detected": 0,
            "signals_rejected": 0,
            "orders_submitted": 0,
            "orders_filled": 0
        }

    def _restore_daily_risk_state(self):
        """Parse today's ledger entries to accurately restore the daily realized PnL limit."""
        try:
            if not os.path.exists(TESTNET_LEDGER_FILE):
                return
            today_str = datetime.datetime.utcnow().date().isoformat()
            daily_loss = 0.0
            
            with open(TESTNET_LEDGER_FILE, 'r') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        record = json.loads(line)
                        if "CLOSE" in record.get("action", ""):
                            # Parse date from timestamp
                            trade_date = record.get("timestamp", "").split("T")[0]
                            if trade_date == today_str:
                                pnl = record.get("pnl", 0.0)
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
            except Exception:
                active_trades = []
                
            local_symbols = set([t['symbol'] for t in active_trades])
            
            # 1. Ensure all assets we hold have an open OCO or limit order protecting them
            protected_symbols = set([o['symbol'] for o in open_orders])
            unprotected = open_symbols_from_assets - protected_symbols
            if unprotected:
                logger.critical(f"[RECONCILIATION] 🚨 SAFETY HALT: Mismatch detected! Binance holds unprotected balances for {unprotected} with no local/exchange protection.")
                self.safety_halt = True
                
            # 2. Sync local active_positions dictionary
            for t in active_trades:
                self.active_positions[t['symbol']] = t
                
        except Exception as e:
            logger.critical(f"[RECONCILIATION] 🚨 SAFETY HALT: Failed to sync exchange state: {e}")
            self.safety_halt = True

    def _save_state(self):
        state = {
            "cash": self.current_equity,
            "equity": self.current_equity,
            "realized_pnl": self.risk_gate.daily_realized_loss,
            "used_margin": sum([p.get('quantity', 0) * p.get('entry_price', 0) for p in self.active_positions.values()]),
            "fees": 0.0,
            "funding": 0.0,
            "open_positions": len(self.active_positions),
            "max_drawdown": (self.risk_gate.peak_equity - self.current_equity) / self.risk_gate.peak_equity if self.risk_gate.peak_equity > 0 else 0,
            "scanner_stats": {
                **self.stats,
                "symbols": self.scanner.symbols if hasattr(self, 'scanner') else [],
                "last_market_update": self.scanner.last_market_update if hasattr(self, 'scanner') else {},
                "last_evaluation": self.last_evaluation
            }
        }
        try:
            tmp_file = "testnet_portfolio.json.tmp"
            with open(tmp_file, "w") as f:
                json.dump(state, f)
            os.replace(tmp_file, "testnet_portfolio.json")
        except Exception as e:
            logger.error(f"[SERVICE] Failed to save state atomically: {e}")

    def on_candle_closed(self, symbol, df, data_health_status="OK"):
        """Callback invoked by MarketScanner when a new candle closes."""
        if self.safety_halt:
            logger.warning(f"[SERVICE] Scanning halted due to RECONCILIATION MISMATCH. Rejecting {symbol} signal.")
            return

        with self.lock:
            try:
                if df.empty or len(df) < 20:
                    return
                    
                df = add_indicators(df)
                current_price = df['close'].iloc[-1]
                
                self.last_evaluation[symbol] = datetime.datetime.utcnow().isoformat() + "Z"
                
                side, sl, tp, conf = get_signal(df)
                if not side:
                    return
                    
                # Generate deterministic client order ID for duplicate protection
                candle_timestamp = df.index[-1]
                deterministic_str = f"{symbol}_{side}_{candle_timestamp}"
                signal_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, deterministic_str))
                
                self.stats["signals_detected"] += 1
                
                # 1. Profitability Gate
                passed_profit, p_metrics = self.profitability_gate.evaluate_signal(symbol, side, current_price, sl, tp, conf)
                
                if not passed_profit:
                    self.stats["signals_rejected"] += 1
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
                    # Re-validate with absolute latest price
                    if not hasattr(self, 'scanner') or symbol not in self.scanner.candle_cache:
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "NO_MARKET_DATA")
                        continue
                        
                    df = self.scanner.candle_cache[symbol]
                    if df.empty:
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "EMPTY_MARKET_DATA")
                        continue
                        
                    current_price = df['close'].iloc[-1]
                    data_health = self.scanner.data_health_status.get(symbol, "OK")
                    
                    # Re-validate Profitability Gate (Price may have moved)
                    passed_profit, fresh_metrics = self.profitability_gate.evaluate_signal(
                        symbol, side, current_price, sl, tp, p_metrics["confidence"]
                    )
                    
                    if not passed_profit:
                        self.stats["signals_rejected"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "REJECTED", f"REVALIDATION_FAILED: {fresh_metrics['reason']}")
                        continue
                    
                    # Sizing
                    filters = self.symbol_filters.get(symbol, {})
                    qty = self.risk_gate.calculate_position_size(self.current_equity, current_price, sl, filters)
                    
                    if qty < 0.00000001:
                        self.stats["signals_rejected"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "REJECTED", "MIN_NOTIONAL_OR_ZERO_QTY")
                        continue
                        
                    # Risk Gate
                    passed_risk, r_reason, r_details = self.risk_gate.evaluate_risk(
                        symbol, side, self.current_equity, self.active_positions, qty, current_price, data_health
                    )
                    
                    if not passed_risk:
                        self.stats["signals_rejected"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "REJECTED", r_reason)
                        continue
                        
                    # Execute
                    if self.observe_only:
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "ACCEPTED (OBSERVE ONLY)", "DEGRADED_PERFORMANCE_HALT")
                        continue
                        
                    self.log_opportunity(signal_id, symbol, side, fresh_metrics, "ACCEPTED", "ALL_GATES_PASSED")
                    logger.info(f"[SERVICE] Executing {side} {qty} {symbol}")
                    
                    order_res = place_market_order(ACTIVE_STRATEGY, side, symbol, quantity=qty, sl=sl, tp=tp, client_order_id=signal_id)
                    if order_res:
                        self.stats["orders_submitted"] += 1
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
                            "entry_client_id": signal_id
                        }
                    else:
                        self.stats["signals_rejected"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "FAILED", "ORDER_FAILED")
                        
                    self._save_state()

    def log_opportunity(self, signal_id, symbol, side, metrics, decision, reason):
        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "signal_id": signal_id,
            "symbol": symbol,
            "side": side,
            "confidence": metrics.get("confidence"),
            "predicted_move": metrics.get("predicted_move"),
            "holding_horizon": metrics.get("holding_horizon"),
            "expected_gross_return": metrics.get("expected_gross_return"),
            "expected_net_return": metrics.get("expected_net_return"),
            "decision": decision,
            "reason": reason
        }
        with open(TESTNET_OPPORTUNITY_LOG, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    def position_monitor_loop(self):
        """Continuously reconciles active positions against Binance."""
        while True:
            try:
                # 1. Update Equity
                account = self.client.get_account()
                usdt_balance = next((item for item in account['balances'] if item['asset'] == 'USDT'), None)
                if usdt_balance:
                    self.current_equity = float(usdt_balance['free']) + float(usdt_balance['locked'])
                    
                # 2. Reconcile Positions (Simplified for demonstration)
                # In production, query client.get_open_orders() to see if OCOs hit.
                # If hit, move position from self.active_positions to testnet_trade_ledger.jsonl
                
                # Phase 5: Degradation Control
                self._check_degradation()
                
                with self.lock:
                    self._save_state()
            except Exception as e:
                logger.error(f"[MONITOR] Error: {e}")
            time.sleep(30)

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
                train_df = add_indicators(get_candles("BTCUSDT", TIMEFRAME, 2000))
                val_df = add_indicators(get_candles("BTCUSDT", TIMEFRAME, 500))
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
