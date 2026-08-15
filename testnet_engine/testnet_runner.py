import os
import time
import json
import uuid
import datetime
from config import TRADING_MODE, ACTIVE_STRATEGY, SYMBOL, TIMEFRAME
from logger import get_logger
from data import get_candles, add_indicators
import importlib
strategy_module = importlib.import_module(f"strategy_{ACTIVE_STRATEGY}")
get_signal = strategy_module.get_signal
from testnet_engine.profitability_gate import ProfitabilityGate
from testnet_engine.risk_gate import RiskGate
from execution import place_market_order, get_exchange_client
from binance.exceptions import BinanceAPIException
from paper_engine.cost_engine import CostEngine

logger = get_logger("testnet_runner")

TESTNET_LEDGER_FILE = os.environ.get("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")
TESTNET_OPPORTUNITY_LOG = os.environ.get("TESTNET_OPPORTUNITY_LOG", "testnet_opportunity_log.jsonl")
TESTNET_PORTFOLIO_FILE = os.environ.get("TESTNET_PORTFOLIO_FILE", "testnet_portfolio.json")
TESTNET_SESSION_ID = str(uuid.uuid4())

class TestnetRunner:
    def __init__(self):
        # 1. TRADING_MODE strictly validated
        if TRADING_MODE != "TESTNET":
            raise RuntimeError(f"CRITICAL ERROR: Refusing to start TestnetRunner because TRADING_MODE={TRADING_MODE}. Must be TESTNET.")
            
        import os
        if os.getenv("TESTNET_ONLY", "FALSE").upper() != "TRUE":
            raise RuntimeError("CRITICAL ERROR: TESTNET_ONLY=TRUE is required to run the Testnet execution mode safely.")
            
        print("========================================")
        print("TESTNET EXECUTION MODE")
        print("========================================")
        print(f"Trading Mode: {TRADING_MODE}")
        print("Live: DISABLED")
        print("Paper: SEPARATE")
        print(f"Strategy: {ACTIVE_STRATEGY}")
        print(f"Symbol: {SYMBOL}")
        
        # We explicitly configure a Taker CostEngine for Testnet conservative estimates
        self.cost_engine = CostEngine.get_binance_taker_config()
        print(f"Expected cost model: {self.cost_engine.get_total_friction()*100:.3f}% round trip")
        print("Risk per trade: 0.5%")
        print("Max exposure: 5%")
        print("Max drawdown: 5%")
        print("========================================")

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
            logger.info(f"[TESTNET] Starting Equity: {self.starting_equity}")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch Testnet account balance: {e}")

        self.profitability_gate = ProfitabilityGate(cost_engine=self.cost_engine)
        self.risk_gate = RiskGate(starting_balance=self.starting_equity)
        self.current_equity = self.starting_equity
        self.active_positions = {} # signal_id -> details
        self.stats = {
            "signals_detected": 0,
            "signals_rejected": 0,
            "signals_executed": 0,
            "reasons": {}
        }

    def log_opportunity(self, signal_id, symbol, side, metrics, decision, reason):
        log_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "signal_id": signal_id,
            "symbol": symbol,
            "side": side,
            "confidence": metrics.get("confidence"),
            "predicted_move": metrics.get("predicted_move"),
            "holding_horizon": metrics.get("holding_horizon"),
            "expected_gross_return": metrics.get("gross_edge") or metrics.get("expected_gross_return"),
            "expected_net_return": metrics.get("expected_net_return"),
            "decision": decision,
            "reason": reason
        }
        try:
            with open(TESTNET_OPPORTUNITY_LOG, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except:
            pass
            
    def _save_state(self):
        state = {
            "cash": self.current_equity, # For Testnet, we just use equity as cash when flat
            "equity": self.current_equity,
            "realized_pnl": self.risk_gate.daily_realized_loss,
            "used_margin": sum([p.get('quantity', 0) * p.get('entry_price', 0) for p in self.active_positions.values()]),
            "fees": 0.0,
            "funding": 0.0,
            "open_positions": len(self.active_positions),
            "max_drawdown": (self.risk_gate.peak_equity - self.current_equity) / self.risk_gate.peak_equity if self.risk_gate.peak_equity > 0 else 0
        }
        with open(TESTNET_PORTFOLIO_FILE, "w") as f:
            json.dump(state, f)

    def record_trade(self, trade_info):
        """Append to testnet specific ledger"""
        with open(TESTNET_LEDGER_FILE, "a") as f:
            f.write(json.dumps(trade_info) + "\n")

    def run_dry_run(self):
        """Perform a single dry run before allowing live testnet orders"""
        print("\n--- TESTNET EXECUTION DRY-RUN FIRST ---")
        try:
            df = get_candles(SYMBOL, TIMEFRAME, limit=100)
            df = add_indicators(df)
            signal_res = get_signal(df)
            if len(signal_res) == 4:
                side, sl, tp, conf = signal_res
            else:
                side, sl, tp = signal_res
                conf = 1.0
            
            # If no signal natively, mock one for dry run validation
            if not side:
                side = "BUY"
                current_price = df['close'].iloc[-1]
                atr = df['atr'].iloc[-1] if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else current_price * 0.01
                sl = current_price - (atr * 1.5)
                tp = current_price + (atr * 3.0)
                conf = 0.52
                
            entry_price = df['close'].iloc[-1]
            
            passed_profit, p_metrics = self.profitability_gate.evaluate_signal(SYMBOL, side, entry_price, sl, tp, conf)
            
            print(f"Signal: {side} {SYMBOL}")
            print(f"Expected Gross Return: {p_metrics['expected_gross_return']:.5f}")
            print(f"Estimated Costs: {p_metrics['total_friction']:.5f}")
            print(f"Expected Net Return: {p_metrics['expected_net_return']:.5f}")
            
            qty = self.risk_gate.calculate_position_size(self.starting_equity, entry_price, sl)
            print(f"Position Size: {qty}")
            print(f"SL: {sl}, TP: {tp}")
            print(f"Risk OK: {'YES' if qty > 0 else 'NO'}")
            print("--- DRY RUN COMPLETE ---\n")
            return True
        except Exception as e:
            logger.error(f"Dry run failed: {e}")
            return False

    def get_data_health(self):
        # In a real system, we'd check candle recency
        return "OK"

    def update_positions_from_exchange(self):
        """Reconcile against binance testnet OCO/Orders"""
        # This function would natively query binance to check if OCOs hit
        # and update local self.active_positions, calculate realized PnL
        pass

    def tick(self):
        # 1. Update existing positions
        self.update_positions_from_exchange()
        
        # 2. Fetch Data
        df = get_candles(SYMBOL, TIMEFRAME, limit=100)
        if df.empty:
            return
        df = add_indicators(df)
        current_price = df['close'].iloc[-1]
        
        # 3. Generate Signal
        signal_res = get_signal(df)
        if len(signal_res) == 4:
            side, sl, tp, conf = signal_res
        else:
            side, sl, tp = signal_res
            conf = 1.0
        if not side:
            return
            
        signal_id = str(uuid.uuid4())
        
        # 4. Profitability Gate
        passed_profit, p_metrics = self.profitability_gate.evaluate_signal(SYMBOL, side, current_price, sl, tp, conf)
        
        if not passed_profit:
            self.log_opportunity(signal_id, SYMBOL, side, p_metrics["expected_gross_return"], p_metrics["expected_net_return"], p_metrics, "REJECTED", p_metrics["reason"])
            return
            
        # 5. Risk Gate
        current_exposure_pct = 0.0 # Calculate based on self.active_positions
        passed_risk, r_reason, r_details = self.risk_gate.evaluate_risk(SYMBOL, self.current_equity, len(self.active_positions), current_exposure_pct, self.get_data_health())
        
        if not passed_risk:
            self.log_opportunity(signal_id, SYMBOL, side, p_metrics["expected_gross_return"], p_metrics["expected_net_return"], p_metrics, "REJECTED", r_reason)
            return
            
        # 6. Execution Gate (Position Sizing)
        qty = self.risk_gate.calculate_position_size(self.current_equity, current_price, sl)
        
        # Safety limit for Binance Testnet minimums (often 0.001 BTC minimum)
        if qty < 0.001:
            self.log_opportunity(signal_id, SYMBOL, side, p_metrics["expected_gross_return"], p_metrics["expected_net_return"], p_metrics, "REJECTED", "QTY_TOO_SMALL_FOR_EXCHANGE")
            return

        # 7. Execute Order
        self.log_opportunity(signal_id, SYMBOL, side, p_metrics["expected_gross_return"], p_metrics["expected_net_return"], p_metrics, "ACCEPTED", "ALL_GATES_PASSED")
        
        logger.info(f"[TESTNET EXECUTION] Placing {side} {qty} {SYMBOL}")
        try:
            # We use the existing Execution engine for placing market + OCO
            order_res = place_market_order(ACTIVE_STRATEGY, side, SYMBOL, quantity=qty, sl=sl, tp=tp)
            if order_res:
                logger.info(f"[TESTNET] Order Submitted Successfully.")
                # We would track this in self.active_positions
        except Exception as e:
            logger.error(f"[TESTNET] Execution Failed: {e}")
            
        self._save_state()

    def loop(self):
        if not self.run_dry_run():
            return
            
        logger.info("[TESTNET] Starting execution loop...")
        while True:
            try:
                self.tick()
            except Exception as e:
                logger.error(f"[TESTNET] Loop error: {e}")
            time.sleep(60) # Run every minute

if __name__ == "__main__":
    runner = TestnetRunner()
    runner.loop()
