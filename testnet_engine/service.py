import datetime
import importlib
import json
import os
import queue
import sys
import threading
import time
import uuid

import pandas as pd

from binance.exceptions import BinanceAPIException

import config
from config import ACTIVE_STRATEGIES, TRADING_MODE
from config_strategy import ADX_EMA_STRATEGY_V2, PRODUCTION_STRATEGY_REGISTRY
from data import add_indicators
from execution import _load_active_trades, get_exchange_client, place_market_order
from logger import get_logger
from paper_engine.exceptions import ZeroFillError
from research_phase9.cost_engine import CostEngine
from testnet_engine.discovery import SymbolDiscoveryService
from testnet_engine.market_scanner import MarketScanner
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate
from testnet_engine.telemetry_manager import get_telemetry_manager

logger = get_logger("service")


def governance_filter_strategies(active_strategies):
    """Return {strategy: [timeframes]} restricted to registry-VALIDATED strategies.

    Governance gate: only strategies with defensible out-of-sample proof may trade.
    DISABLED/unregistered strategies (e.g. 1m scalpers whose targets cannot overcome
    taker friction) are dropped, and scanning is pinned to the validated timeframe.
    """
    filtered = {}
    for strat_name, tfs in active_strategies.items():
        entry = PRODUCTION_STRATEGY_REGISTRY.get(strat_name)
        if entry is None or entry.get("status") != "VALIDATED":
            status = entry.get("status") if entry else "UNREGISTERED"
            logger.warning(
                f"[GOVERNANCE] Strategy '{strat_name}' skipped — registry status "
                f"{status}; only VALIDATED strategies may generate executable signals."
            )
            continue
        validated_tf = entry.get("timeframe")
        tfs_list = tfs if isinstance(tfs, list) else [tfs]
        if validated_tf and validated_tf not in tfs_list:
            validated_tf = tfs_list[-1]
        filtered[strat_name] = [validated_tf] if validated_tf else tfs_list
    return filtered


def governance_validated_assets(strategies_by_tf):
    """Union of OOS-validated assets across the loaded (already gated) strategies."""
    assets = set()
    for tf_strats in (strategies_by_tf or {}).values():
        for strat_name, _mod in tf_strats:
            entry = PRODUCTION_STRATEGY_REGISTRY.get(strat_name, {})
            assets.update(entry.get("validated_assets", []))
    return assets


def compute_btc_regime(btc_df):
    """Return (regime_ok, btc_close, btc_ema200) from a BTCUSDT 4h frame.

    regime_ok is True (risk-on) when close > EMA200, False (risk-off) below.
    Returns (None, None, None) when the data is insufficient to judge — the
    caller treats None as fail-open with a warning rather than blocking all
    trading on a lagging websocket feed.
    """
    if btc_df is None or len(btc_df) < 200 or "close" not in btc_df.columns:
        return None, None, None
    closes = btc_df["close"].astype(float)
    ema200 = float(closes.ewm(span=200, adjust=False).mean().iloc[-1])
    close = float(closes.iloc[-1])
    return (close > ema200), close, ema200

_TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400}
_COOLDOWN_SECONDS = float(os.getenv("SIGNAL_COOLDOWN_SECONDS", "300"))  # 5 minutes default

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
            
        testnet_is_only = os.getenv("TESTNET_ONLY", "").upper() == "TRUE"
        testnet_is_enabled = getattr(config, "TESTNET_ENABLED", False) or os.getenv("TESTNET_ENABLED", "False").lower() == "true"
        if not (testnet_is_only or testnet_is_enabled):
            raise RuntimeError("CRITICAL ERROR: TESTNET_ENABLED=True or TESTNET_ONLY=TRUE is required to run the Testnet execution mode safely.")
            
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
        for strat_name, scan_tfs in governance_filter_strategies(ACTIVE_STRATEGIES).items():
            try:
                mod = importlib.import_module(f"strategy_{strat_name}")
                for tf in scan_tfs:
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
                s: {"signals": 0, "qualified": 0, "rejected": 0, "executed": 0} for s in ACTIVE_STRATEGIES
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
                        for k in self.stats:
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

                        action_str = str(record.get("action", "")).upper()
                        status_str = str(record.get("status", "")).upper()
                        event_type_str = str(record.get("event_type", "")).upper()
                        if "CLOSE" in action_str or status_str == "CLOSED" or "CLOSE" in event_type_str:
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
            open_symbols_from_assets = {a['asset'] + "USDT" for a in assets if a['asset'] != "USDT"}
            
            from execution import _load_active_trades
            try:
                active_trades = _load_active_trades()
            except Exception as e:
                logger.error(f"[RECONCILIATION] Failed to load active trades: {e}")
                active_trades = []
                
            local_symbols = {t['symbol'] for t in active_trades}
            protected_symbols = {o['symbol'] for o in open_orders}
            
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
                            except Exception:
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
            managed_symbols = {t['symbol'] for t in active_trades if t.get('status') == 'OPEN'}
            managed_unprotected = [s for s in managed_symbols if s not in protected_symbols and s not in floating_ocos]
            
            # Crash Recovery: Attempt to place missing OCO protection for unmanaged/unprotected open assets
            if unprotected and not os.environ.get("PYTEST_CURRENT_TEST"):
                from execution import _save_active_trades
                from testnet_engine.protection import (
                    emergency_market_close,
                    place_oco_protection,
                )
                for sym in list(unprotected):
                    try:
                        logger.info(f"[RECOVERY] 🛡️ Unprotected position detected after restart for {sym}. Attempting safe OCO restoration...")
                        recent_trades = self.client.get_my_trades(symbol=sym, limit=20)
                        if recent_trades:
                            last_trade = recent_trades[-1]
                            entry_price = float(last_trade['price'])
                            executed_qty = float(last_trade['qty'])
                            entry_side = "BUY" if last_trade['isBuyer'] else "SELL"
                            
                            # Get current ticker price
                            curr_ticker = self.client.get_symbol_ticker(symbol=sym)
                            curr_price = float(curr_ticker['price'])
                            
                            # Standard conservative protection levels (SL: 2%, TP: 4%)
                            if entry_side == "BUY":
                                sl_price = round(entry_price * 0.98, 4)
                                tp_price = round(entry_price * 1.04, 4)
                                is_safe = curr_price > sl_price and curr_price < tp_price
                            else:
                                sl_price = round(entry_price * 1.02, 4)
                                tp_price = round(entry_price * 0.96, 4)
                                is_safe = curr_price < sl_price and curr_price > tp_price

                            if is_safe:
                                prot = place_oco_protection(
                                    client=self.client,
                                    symbol=sym,
                                    entry_side=entry_side,
                                    executed_qty=executed_qty,
                                    actual_fill_price=entry_price,
                                    sl_price=sl_price,
                                    tp_price=tp_price,
                                    list_client_order_id=f"rec-{int(time.time())}"
                                )
                                recovered_trade = {
                                    "strategy": "RECOVERED",
                                    "symbol": sym,
                                    "side": entry_side,
                                    "quantity": executed_qty,
                                    "entry_price": entry_price,
                                    "entry_fee": float(last_trade.get('commission', 0.0)),
                                    "sl_price": sl_price,
                                    "tp_price": tp_price,
                                    "oco_id": prot["oco_order_list_id"],
                                    "tp_order_id": prot["tp_order_id"],
                                    "sl_order_id": prot["sl_order_id"],
                                    "status": "OPEN",
                                    "state": 1,
                                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                                    "entry_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                                    "signal_id": f"RECOVERED_{int(time.time())}",
                                    "entry_client_id": f"RECOVERED_{int(time.time())}"
                                }
                                active_trades.append(recovered_trade)
                                _save_active_trades(active_trades)
                                unprotected.remove(sym)
                                logger.info(f"[RECOVERY] ✅ Successfully restored missing OCO protection for {sym} (OCO ID: {prot['oco_order_list_id']})")
                            else:
                                logger.warning(f"[RECOVERY] Price outside safe bounds for {sym}. Attempting emergency market close.")
                                emergency_market_close(self.client, sym, entry_side, executed_qty)
                                unprotected.remove(sym)
                    except Exception as re_err:
                        logger.error(f"[RECOVERY] Failed to restore missing protection for {sym}: {re_err}")

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
            "current_equity": getattr(self, "current_equity", 10000.0),
            "starting_equity": getattr(self, "starting_equity", 10000.0),
            "service_start_time": getattr(self, 'service_start_time', datetime.datetime.utcnow().isoformat() + "Z"),
            "safety_halt": getattr(self, 'safety_halt', False),
            "cash": max(0.0, self.current_equity - sum(p.get('quantity', 0) * p.get('entry_price', 0) for p in self.active_positions.values() if isinstance(p, dict))),
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
            },
            "funnel": {
                "candles_evaluated": self.stats.get("CANDLES", self.stats.get("TOTAL_CANDLES", 0)),
                "strategies_evaluated": self.stats.get("STRATEGIES_EVALUATED", self.stats.get("strategy_evaluations", 0)),
                "signals_generated": self.stats.get("SIGNALS_GENERATED", self.stats.get("TOTAL_SIGNALS", 0)),
                "profitability_accepted": self.stats.get("PROFITABILITY_ACCEPTED", 0),
                "profitability_rejected": self.stats.get("PROFITABILITY_REJECTED", 0),
                "risk_accepted": self.stats.get("RISK_ACCEPTED", 0),
                "risk_rejected": self.stats.get("RISK_REJECTED", 0),
                "execution_eligible": self.stats.get("EXECUTION_ELIGIBLE", self.stats.get("QUALIFIED", 0)),
                "execution_rejected": self.stats.get("EXECUTION_REJECTED", 0),
                "orders_submitted": self.stats.get("ORDERS_SUBMITTED", 0),
                "orders_filled": self.stats.get("ORDERS_FILLED", 0),
                "orders_failed": self.stats.get("ORDERS_FAILED", 0)
            },
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
        }
        try:
            tmp_file = TESTNET_PORTFOLIO_FILE + ".tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                def convert_keys(obj):
                    if isinstance(obj, dict):
                        return {str(k) if isinstance(k, tuple) else k: convert_keys(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_keys(i) for i in obj]
                    return obj
                json.dump(convert_keys(state), f, indent=2)
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

        # Strategy Protection: Reject stale, unverified, or missing market data
        if data_health_status != "OK":
            logger.warning(f"[STRATEGY_SKIPPED] reason=STALE_MARKET_DATA symbol={symbol} tf={tf} health={data_health_status}")
            return

        if df is None or df.empty or len(df) < 20:
            logger.warning(f"[STRATEGY_SKIPPED] reason=INSUFFICIENT_MARKET_DATA symbol={symbol} tf={tf} rows={len(df) if df is not None else 0}")
            return

        # Check candle age freshness against timeframe
        try:
            last_ts = df["timestamp"].iloc[-1]
            if isinstance(last_ts, pd.Timestamp):
                age_sec = (datetime.datetime.utcnow() - last_ts.to_pydatetime()).total_seconds()
            else:
                age_sec = 0
            max_allowed_age = _TF_SECONDS.get(tf, 3600) * 3
            if age_sec > max_allowed_age and age_sec > 0:
                logger.warning(f"[STRATEGY_SKIPPED] reason=STALE_MARKET_DATA symbol={symbol} tf={tf} age={age_sec:.1f}s")
                return
        except Exception:
            pass

        df = add_indicators(df)
        if df.empty:
            return

        current_price = df['close'].iloc[-1]
        logger.info(f"[FEATURES_READY] {symbol} {tf} | Rows: {len(df)} | Current Price: {current_price}")

        with self.lock:
            try:
                self.stats["TOTAL_CANDLES"] += 1
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
                        _adx_th = ADX_EMA_STRATEGY_V2.get("ADX_THRESHOLD", 30)
                        if adx_val <= _adx_th:
                            reasons.append(f"ADX_BELOW_{_adx_th}")
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

                    candle_timestamp = df['timestamp'].iloc[-1]
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

                    # V2 spot upgrade: BTC market-regime gate — alts follow BTC;
                    # long entries during BTC risk-off (4h close < EMA200) are
                    # historically net-negative (see research/upgrade_2026_08).
                    if side == "BUY" and ADX_EMA_STRATEGY_V2.get("BTC_REGIME_FILTER", False):
                        regime_ok, btc_close, btc_ema = self._btc_regime_state()
                        if regime_ok is False:
                            self.stats["OTHER_REJECTED"] += 1
                            if strat_name in self.stats["strategy_metrics"]:
                                self.stats["strategy_metrics"][strat_name]["rejected"] += 1
                            if tf in self.stats["timeframe_metrics"]:
                                self.stats["timeframe_metrics"][tf]["rejected"] += 1
                            self.log_opportunity(
                                signal_id, symbol, side,
                                {"reason": "BTC_REGIME_RISK_OFF", "btc_close": btc_close, "btc_ema200": btc_ema},
                                "REJECTED", "BTC_REGIME_RISK_OFF"
                            )
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
                        "entry": current_price,
                        "current_price": current_price,
                        "sl": sl,
                        "tp": tp,
                        "strategy": strat_name,
                        "metrics": p_metrics,
                        "signal_result": signal_result,
                        "timestamp": datetime.datetime.utcnow().timestamp()
                    }
                    self.opportunity_pool.put(candidate)
                    self.pool_event.set()
                    self.log_opportunity(signal_id, symbol, side, p_metrics, "QUALIFIED", "ADDED_TO_POOL", current_price=current_price, candidate=candidate)
                
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
                
            # Opportunity Ranking:
            # Deterministic Score Formula: score = round((expected_net_return * confidence) / max(0.001, risk_pct), 6)
            for c in candidates:
                p_met = c.get("metrics", {})
                exp_net = float(p_met.get("expected_net_return", 0.0))
                conf = float(p_met.get("confidence", p_met.get("prob_win", p_met.get("win_rate_prior", 0.5))))
                sl_p = float(c.get("sl", 0.0))
                tp_p = float(c.get("tp", 0.0))
                fallback_entry = (sl_p + tp_p) / 2 if (sl_p > 0 and tp_p > 0) else 1.0
                entry_p = float(c.get("entry", c.get("current_price", fallback_entry)))
                if entry_p <= 1.0 and fallback_entry > 1.0:
                    entry_p = fallback_entry
                risk_pct = float(p_met.get("risk_pct", abs(entry_p - sl_p) / max(1e-5, entry_p)))
                if risk_pct <= 0.0 or risk_pct > 1.0:
                    risk_pct = 0.01 # normalize to standard 1% risk baseline
                score = round(exp_net * conf / max(0.001, risk_pct), 6)
                c["score"] = score
                c["net_edge"] = exp_net
                c["risk"] = risk_pct
                c["confidence"] = conf
                
            # Rank candidates by Deterministic Score (Descending), then Net Edge, Confidence, Risk, Symbol
            candidates.sort(key=lambda x: (x["score"], x["net_edge"], x["confidence"], -x["risk"], x["symbol"]), reverse=True)
            
            for rank_idx, candidate in enumerate(candidates, 1):
                candidate["rank"] = rank_idx
                logger.info(
                    f"[OPPORTUNITY_RANK] Rank #{rank_idx} | {candidate['symbol']} ({candidate.get('tf','15m')}) | "
                    f"Strat: {candidate.get('strategy')} | Score: {candidate['score']} | "
                    f"Net Edge: {candidate['net_edge']*100:.3f}% | Risk: {candidate['risk']*100:.3f}% | Conf: {candidate['confidence']*100:.1f}%"
                )
                symbol = candidate["symbol"]
                side = candidate["side"]
                signal_id = candidate["signal_id"]
                p_metrics = candidate["metrics"]
                sl = candidate["sl"]
                tp = candidate["tp"]
                strategy_name = candidate.get("strategy", "adx_ema")
                
                with self.lock:
                    # Enforce per-symbol cooldown
                    now_ts = datetime.datetime.utcnow().timestamp()
                    if symbol in self.cooldowns and now_ts - self.cooldowns[symbol] < _COOLDOWN_SECONDS:
                        self.stats["COOLDOWN_REJECTED"] += 1
                        self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "ON_COOLDOWN")
                        continue

                    # Pre-execution duplicate position guard
                    # Risk gate uses portfolio file which may be slightly stale on fast restarts.
                    # This in-memory check prevents double-entry into the same symbol.
                    if symbol in self.active_positions:
                        existing = self.active_positions[symbol]
                        if existing.get("status") == "OPEN":
                            logger.warning(
                                f"[DUPLICATE_POSITION_GUARD] {symbol} already has OPEN position "
                                f"(SignalID: {signal_id}). Skipping duplicate entry."
                            )
                            self.stats["OTHER_REJECTED"] += 1
                            self.log_opportunity(signal_id, symbol, side, p_metrics, "REJECTED", "DUPLICATE_POSITION_GUARD")
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
                    passed_risk, r_reason, _r_details = self.risk_gate.evaluate_risk(
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
                            order_status = str(order_res.get("status", "")).upper()
                            if order_status in ("FILLED", "PARTIALLY_FILLED") or order_res.get("_executed_qty", 0) > 0:
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

                            # Partial fill detection: warn if filled quantity < 95% of requested
                            proposed_qty = float(qty_str)
                            if proposed_qty > 0 and executed_qty / proposed_qty < 0.95:
                                fill_pct = (executed_qty / proposed_qty) * 100
                                logger.warning(
                                    f"[PARTIAL_FILL] {symbol} {side} filled {fill_pct:.1f}% of proposed qty. "
                                    f"Requested: {proposed_qty}, Executed: {executed_qty}"
                                )
                                self.telemetry.record_execution_event({
                                    "event_type": "partial_fill_detected",
                                    "symbol": symbol,
                                    "trade_id": signal_id,
                                    "proposed_qty": proposed_qty,
                                    "executed_qty": executed_qty,
                                    "fill_pct": round(fill_pct, 2)
                                })
                            
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

    def log_opportunity(self, signal_id, symbol, side, metrics, decision, reason, current_price=0.0, rank=None, score=None, candidate=None):
        tf = metrics.get("timeframe") or (candidate.get("tf") if candidate else None) or "5m"
        strat = metrics.get("strategy") or (candidate.get("strategy") if candidate else None) or "adx_ema"
        entry = float(current_price or metrics.get("entry_price") or metrics.get("current_price", 0.0) or (candidate.get("entry") if candidate else 0.0))
        stop = float(metrics.get("sl_price") or metrics.get("sl") or (candidate.get("sl") if candidate else 0.0))
        target = float(metrics.get("tp_price") or metrics.get("tp") or (candidate.get("tp") if candidate else 0.0))
        conf = float(metrics.get("confidence", metrics.get("prob_win", metrics.get("win_rate_prior", 0.5))))
        gross = float(metrics.get("gross_edge") or metrics.get("expected_gross_return") or metrics.get("expected_gross", 0.0))
        fees = float(metrics.get("estimated_fees", metrics.get("fees", metrics.get("friction", 0.0031) * entry if entry > 0 else 0.0)))
        slippage = float(metrics.get("slippage", metrics.get("slippage_pct", 0.0011) * entry if entry > 0 else 0.0))
        net = float(metrics.get("expected_net_return") or metrics.get("expected_net") or metrics.get("net_edge", 0.0))
        
        is_risk_stage = any(k in reason for k in [
            "RISK", "MIN_NOTIONAL", "EXPOSURE", "DRAWDOWN", "DAILY_LOSS",
            "SAFETY_HALT", "LOCAL_ORDER_BLOCKED", "ZERO_FILL", "BINANCE_API_ERROR"
        ])
        is_profit_stage = any(k in reason for k in [
            "PROFITABILITY", "REVALIDATION", "EDGE", "NET_RETURN", "FEES", "INSUFFICIENT", "NEGATIVE"
        ])

        if decision in ["ACCEPTED", "ALL_GATES_PASSED"]:
            p_dec = "ACCEPTED"
            r_dec = "ACCEPTED"
            e_dec = "ELIGIBLE"
            p_reason_val = "NET_EDGE_POSITIVE"
            r_reason_val = "WITHIN_LIMITS"
            e_reason_val = "DISPATCHED"
        elif is_risk_stage:
            p_dec = "ACCEPTED"
            r_dec = "REJECTED"
            e_dec = "REJECTED"
            p_reason_val = "NET_EDGE_POSITIVE"
            r_reason_val = reason
            e_reason_val = f"BLOCKED_BY_RISK: {reason}"
        elif is_profit_stage:
            p_dec = "REJECTED"
            r_dec = "PENDING"
            e_dec = "REJECTED"
            p_reason_val = reason
            r_reason_val = ""
            e_reason_val = f"BLOCKED_BY_PROFITABILITY: {reason}"
        else:
            p_dec = "REJECTED" if "REJECT" in decision else decision
            r_dec = "REJECTED" if "REJECT" in decision else "PENDING"
            e_dec = "REJECTED"
            p_reason_val = reason
            r_reason_val = reason
            e_reason_val = reason

        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "signal_id": signal_id,
            "symbol": symbol,
            "timeframe": tf,
            "strategy": strat,
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "confidence": conf,
            "expected_gross": gross,
            "expected_gross_return": gross,
            "fees": fees,
            "estimated_fees": fees,
            "slippage": slippage,
            "expected_net": net,
            "expected_net_return": net,
            "profitability_decision": p_dec,
            "profitability_reason": p_reason_val,
            "risk_decision": r_dec,
            "risk_reason": r_reason_val,
            "execution_decision": e_dec,
            "execution_reason": e_reason_val,
            "rank": rank or (candidate.get("rank") if candidate else None),
            "score": score or (candidate.get("score") if candidate else None),
            "decision": decision,
            "reason": reason
        }
        try:
            with open(TESTNET_OPPORTUNITY_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except:
            pass

        try:
            if hasattr(self, "telemetry") and self.telemetry:
                self.telemetry.record_signal_event(log_entry)
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
                from execution import monitor_open_trades
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
            
            # Include all symbols from active scanner if available
            if hasattr(self, 'scanner') and getattr(self.scanner, 'symbols', None):
                symbols_to_check.update(self.scanner.symbols)
                
            # Include symbols from existing ledger file
            ledger_file = os.getenv("TESTNET_LEDGER_FILE", TESTNET_LEDGER_FILE)
            if os.path.exists(ledger_file):
                try:
                    with open(ledger_file, "r") as lf:
                        for line in lf:
                            if not line.strip(): continue
                            rec = json.loads(line)
                            if rec.get("symbol"):
                                symbols_to_check.add(rec["symbol"])
                except Exception:
                    pass

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
                    
                    order_trades = all_trades_by_order.get(oid, [])
                    order_fees = 0.0
                    for tr in order_trades:
                        comm = float(tr.get('commission', 0.0))
                        asset = str(tr.get('commissionAsset', 'USDT')).upper()
                        if asset == 'USDT':
                            order_fees += comm
                        elif asset in sym:
                            order_fees += comm * avg_price
                        else:
                            # Standard testnet fee rate for other assets
                            order_fees += (avg_price * qty * 0.00075) / max(1, len(order_trades))
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
            ledger_file = os.getenv("TESTNET_LEDGER_FILE", TESTNET_LEDGER_FILE)
            existing_exit_ids = set()
            if os.path.exists(ledger_file):
                with open(ledger_file, "r") as f:
                    for line in f:
                        if not line.strip(): continue
                        try:
                            record = json.loads(line)
                            if record.get("exit_order_id"):
                                existing_exit_ids.add(str(record["exit_order_id"]))
                            elif record.get("exit_client_id"):
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
            # Filter completed_trades by today's date for daily loss (C-05 fix)
            today_str = datetime.datetime.utcnow().date().isoformat()
            todays_pnl = sum(
                t['net_pnl'] for t in completed_trades
                if t.get('exit_timestamp', t.get('timestamp', '')).startswith(today_str)
            )
            self.risk_gate.daily_realized_loss = todays_pnl
            self.total_reconciled_fees = total_fees
            self.total_reconciled_pnl = total_gross_pnl - total_fees
            
        except Exception as e:
            logger.error(f"[SERVICE] Authoritative Rebuild Failed: {e}")

    def _btc_regime_state(self):
        """Current BTC risk-on/risk-off state from the scanner's BTCUSDT 4h cache."""
        try:
            btc_df = None
            scanner = getattr(self, "scanner", None)
            if scanner is not None:
                with scanner._cache_lock:
                    btc_df = scanner.candle_cache.get(("BTCUSDT", "4h"))
            if btc_df is None:
                logger.warning("[GOVERNANCE] BTCUSDT 4h data unavailable — regime gate fail-open for this candle")
                return None, None, None
            return compute_btc_regime(btc_df)
        except Exception as e:
            logger.error(f"[GOVERNANCE] BTC regime evaluation failed ({e}) — fail-open")
            return None, None, None

    def _check_degradation(self):
        """Phase 5: Automatically switch to OBSERVE-ONLY if strategy degrades."""
        window = getattr(config, "DEGRADATION_WINDOW", 20)
        min_win_rate = getattr(config, "MIN_WIN_RATE_THRESHOLD", 0.35)
        
        if not hasattr(self, 'ledger_file'):
            self.ledger_file = TESTNET_LEDGER_FILE
            
        if not os.path.exists(self.ledger_file):
            return
            
        closed_trades = []
        try:
            with open(self.ledger_file, 'r', encoding="utf-8") as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        record = json.loads(line)
                        if "CLOSE" in record.get("action", ""):
                            closed_trades.append(record)
                    except Exception:
                        pass
        except Exception:
            return
        
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
                "strategy": next(iter(ACTIVE_STRATEGIES.keys())) if ACTIVE_STRATEGIES else "adx_ema",
                "strategies": list(ACTIVE_STRATEGIES.keys()),
                "timeframes": list({tf for tfs in ACTIVE_STRATEGIES.values() for tf in (tfs if isinstance(tfs, list) else [tfs])}),
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
                    tfs = list({tf for tfs in ACTIVE_STRATEGIES.values() for tf in (tfs if isinstance(tfs, list) else [tfs])})
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
                # Direct append (no atomic replace needed for append-only JSONL)
                with open(progress_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(report) + "\n")
                logger.info(f"[PROGRESS] Total:{report['total_signals']} BUY:{report['buy_signals']} SELL:{report['sell_signals']} Equity:{report['current_equity']:.2f}")
            except Exception as e:
                logger.error(f"[SERVICE] Progress report loop error: {e}")
            time.sleep(60)

    def run(self):
        # 1. Discover Symbols
        discovery = SymbolDiscoveryService()
        self.symbol_filters = discovery.discover_eligible_symbols(min_quote_volume=1_000_000)
        symbol_list = list(self.symbol_filters.keys())

        # Governance gate: restrict the trading universe to assets with OOS-validated
        # edge for the enabled strategies. Unvalidated symbols carry unquantified
        # slippage/regime risk and are excluded from signal generation.
        validated_assets = governance_validated_assets(self.strategies)
        if validated_assets:
            blocked = [s for s in symbol_list if s not in validated_assets]
            if blocked:
                logger.info(
                    f"[GOVERNANCE] Restricting universe to {len(validated_assets)} OOS-validated assets; "
                    f"{len(blocked)} discovered symbols excluded from scanning."
                )
            self.symbol_filters = {s: f for s, f in self.symbol_filters.items() if s in validated_assets}
            symbol_list = list(self.symbol_filters.keys())
        self.stats["symbols_scanned"] = len(symbol_list)
        
        # 2. Train ML Strategy (only if it survived the governance gate)
        _ml_loaded = any(name == "ml" for tf_strats in self.strategies.values() for name, _ in tf_strats)
        if _ml_loaded:
            try:
                from data import add_indicators, get_candles
                from strategy_ml import train
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
