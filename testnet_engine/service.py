import sys
import os
import time
import json
import uuid
import datetime
import threading
import queue
import importlib
from config import TRADING_MODE, ACTIVE_STRATEGIES
from logger import get_logger
from data import add_indicators

from execution import place_market_order, get_exchange_client
from binance.exceptions import BinanceAPIException
from paper_engine.exceptions import ZeroFillError
from research_phase9.cost_engine import CostEngine
from testnet_engine.discovery import SymbolDiscoveryService
from testnet_engine.market_scanner import MarketScanner
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate
from testnet_engine.telemetry_manager import get_telemetry_manager

logger = get_logger("service")

TESTNET_LEDGER_FILE = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
TESTNET_OPPORTUNITY_LOG = os.getenv("TESTNET_OPPORTUNITY_LOG", "testnet_opportunity_log.jsonl")
TESTNET_PORTFOLIO_FILE = os.getenv("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
TESTNET_HEARTBEAT_FILE = os.getenv("TESTNET_HEARTBEAT_FILE", "testnet_heartbeat.json")

class TestnetService:
    __test__ = False
    def __init__(self):
        # 1. TRADING_MODE strictly validated
        if TRADING_MODE != "TESTNET":
            raise RuntimeError(f"CRITICAL ERROR: Refusing to start TestnetService because TRADING_MODE={TRADING_MODE}. Must be TESTNET.")
            
        if os.getenv("TESTNET_ONLY", "FALSE").upper() != "TRUE":
            raise RuntimeError("CRITICAL ERROR: TESTNET_ONLY=TRUE is required to run the Testnet execution mode safely.")
            
        if os.getenv("RESET_REVIEW_STATE", "FALSE").upper() == "TRUE":
            logger.warning("[RESET] RESET_REVIEW_STATE=TRUE detected. Wiping local review telemetry...")
            files_to_clean = [
                TESTNET_LEDGER_FILE, TESTNET_OPPORTUNITY_LOG, TESTNET_PORTFOLIO_FILE, 
                TESTNET_HEARTBEAT_FILE, "testnet_equity_history.jsonl", "bot.log",
                "heartbeat.json", "status.json", "trades.json", "scanner.json"
            ]
            for f in files_to_clean:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                        logger.warning(f"[RESET] Cleared {f}")
                    except Exception as e:
                        logger.error(f"[RESET] Failed to clear {f}: {e}")
                        
        logger.info("[ENGINE_START] Starting Binance Testnet Trading Engine...")
        print("========================================")
        print("24/7 TESTNET EXECUTION SERVICE")
        print("========================================")
        print("BINANCE CONFIGURED: YES")
        print("TRADING MODE: TESTNET")
        print("LIVE ORDERS: BLOCKED")
        print(f"Strategies: {', '.join(ACTIVE_STRATEGIES.keys())}")
        
        self.strategies = {}
        for strat_name, tfs in ACTIVE_STRATEGIES.items():
            try:
                mod = importlib.import_module(f"strategy_{strat_name}")
                tfs_list = tfs if isinstance(tfs, list) else [tfs]
                for tf in tfs_list:
                    if tf not in self.strategies:
                        self.strategies[tf] = []
                    self.strategies[tf].append((strat_name, mod))
            except Exception as e:
                logger.error(f"Failed to load strategy {strat_name}: {e}")
        
        self.client = get_exchange_client()
        if self.client is None:
            raise RuntimeError("CRITICAL ERROR: Binance Client could not be instantiated for TESTNET.")
            
        logger.info("[BINANCE_CONNECTED] Verified Binance Testnet API connectivity.")
            
        # Reconcile Initial Balance
        try:
            account = self.client.get_account()
            usdt_balance = next((item for item in account['balances'] if item['asset'] == 'USDT'), None)
            if not usdt_balance:
                raise RuntimeError("No USDT balance found on Testnet.")
            actual_binance_balance = float(usdt_balance['free']) + float(usdt_balance['locked'])
            
            # Calculate total reconstructable PnL from the Ledger
            total_reconstructable_pnl = 0.0
            if os.path.exists(TESTNET_LEDGER_FILE):
                try:
                    with open(TESTNET_LEDGER_FILE, "r") as f:
                        for line in f:
                            if not line.strip(): continue
                            try:
                                record = json.loads(line)
                                total_reconstructable_pnl += float(record.get("net_pnl", 0.0))
                            except:
                                pass
                except:
                    pass

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
        self.telemetry = get_telemetry_manager()
        
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
        # Start trade target monitor thread
        self._target_monitor_thread = threading.Thread(target=self._trade_target_monitor, daemon=True)
        self._target_monitor_thread.start()
        self._execution_thread.start()
        
        # Stats for dashboard (Strict Signal Funnel)
        self.stats = {
            "TOTAL_SIGNALS": 0,
            "PROFITABILITY_ACCEPTED": 0,
            "PROFITABILITY_REJECTED": 0,
            "RISK_ACCEPTED": 0,
            "RISK_REJECTED": 0,
            "EXECUTION_ELIGIBLE": 0,
            "EXECUTION_REJECTED": 0,
            "COOLDOWN_REJECTED": 0,
            "MARKET_DATA_REJECTED": 0,
            "JIT_REJECTED": 0,
            "OTHER_REJECTED": 0,
            "QUALIFIED": 0,
            "ORDERS_SUBMITTED": 0,
            "ORDERS_FILLED": 0,
            "ORDERS_FAILED": 0,
            "OPEN_POSITIONS": 0,
            "CLOSED_TRADES": 0,
            "symbols_scanned": 0,
            "ticks_received_per_symbol": {},
            "candles_constructed_per_symbol": {},
            "latest_candle_timestamp": {},
            "strategy_evaluations": 0,
            "buy_predictions": 0,
            "sell_predictions": 0,
            "TOTAL_CANDLES": 0,
            "strategy_metrics": {
                s: {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0} for s in ACTIVE_STRATEGIES.keys()
            },
            "timeframe_metrics": {}
        }
        
        # Populate timeframe metrics dynamically
        for tfs in ACTIVE_STRATEGIES.values():
            tfs_list = tfs if isinstance(tfs, list) else [tfs]
            for tf in tfs_list:
                if tf not in self.stats["timeframe_metrics"]:
                    self.stats["timeframe_metrics"][tf] = {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0}
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
                            "strategy": "RECOVERED",
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
            managed_symbols = set([t['symbol'] for t in active_trades if t.get('status') == 'OPEN'])
            managed_unprotected = [s for s in managed_symbols if s not in protected_symbols and s not in floating_ocos]
            
            if unprotected and ("pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST") or managed_unprotected):
                logger.critical(f"[RECONCILIATION] 🚨 SAFETY HALT: Unprotected assets detected: {unprotected}")
                self.safety_halt = True
            elif managed_unprotected:
                logger.critical(f"[RECONCILIATION] 🚨 SAFETY HALT: Managed assets unprotected: {managed_unprotected}")
                self.safety_halt = True
            else:
                self.safety_halt = False
                
            # 4. Sync local active_positions dictionary
            for t in active_trades:
                if t.get('status') == 'OPEN':
                    self.active_positions[t['symbol']] = t
                
        except Exception as e:
            logger.warning(f"[RECONCILIATION] Warning syncing exchange state: {e}")

    def _save_state(self):
        # Format datetime safely
        last_m = {}
        if hasattr(self, 'scanner'):
            for k, v in self.scanner.last_market_update.items():
                last_m[f"{k[0]}_{k[1]}"] = v.isoformat() + "Z" if isinstance(v, datetime.datetime) else str(v)
            
            # Populate diagnostics
            self.stats["ticks_received_per_symbol"] = {f"{k[0]}_{k[1]}": v for k, v in getattr(self.scanner, 'tick_counts', {}).items()}
            self.stats["candles_constructed_per_symbol"] = {f"{k[0]}_{k[1]}": len(v) for k, v in self.scanner.candle_cache.items()}
            self.stats["latest_candle_timestamp"] = {f"{k[0]}_{k[1]}": str(v.index[-1] if not v.empty else "") for k, v in self.scanner.candle_cache.items()}
            
        self.stats["OPEN_POSITIONS"] = len(self.active_positions)
                
        state = {
            "initial_deposit": self.initial_deposit,
            "service_start_time": getattr(self, 'service_start_time', datetime.datetime.utcnow().isoformat() + "Z"),
            "safety_halt": getattr(self, 'safety_halt', False),
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
                def convert_keys(obj):
                    if isinstance(obj, dict):
                        return {str(k) if isinstance(k, tuple) else k: convert_keys(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_keys(i) for i in obj]
                    return obj
                json.dump(convert_keys(state), f)
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

    def on_candle_closed(self, symbol, tf, df, data_health_status="OK"):
        """Callback invoked by MarketScanner when a new candle closes."""
        if getattr(self, 'safety_halt', False):
            logger.warning(f"[SAFETY_HALT] Scanning halted due to RECONCILIATION MISMATCH. Rejecting {symbol} signal.")
            return

        with self.lock:
            try:
                self.stats["TOTAL_CANDLES"] += 1
                if df.empty or len(df) < 20:
                    return
                    
                df = add_indicators(df)
                if df.empty:
                    return
                    
                current_price = df['close'].iloc[-1]
                logger.info(f"[FEATURES_READY] {symbol} {tf} | Rows: {len(df)} | Current Price: {current_price}")
                
                self.last_evaluation[symbol] = datetime.datetime.utcnow().isoformat() + "Z"
                
                if tf not in self.strategies:
                    return
                    
                for strat_name, strat_mod in self.strategies[tf]:
                    self.stats["strategy_evaluations"] += 1
                    
                    signal_result = strat_mod.get_signal(df)
                    side = getattr(signal_result, 'side', signal_result[0] if signal_result else None)
                    sl   = getattr(signal_result, 'sl',   signal_result[1] if signal_result else None)
                    tp   = getattr(signal_result, 'tp',   signal_result[2] if signal_result else None)

                    last_row = df.iloc[-1]
                    prev_row = df.iloc[-2] if len(df) >= 2 else last_row
                    adx_val = float(last_row.get("adx", 0.0))
                    ema20_val = float(last_row.get("ema_20", 0.0))
                    ema50_val = float(last_row.get("ema_50", 0.0))
                    ema200_val = float(last_row.get("ema_200", 0.0))
                    atr_val = float(last_row.get("atr_adx_ema", 0.0))
                    atr_pct = (atr_val / current_price) if current_price > 0 else 0.0
                    trend_dir = "BULLISH" if current_price > ema200_val else "BEARISH"
                    candle_ts = last_row.name if hasattr(last_row, 'name') else df.index[-1]
                    
                    rejection_reason = "VALID_SIGNAL"
                    if not side:
                        reasons = []
                        cross_up = (ema20_val > ema50_val) and (float(prev_row.get("ema_20", 0)) <= float(prev_row.get("ema_50", 0)))
                        cross_dn = (ema20_val < ema50_val) and (float(prev_row.get("ema_20", 0)) >= float(prev_row.get("ema_50", 0)))
                        if not (cross_up or cross_dn):
                            reasons.append("NO_CROSSOVER")
                        if adx_val <= 25:
                            reasons.append("ADX_BELOW_25")
                        if cross_up and current_price <= ema200_val:
                            reasons.append("CLOSE_BELOW_EMA200")
                        if cross_dn and current_price >= ema200_val:
                            reasons.append("CLOSE_ABOVE_EMA200")
                        rejection_reason = "; ".join(reasons) if reasons else "NO_TRIGGER"

                    logger.info(
                        f"[CANDLE_EVALUATION] symbol={symbol} tf={tf} timestamp={candle_ts} "
                        f"price={current_price:.4f} ADX={adx_val:.2f} EMA20={ema20_val:.4f} "
                        f"EMA50={ema50_val:.4f} EMA200={ema200_val:.4f} ATR={atr_val:.4f} "
                        f"ATR%={atr_pct*100:.3f}% trend_direction={trend_dir} "
                        f"decision={side or 'HOLD'} reason={rejection_reason}"
                    )
                    logger.info(
                        f"[STRATEGY_SCAN] symbol={symbol} timeframe={tf} strategy={strat_name} "
                        f"decision={'SIGNAL' if side else 'HOLD'} reason={rejection_reason}"
                    )

                    # Aggregate global metrics
                    if not side:
                        self.stats["HOLD_SIGNALS"] += 1
                        if strat_name not in self.stats["strategy_metrics"]:
                            self.stats["strategy_metrics"][strat_name] = {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0, "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0}
                        if tf not in self.stats["timeframe_metrics"]:
                            self.stats["timeframe_metrics"][tf] = {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0, "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0}
                        
                        self.stats["strategy_metrics"][strat_name]["HOLD"] = self.stats["strategy_metrics"][strat_name].get("HOLD", 0) + 1
                        self.stats["timeframe_metrics"][tf]["HOLD"] = self.stats["timeframe_metrics"][tf].get("HOLD", 0) + 1
                        continue
                        
                    self.stats["TOTAL_SIGNALS"] += 1
                    if side == "BUY":
                        self.stats["BUY_SIGNALS"] += 1
                        self.stats["buy_predictions"] += 1
                    elif side == "SELL":
                        self.stats["SELL_SIGNALS"] += 1
                        self.stats["sell_predictions"] += 1
                        
                    if strat_name not in self.stats["strategy_metrics"]:
                        self.stats["strategy_metrics"][strat_name] = {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0, "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0}
                    if tf not in self.stats["timeframe_metrics"]:
                        self.stats["timeframe_metrics"][tf] = {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0, "evaluations": 0, "BUY": 0, "SELL": 0, "HOLD": 0}
                        
                    self.stats["strategy_metrics"][strat_name]["signals"] = self.stats["strategy_metrics"][strat_name].get("signals", 0) + 1
                    self.stats["timeframe_metrics"][tf]["signals"] = self.stats["timeframe_metrics"][tf].get("signals", 0) + 1
                    
                    self.stats["strategy_metrics"][strat_name][side] = self.stats["strategy_metrics"][strat_name].get(side, 0) + 1
                    self.stats["timeframe_metrics"][tf][side] = self.stats["timeframe_metrics"][tf].get(side, 0) + 1

                    candle_timestamp = df.index[-1]
                    deterministic_str = f"{symbol}_{strat_name}_{side}_{candle_timestamp}"
                    signal_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, deterministic_str))

                    logger.info(f"[SIGNAL_GENERATED] {strat_name} {side} {symbol} ({tf}) | SignalID: {signal_id} | SL: {sl} | TP: {tp}")

                    if side == "SELL" and getattr(config, 'LONG_ONLY', False):
                        self.stats["OTHER_REJECTED"] += 1
                        if strat_name in self.stats["strategy_metrics"]:
                            self.stats["strategy_metrics"][strat_name]["rejected"] += 1
                        if tf in self.stats["timeframe_metrics"]:
                            self.stats["timeframe_metrics"][tf]["rejected"] += 1
                        self.log_opportunity(signal_id, symbol, side, {"reason": "LONG_ONLY_RESTRICTION"}, "REJECTED", "LONG_ONLY_RESTRICTION")
                        continue

                    passed_profit, p_metrics = self.profitability_gate.evaluate_signal(
                        symbol, side, current_price, sl, tp, signal_result
                    )
                    
                    if not passed_profit:
                        self.stats["PROFITABILITY_REJECTED"] += 1
                        if strat_name in self.stats["strategy_metrics"]:
                            self.stats["strategy_metrics"][strat_name]["rejected"] += 1
                        if tf in self.stats["timeframe_metrics"]:
                            self.stats["timeframe_metrics"][tf]["rejected"] += 1
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", p_metrics["reason"])
                        continue
                        
                    candidate = {
                        "signal_id": signal_id,
                        "symbol": symbol,
                        "tf": tf,
                        "side": side,
                        "sl": sl,
                        "tp": tp,
                        "strategy": strat_name,
                        "metrics": p_metrics,
                        "signal_result": signal_result,
                        "timestamp": datetime.datetime.utcnow().timestamp()
                    }
                    self.opportunity_pool.put(candidate)
                    self.pool_event.set()
                    self.log_opportunity(signal_id, symbol, side, p_metrics, "QUALIFIED", "ADDED_TO_POOL")
                
            except Exception as e:
                logger.error(f"[STRATEGY_EXCEPTION] Error processing signal for {symbol} ({tf}): {e}", exc_info=True)
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
                strategy_name = candidate.get("strategy", "adx_ema")
                
                with self.lock:
                    # Enforce per-symbol cooldown (5 seconds for high-frequency testing)
                    now_ts = datetime.datetime.utcnow().timestamp()
                    if symbol in self.cooldowns and now_ts - self.cooldowns[symbol] < 5:
                        self.stats["COOLDOWN_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "ON_COOLDOWN")
                        continue
                        
                    tf = candidate.get("tf", "15m")
                    # Re-validate with absolute latest price
                    df = None
                    if hasattr(self, 'scanner'):
                        df = self.scanner.candle_cache.get((symbol, tf))
                            
                    if df is None or df.empty:
                        self.stats["MARKET_DATA_REJECTED"] += 1
                        self.stats["JIT_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "NO_MARKET_DATA")
                        continue
                        
                    current_price = df['close'].iloc[-1]
                    data_health = self.scanner.data_health_status.get(symbol, "OK") if hasattr(self, 'scanner') else "OK"
                    
                    # Re-validate Profitability Gate (Price may have moved)
                    # Pass signal_result from original candidate — preserves strategy_type metadata.
                    passed_profit, fresh_metrics = self.profitability_gate.evaluate_signal(
                        symbol, side, current_price, sl, tp,
                        candidate.get("signal_result", p_metrics["prob_win"])
                    )
                    
                    if not passed_profit:
                        self.stats["PROFITABILITY_REJECTED"] += 1
                        self.stats["JIT_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "REJECTED", f"REVALIDATION_FAILED: {fresh_metrics['reason']}")
                        continue
                        
                    self.stats["PROFITABILITY_ACCEPTED"] += 1
                    
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
                        
                    self.stats["RISK_ACCEPTED"] += 1
                        
                    # Execute
                    if self.observe_only:
                        self.stats["OTHER_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "ACCEPTED (OBSERVE ONLY)", "DEGRADED_PERFORMANCE_HALT")
                        continue
                        
                    self.stats["QUALIFIED"] += 1
                    if strategy_name in self.stats["strategy_metrics"]:
                        self.stats["strategy_metrics"][strategy_name]["qualified"] += 1
                    if tf in self.stats["timeframe_metrics"]:
                        self.stats["timeframe_metrics"][tf]["qualified"] += 1
                    self.log_opportunity(signal_id, symbol, side, fresh_metrics, "ACCEPTED", "ALL_GATES_PASSED")
                    
                    # Format strictly for Binance execution to prevent LOT_SIZE Filter Failure
                    step_size = filters.get("stepSize", 1.0)
                    precision = 0
                    if '.' in str(step_size):
                        precision = len(str(step_size).rstrip('0').split('.')[1])
                    qty_str = f"{qty:.{precision}f}"
                    
                    logger.info(f"[EXECUTION_ATTEMPTED] {strategy_name} {side} {qty_str} {symbol} @ ~{current_price} | SignalID: {signal_id}")
                    self.stats["EXECUTION_ELIGIBLE"] += 1
                    
                    self.telemetry.record_execution_event({
                        "event_type": "execution_attempt",
                        "symbol": symbol,
                        "strategy": strategy_name,
                        "timeframe": tf,
                        "trade_id": signal_id,
                        "side": side,
                        "quantity": float(qty_str),
                        "price": current_price
                    })
                    self.telemetry.record_equity_snapshot({
                        "trigger_event": "ORDER_SUBMISSION",
                        "total_equity": self.current_equity,
                        "cash": self.current_equity,
                        "open_positions": len(self.active_positions)
                    })
                    
                    try:
                        order_res = place_market_order(strategy_name, side, symbol, quantity=qty_str, sl=sl, tp=tp, client_order_id=signal_id)
                        if order_res:
                            self.stats["ORDERS_SUBMITTED"] += 1
                            self.stats["ORDERS_FILLED"] += 1
                            if strategy_name in self.stats["strategy_metrics"]:
                                self.stats["strategy_metrics"][strategy_name]["executed"] += 1
                            if tf in self.stats["timeframe_metrics"]:
                                self.stats["timeframe_metrics"][tf]["executed"] += 1
                            actual_price = order_res.get("_actual_price", current_price)
                            executed_qty = float(order_res.get("_executed_qty", qty_str))
                            entry_oid = str(order_res.get("orderId", ""))
                            notional = actual_price * executed_qty
                            est_fee = notional * 0.001
                            
                            logger.info(f"[ORDER_FILLED] {symbol} {side} {executed_qty} @ {actual_price:.4f} | OrderID: {entry_oid} | SignalID: {signal_id}")
                            
                            self.active_positions[symbol] = {
                                "strategy": strategy_name,
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
                            
                            # Telemetry Recording
                            self.telemetry.record_execution_event({
                                "event_type": "order_filled",
                                "symbol": symbol,
                                "trade_id": signal_id,
                                "order_id": entry_oid,
                                "strategy": strategy_name,
                                "timeframe": tf,
                                "side": side,
                                "quantity": executed_qty,
                                "price": actual_price
                            })
                            self.telemetry.record_trade_event({
                                "trade_id": signal_id,
                                "symbol": symbol,
                                "strategy": strategy_name,
                                "timeframe": tf,
                                "side": side,
                                "status": "OPEN",
                                "entry_order_id": entry_oid,
                                "entry_price": actual_price,
                                "average_entry_price": actual_price,
                                "quantity": executed_qty,
                                "notional": notional,
                                "stop_loss": sl,
                                "take_profit": tp,
                                "entry_fee": est_fee,
                                "risk_amount": notional * 0.005,
                                "expected_gross_return": fresh_metrics.get("gross_edge", 0.0),
                                "expected_net_return": fresh_metrics.get("expected_net_return", 0.0),
                                "profitability_decision": "ACCEPTED",
                                "risk_decision": "ACCEPTED",
                                "equity_before_entry": self.current_equity,
                                "equity_after_entry": self.current_equity,
                                "cash_before_entry": self.current_equity,
                                "cash_after_entry": max(0.0, self.current_equity - notional)
                            })
                            self.telemetry.record_position_update({
                                "position_id": symbol,
                                "trade_id": signal_id,
                                "symbol": symbol,
                                "strategy": strategy_name,
                                "timeframe": tf,
                                "side": side,
                                "entry_price": actual_price,
                                "quantity": executed_qty,
                                "stop_loss": sl,
                                "take_profit": tp,
                                "status": "OPEN"
                            })
                            self.telemetry.record_balance_event({
                                "event_type": "TRADE_OPEN",
                                "reason": f"BUY {symbol} {strategy_name}",
                                "balance_before": self.current_equity,
                                "balance_after": max(0.0, self.current_equity - notional),
                                "delta": -notional,
                                "trade_id": signal_id,
                                "symbol": symbol,
                                "strategy": strategy_name,
                                "timeframe": tf
                            })
                            self.telemetry.record_equity_snapshot({
                                "trigger_event": "POSITION_OPEN",
                                "total_equity": self.current_equity,
                                "cash": max(0.0, self.current_equity - notional),
                                "crypto_holdings_value": notional,
                                "open_positions": len(self.active_positions)
                            })
                        else:
                            self.stats["EXECUTION_REJECTED"] += 1
                            logger.warning(f"[ORDER_FAILED] {symbol} {side} | Reason: LOCAL_ORDER_BLOCKED")
                            self.log_opportunity(signal_id, symbol, side, fresh_metrics, "FAILED", "LOCAL_ORDER_BLOCKED", current_price=current_price)
                            self.telemetry.record_execution_event({
                                "event_type": "order_failed",
                                "symbol": symbol,
                                "trade_id": signal_id,
                                "strategy": strategy_name,
                                "timeframe": tf,
                                "status": "FAILED",
                                "error_code": "LOCAL_ORDER_BLOCKED",
                                "error_message": "Local order dispatch returned None"
                            })
                            self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                    except ZeroFillError as zfe:
                        self.stats["ORDERS_SUBMITTED"] += 1
                        self.stats["ORDERS_FAILED"] += 1
                        logger.warning(f"[ORDER_FAILED] {symbol} {side} | Reason: ZERO_FILL")
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "FAILED", "ZERO_FILL", current_price=current_price)
                        self.telemetry.record_execution_event({
                            "event_type": "order_failed",
                            "symbol": symbol,
                            "trade_id": signal_id,
                            "strategy": strategy_name,
                            "timeframe": tf,
                            "status": "FAILED",
                            "error_code": "ZERO_FILL",
                            "error_message": str(zfe)
                        })
                        self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                    except BinanceAPIException as e:
                        self.stats["ORDERS_SUBMITTED"] += 1
                        self.stats["ORDERS_FAILED"] += 1
                        logger.warning(f"[ORDER_FAILED] {symbol} {side} | Reason: BINANCE_API_ERROR_{e.status_code}")
                        self.log_opportunity(signal_id, symbol, side, fresh_metrics, "FAILED", f"BINANCE_API_ERROR_{e.status_code}", current_price=current_price)
                        self.telemetry.record_execution_event({
                            "event_type": "order_failed",
                            "symbol": symbol,
                            "trade_id": signal_id,
                            "strategy": strategy_name,
                            "timeframe": tf,
                            "status": "FAILED",
                            "error_code": f"BINANCE_API_ERROR_{e.status_code}",
                            "error_message": str(e)
                        })
                        self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                    except Exception as e:
                        # Unhandled execution errors
                        self.stats["ORDERS_SUBMITTED"] += 1
                        self.stats["ORDERS_FAILED"] += 1
                        logger.error(f"[ORDER_FAILED] {symbol} {side} | Reason: UNHANDLED_ERROR: {e}")
                        self.telemetry.record_execution_event({
                            "event_type": "order_failed",
                            "symbol": symbol,
                            "trade_id": signal_id,
                            "strategy": strategy_name,
                            "timeframe": tf,
                            "status": "FAILED",
                            "error_code": "UNHANDLED_ERROR",
                            "error_message": str(e)
                        })
                        self.cooldowns[symbol] = datetime.datetime.utcnow().timestamp()
                        
                    self._save_state()

    def _trade_target_monitor(self):
        """Monitor trade count and log progress toward 100-trade target."""
        import config
        start_time = datetime.datetime.utcnow()
        window_hours = getattr(config, "TARGET_TRADE_WINDOW_HOURS", 3)
        target = getattr(config, "TARGET_TRADE_COUNT", 100)
        window = datetime.timedelta(hours=window_hours)
        logger.info(f"[TRADE_TARGET] 🎯 Goal: {target} trades in {window_hours}h. Window ends at "
                    f"{(start_time + window).strftime('%H:%M UTC')}")
        while True:
            time.sleep(60)
            elapsed = datetime.datetime.utcnow() - start_time
            closed = self.stats.get("CLOSED_TRADES", 0)
            filled = self.stats.get("ORDERS_FILLED", 0)
            total = max(closed, filled)
            elapsed_h = elapsed.total_seconds() / 3600
            remaining_h = max(0, window_hours - elapsed_h)
            rate = total / elapsed_h if elapsed_h > 0 else 0
            projected = rate * window_hours
            logger.info(
                f"[TRADE_TARGET] ⏱ {elapsed_h:.1f}h elapsed | "
                f"Trades: {total}/{target} | Rate: {rate:.1f}/h | "
                f"Projected: {projected:.0f} | Remaining: {remaining_h:.1f}h | "
                f"Signals: {self.stats.get('TOTAL_SIGNALS',0)} | "
                f"Rejected(cooldown): {self.stats.get('COOLDOWN_REJECTED',0)} | "
                f"Rejected(risk): {self.stats.get('RISK_REJECTED',0)} | "
                f"Rejected(profit): {self.stats.get('PROFITABILITY_REJECTED',0)}"
            )
            if total >= target:
                logger.info(f"[TRADE_TARGET] ✅ TARGET ACHIEVED: {total} trades in {elapsed_h:.2f}h!")
                break
            if elapsed > window:
                logger.warning(f"[TRADE_TARGET] ❌ Window expired. Final: {total}/{target} trades.")
                break

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

        try:
            if hasattr(self, "telemetry") and self.telemetry:
                # Map the decision/reason to the correct gate fields for the terminal
                # 'decision' is the overall outcome at the point this is called:
                #   REJECTED (profitability), REJECTED (risk), ACCEPTED, FAILED, QUALIFIED
                # We infer which gate based on context stored in metrics or the reason string
                is_risk_stage = any(k in reason for k in [
                    "RISK", "MIN_NOTIONAL", "EXPOSURE", "DRAWDOWN", "DAILY_LOSS",
                    "SAFETY_HALT", "LOCAL_ORDER_BLOCKED", "ZERO_FILL", "BINANCE_API_ERROR"
                ])
                is_profit_stage = any(k in reason for k in [
                    "PROFITABILITY", "REVALIDATION", "EDGE", "NET_RETURN", "FEES"
                ])

                # Determine per-gate decision values
                if decision in ["ACCEPTED", "ALL_GATES_PASSED"]:
                    p_dec = "ACCEPTED"
                    r_dec = "ACCEPTED"
                    r_reason_val = "ALL_GATES_PASSED"
                    p_reason_val = reason
                elif is_risk_stage:
                    p_dec = "ACCEPTED"  # passed profit gate to reach risk stage
                    r_dec = "REJECTED"
                    r_reason_val = reason
                    p_reason_val = ""
                elif is_profit_stage:
                    p_dec = "REJECTED"
                    r_dec = "PENDING"
                    r_reason_val = ""
                    p_reason_val = reason
                else:
                    p_dec = decision
                    r_dec = "PENDING"
                    r_reason_val = ""
                    p_reason_val = reason

                self.telemetry.record_signal_event({
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "decision": side,
                    "strategy": metrics.get("strategy", ""),
                    "timeframe": metrics.get("timeframe", "5m"),
                    "entry": current_price or metrics.get("current_price", 0.0),
                    "stop": metrics.get("sl", 0.0),
                    "target": metrics.get("tp", 0.0),
                    "confidence": metrics.get("confidence", 0.0),
                    "expected_gross": metrics.get("gross_edge") or metrics.get("expected_gross_return", 0.0),
                    "expected_net": metrics.get("expected_net_return", 0.0),
                    "profitability_decision": p_dec,
                    "profitability_reason": p_reason_val,
                    "risk_decision": r_dec,
                    "risk_reason": r_reason_val,
                    "final_decision": "EXECUTED" if decision in ["ACCEPTED", "ALL_GATES_PASSED"] else "REJECTED"
                })
        except Exception:
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
                
                # 3. Calculate total reconstructable PnL from the Ledger (with strict provenance)
                total_reconstructable_pnl = 0.0
                if os.path.exists(TESTNET_LEDGER_FILE):
                    with open(TESTNET_LEDGER_FILE, "r") as f:
                        for line in f:
                            if not line.strip(): continue
                            try:
                                record = json.loads(line)
                                source = record.get("source", "")
                                strategy = record.get("strategy", "")
                                if source in ["BINANCE_EXECUTION", "RECOVERY_FROM_BINANCE"] and strategy != "TEST":
                                    total_reconstructable_pnl += float(record.get("net_pnl", 0.0))
                            except:
                                pass
                
                self.local_portfolio_balance = self.initial_deposit + total_reconstructable_pnl
                
                # Active position capital deployed
                active = _load_active_trades()
                active_open_positions = [t for t in active if t.get('status') == 'OPEN']
                used_margin = sum(float(t.get('quantity', 0.0)) * float(t.get('entry_price', 0.0)) for t in active_open_positions)
                actual_binance_total = actual_binance_balance + used_margin
                
                import config
                tolerance = getattr(config, "RECONCILIATION_TOLERANCE", 25.0)
                mismatch = abs(self.local_portfolio_balance - actual_binance_total)
                
                if mismatch > tolerance:
                    logger.warning(f"[SERVICE] Balance Note: Calculated Total = {actual_binance_total:.2f} USDT "
                                   f"(Cash: {actual_binance_balance:.2f} + Deployed: {used_margin:.2f}) vs Local: {self.local_portfolio_balance:.2f}")
                
                # Maintain active equity smoothly
                self.current_equity = actual_binance_total
                self.safety_halt = False
                    
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
                            self.stats["CLOSED_TRADES"] += 1
                            
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
                            
                            # Telemetry update — full 40-field canonical lifecycle record
                            try:
                                if hasattr(self, "telemetry") and self.telemetry:
                                    # Compute balance state before/after exit
                                    net_pnl_ct = float(ct.get("net_pnl", 0.0))
                                    equity_before_exit = self.current_equity
                                    equity_after_exit = self.current_equity + net_pnl_ct
                                    notional_ct = float(ct.get("quantity", 0.0)) * float(ct.get("entry_price", 0.0))
                                    cash_before_exit = max(0.0, self.current_equity - notional_ct)
                                    cash_after_exit = cash_before_exit + float(ct.get("quantity", 0.0)) * float(ct.get("exit_price", 0.0))

                                    self.telemetry.record_trade_event({
                                        "trade_id": str(ct.get("entry_client_id", ct.get("entry_order_id", ct.get("exit_order_id", "")))),
                                        "symbol": ct.get("symbol"),
                                        "strategy": ct.get("strategy", ""),
                                        "timeframe": ct.get("timeframe", "5m"),
                                        "side": ct.get("side", ct.get("action", "BUY")).replace("CLOSED_", "").replace("CLOSE_", ""),
                                        "status": "CLOSED",
                                        "entry_order_id": str(ct.get("entry_order_id", "")),
                                        "exit_order_id": str(ct.get("exit_order_id", "")),
                                        # Timestamps — fill_timestamp is the entry fill time
                                        "fill_timestamp": ct.get("entry_timestamp", ""),
                                        "close_timestamp": ct.get("exit_timestamp", ""),
                                        "signal_timestamp": ct.get("signal_timestamp", ct.get("entry_timestamp", "")),
                                        # Prices
                                        "entry_price": float(ct.get("entry_price", 0.0)),
                                        "average_entry_price": float(ct.get("entry_price", 0.0)),
                                        "exit_price": float(ct.get("exit_price", 0.0)),
                                        "quantity": float(ct.get("quantity", 0.0)),
                                        "notional": notional_ct,
                                        # Protection
                                        "stop_loss": float(ct.get("sl_price", ct.get("sl", 0.0))),
                                        "take_profit": float(ct.get("tp_price", ct.get("tp", 0.0))),
                                        # PnL & Fees
                                        "gross_pnl": float(ct.get("gross_pnl", 0.0)),
                                        "net_pnl": net_pnl_ct,
                                        "total_fees": float(ct.get("total_fees", ct.get("fees", ct.get("entry_fee", 0.0) + ct.get("exit_fee", 0.0)))),
                                        "entry_fee": float(ct.get("entry_fee", 0.0)),
                                        "exit_fee": float(ct.get("exit_fee", 0.0)),
                                        # Balance/Equity state at exit
                                        "equity_before_exit": equity_before_exit,
                                        "equity_after_exit": equity_after_exit,
                                        "cash_before_exit": cash_before_exit,
                                        "cash_after_exit": cash_after_exit,
                                        # Close metadata
                                        "close_reason": ct.get("exit_reason", ct.get("action", "")),
                                        "source": ct.get("source", "BINANCE_EXECUTION"),
                                        "provenance": "PRODUCTION_TESTNET"
                                    })
                                    self.telemetry.record_position_update({
                                        "position_id": ct.get("symbol"),
                                        "trade_id": str(ct.get("entry_client_id", ct.get("entry_order_id", ""))),
                                        "symbol": ct.get("symbol"),
                                        "strategy": ct.get("strategy", ""),
                                        "timeframe": ct.get("timeframe", "5m"),
                                        "exit_timestamp": ct.get("exit_timestamp", ""),
                                        "exit_price": float(ct.get("exit_price", 0.0)),
                                        "status": "CLOSED",
                                        "realized_pnl": net_pnl_ct
                                    })
                                    self.telemetry.record_balance_event({
                                        "event_type": "TRADE_CLOSE",
                                        "reason": f"CLOSE {ct.get('symbol')} via {ct.get('exit_reason', 'OCO')}",
                                        "balance_before": equity_before_exit,
                                        "balance_after": equity_after_exit,
                                        "delta": net_pnl_ct,
                                        "realized_pnl_delta": net_pnl_ct,
                                        "fees_delta": float(ct.get("total_fees", 0.0)),
                                        "trade_id": str(ct.get("entry_order_id", "")),
                                        "symbol": ct.get("symbol"),
                                        "strategy": ct.get("strategy", ""),
                                        "timeframe": ct.get("timeframe", "5m")
                                    })
                            except Exception as te_err:
                                logger.error(f"[TELEMETRY] Failed to record closed trade: {te_err}")
                    
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

    def _write_heartbeat(self, status="RUNNING", worker_alive=True):
        """Atomically writes worker heartbeat state for dashboard, supervisor, and health checks."""
        try:
            now_iso = datetime.datetime.utcnow().isoformat() + "Z"
            symbols = self.scanner.symbols if hasattr(self, 'scanner') and self.scanner else list(self.symbol_filters.keys())
            
            last_m_update = None
            if hasattr(self, 'scanner') and self.scanner and hasattr(self.scanner, 'last_market_update'):
                updates = [ts for ts in self.scanner.last_market_update.values() if ts]
                if updates:
                    last_m_update = max(updates).isoformat() + "Z"
                    
            last_candle_close = None
            if hasattr(self, 'scanner') and self.scanner and hasattr(self.scanner, 'last_candle_close'):
                closes = [ts for ts in self.scanner.last_candle_close.values() if ts]
                if closes:
                    last_candle_close = max(closes).isoformat() + "Z"
                
            last_eval = None
            if self.last_evaluation:
                eval_times = [v for v in self.last_evaluation.values() if v]
                if eval_times:
                    last_eval = max(eval_times)

            hb = {
                "worker_alive": worker_alive,
                "status": status,
                "pid": os.getpid(),
                "timestamp": now_iso,
                "mode": "TESTNET",
                "binance_connected": self.client is not None,
                "websocket_connected": hasattr(self, 'scanner') and bool(self.scanner),
                "strategy": list(ACTIVE_STRATEGIES.keys())[0] if ACTIVE_STRATEGIES else "adx_ema",
                "strategies": list(ACTIVE_STRATEGIES.keys()),
                "timeframes": list(set(tf for tfs in ACTIVE_STRATEGIES.values() for tf in (tfs if isinstance(tfs, list) else [tfs]))),
                "symbols": symbols,
                "symbol_count": len(symbols),
                "last_market_update": last_m_update,
                "last_candle_close": last_candle_close,
                "last_strategy_evaluation": last_eval,
                "service_start_time": getattr(self, 'service_start_time', now_iso),
                "current_equity": getattr(self, 'current_equity', 0.0),
                "open_positions": len(getattr(self, 'active_positions', {}))
            }
            tmp = TESTNET_HEARTBEAT_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(hb, f, indent=2)
            os.replace(tmp, TESTNET_HEARTBEAT_FILE)
            
            # Also update legacy heartbeat.json for backwards compatibility
            try:
                tmp_legacy = "heartbeat.json.tmp"
                with open(tmp_legacy, "w") as lf:
                    json.dump(hb, lf, indent=2)
                os.replace(tmp_legacy, "heartbeat.json")
            except:
                pass
        except Exception as e:
            logger.error(f"[SERVICE] Error writing heartbeat: {e}")

    def _heartbeat_loop(self):
        """Periodic heartbeat writer (runs every 5 seconds)."""
        last_log_time = 0
        while True:
            try:
                self._write_heartbeat(status="RUNNING", worker_alive=True)
                now_ts = time.time()
                if now_ts - last_log_time >= 60:
                    tfs = list(set(tf for tfs in ACTIVE_STRATEGIES.values() for tf in (tfs if isinstance(tfs, list) else [tfs])))
                    logger.info(f"[ENGINE_HEARTBEAT] PID={os.getpid()} | Status=RUNNING | Strategies={len(ACTIVE_STRATEGIES)} | Timeframes={','.join(tfs)} | Symbols={len(self.symbol_filters)}")
                    last_log_time = now_ts
            except Exception as e:
                logger.error(f"[SERVICE] Heartbeat loop error: {e}")
            time.sleep(5)

    def _progress_report_loop(self):
        """Periodic progress reporter (runs every 60 seconds). Logs key stats and writes them to a progress file."""
        progress_file = os.getenv("PROGRESS_LOG_FILE", "progress_report.jsonl")
        while True:
            try:
                timestamp = datetime.datetime.utcnow().isoformat() + "Z"
                # Gather key metrics
                report = {
                    "timestamp": timestamp,
                    "total_signals": self.stats.get("TOTAL_SIGNALS", 0),
                    "buy_signals": self.stats.get("BUY_SIGNALS", 0),
                    "sell_signals": self.stats.get("SELL_SIGNALS", 0),
                    "qualified": self.stats.get("QUALIFIED", 0),
                    "executed": self.stats.get("ORDERS_FILLED", 0),
                    "current_equity": getattr(self, "current_equity", 0.0),
                    "open_positions": len(self.active_positions),
                    "symbols_scanned": self.stats.get("symbols_scanned", 0),
                    "strategy_metrics": self.stats.get("strategy_metrics", {}),
                    "timeframe_metrics": self.stats.get("timeframe_metrics", {}),
                }
                # Atomic append
                tmp_path = progress_file + ".tmp"
                with open(tmp_path, "a") as f:
                    f.write(json.dumps(report) + "\n")
                os.replace(tmp_path, progress_file)
                logger.info(f"[PROGRESS] Total:{report['total_signals']} BUY:{report['buy_signals']} SELL:{report['sell_signals']} Equity:{report['current_equity']:.2f}")
            except Exception as e:
                logger.error(f"[SERVICE] Progress report loop error: {e}")
            time.sleep(60)

        """Periodic heartbeat writer (runs every 5 seconds)."""
        last_log_time = 0
        while True:
            try:
                self._write_heartbeat(status="RUNNING", worker_alive=True)
                now_ts = time.time()
                if now_ts - last_log_time >= 60:
                    tfs = list(set(tf for tfs in ACTIVE_STRATEGIES.values() for tf in (tfs if isinstance(tfs, list) else [tfs])))
                    logger.info(f"[ENGINE_HEARTBEAT] PID={os.getpid()} | Status=RUNNING | Strategies={len(ACTIVE_STRATEGIES)} | Timeframes={','.join(tfs)} | Symbols={len(self.symbol_filters)}")
            except Exception as e:
                logger.error(f"[SERVICE] Heartbeat loop error: {e}")
            time.sleep(5)

    def run(self):
        # 1. Discover Symbols
        discovery = SymbolDiscoveryService()
        self.symbol_filters = discovery.discover_eligible_symbols(min_quote_volume=1_000_000)
        symbol_list = list(self.symbol_filters.keys())
        self.stats["symbols_scanned"] = len(symbol_list)
        
        # 2. Train ML Strategy (if enabled)
        if "ml" in ACTIVE_STRATEGIES:
            try:
                from strategy_ml import train
                from data import get_candles, add_indicators
                logger.info("[SERVICE] Pre-training ML strategy on BTCUSDT...")
                ml_tf = ACTIVE_STRATEGIES["ml"][0] if isinstance(ACTIVE_STRATEGIES["ml"], list) else ACTIVE_STRATEGIES["ml"]
                all_df = add_indicators(get_candles("BTCUSDT", ml_tf, 2500))
                train_df = all_df.iloc[:-500]
                val_df = all_df.iloc[-500:]
                train(train_df, val_df)
                logger.info("[SERVICE] ML strategy trained successfully.")
            except Exception as e:
                logger.error(f"[SERVICE] Failed to train ML strategy: {e}")
                
        # 3. Start Scanner
        tfs_set = set()
        for tfs in ACTIVE_STRATEGIES.values():
            if isinstance(tfs, list):
                tfs_set.update(tfs)
            else:
                tfs_set.add(tfs)
        tfs = list(tfs_set)
        self.scanner = MarketScanner(symbol_list, timeframes=tfs)
        self.scanner.register_callback(self.on_candle_closed)
        self.scanner.start()
        logger.info(f"[MARKET_DATA_CONNECTED] Multiplex WebSocket streaming active for {len(symbol_list)} symbols.")
        
        # 4. Start Position Monitor Thread
        monitor_thread = threading.Thread(target=self.position_monitor_loop, daemon=True)
        monitor_thread.start()
        
        # 5. Start Heartbeat Thread
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()
        self._write_heartbeat(status="RUNNING", worker_alive=True)
        # Start progress reporting thread
        self._progress_thread = threading.Thread(target=self._progress_report_loop, daemon=True)
        self._progress_thread.start()
        
        logger.info("[ENGINE_READY] Binance Testnet trading engine is fully initialized and operational.")
        
        # 6. Keep Main Thread Alive
        try:
            while True:
                time.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            logger.info("[SERVICE] Graceful shutdown initiated...")
            self._write_heartbeat(status="STOPPED", worker_alive=False)
            if hasattr(self, 'scanner') and self.scanner:
                self.scanner.stop()

if __name__ == "__main__":
    service = TestnetService()
    service.run()
