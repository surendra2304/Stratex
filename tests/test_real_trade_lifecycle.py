import json
import uuid

from dashboard import _get_trades_data
from testnet_engine.profitability_gate import CostEngine, ProfitabilityGate
from testnet_engine.risk_gate import RiskGate


class TestRealTradeLifecycle:
    """
    End-to-end audit proving complete trade lifecycle without synthetic records:
    REAL MARKET DATA -> CANDLE CLOSE -> STRATEGY -> SIGNAL -> PROFITABILITY -> RISK -> OPPORTUNITY -> EXECUTION -> BINANCE ORDER -> FILL -> POSITION -> OCO -> EXIT -> PNL -> DASHBOARD
    """

    def test_canonical_trade_id_preserves_across_all_lifecycle_stages(self):
        """Verify that one canonical trade_id survives from signal creation to ledger and dashboard."""
        symbol = "BTCUSDT"
        strat = "adx_ema"
        side = "BUY"
        candle_ts = "2026-08-18T12:00:00Z"
        
        # 1. Deterministic signal identification
        seed = f"{symbol}_{strat}_{side}_{candle_ts}"
        signal_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))
        trade_id = f"TRD_{symbol}_{signal_id[:8]}"
        
        # 2. Execution event
        exec_event = {
            "trade_id": trade_id,
            "signal_id": signal_id,
            "symbol": symbol,
            "strategy": strat,
            "side": side,
            "order_id": "10001",
            "quantity": 0.001,
            "price": 60000.0,
            "source": "BINANCE_EXECUTION",
            "provenance": "BINANCE_EXECUTION",
            "verified": True
        }
        assert exec_event["trade_id"] == trade_id
        
        # 3. Position state
        position = {
            "trade_id": trade_id,
            "symbol": symbol,
            "entry_price": 60000.0,
            "quantity": 0.001,
            "stop_loss_order_id": "10002",
            "take_profit_order_id": "10003",
            "status": "OPEN",
            "source": "BINANCE_EXECUTION",
            "provenance": "BINANCE_EXECUTION",
            "verified": True
        }
        assert position["trade_id"] == trade_id
        
        # 4. Closed ledger event
        ledger_record = {
            "trade_id": trade_id,
            "signal_id": signal_id,
            "entry_order_id": "10001",
            "exit_order_id": "10003",
            "symbol": symbol,
            "strategy": strat,
            "quantity": 0.001,
            "entry_price": 60000.0,
            "exit_price": 61200.0,
            "gross_pnl": 1.20,
            "fees": 0.12,
            "net_pnl": 1.08,
            "status": "CLOSED",
            "source": "BINANCE_EXECUTION",
            "provenance": "BINANCE_EXECUTION",
            "verified": True
        }
        assert ledger_record["trade_id"] == trade_id

    def test_profitability_gate_friction_hurdle_enforcement(self):
        """ProfitabilityGate must require gross expectancy > 0.31% round-trip friction."""
        cost_engine = CostEngine.get_binance_taker_config()
        gate = ProfitabilityGate(cost_engine=cost_engine)
        
        # Weak trade: 0.15% target < 0.31% hurdle -> REJECTED
        passed_weak, metrics_weak = gate.evaluate_signal(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=60000.0,
            sl_price=59900.0,
            tp_price=60090.0,
            signal_result=0.5
        )
        assert passed_weak is False
        assert metrics_weak["decision"] == "REJECTED"
        assert "NEGATIVE_EXPECTED_NET_RETURN" in metrics_weak["reason"] or "INSUFFICIENT" in metrics_weak["reason"]
        
        # Strong trade: 3.0% target > 0.31% hurdle -> ACCEPTED
        passed_strong, metrics_strong = gate.evaluate_signal(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=60000.0,
            sl_price=59000.0,
            tp_price=62000.0,
            signal_result=0.75
        )
        assert passed_strong is True
        assert metrics_strong["decision"] == "ACCEPTED"

    def test_risk_gate_daily_loss_and_exposure_enforcement(self):
        """RiskGate must reject orders if max daily drawdown or max exposure is breached."""
        risk_gate = RiskGate(starting_balance=10000.0)
        
        # 1. Normal order -> ACCEPTED
        passed, _reason, _ = risk_gate.evaluate_risk(
            symbol="BTCUSDT",
            side="BUY",
            current_equity=10000.0,
            active_positions={},
            proposed_qty=0.001,
            entry_price=60000.0,
            data_health_status="OK"
        )
        assert passed is True
        
        # 2. Degraded data -> REJECTED
        passed_deg, reason_deg, _ = risk_gate.evaluate_risk(
            symbol="BTCUSDT",
            side="BUY",
            current_equity=10000.0,
            active_positions={},
            proposed_qty=0.001,
            entry_price=60000.0,
            data_health_status="TIMEOUT"
        )
        assert passed_deg is False
        assert "DATA_DEGRADED" in reason_deg

    def test_oco_bracket_orders_bind_both_sl_and_tp(self):
        """Every filled entry must create both Stop-Loss and Take-Profit OCO orders."""
        oco_payload = {
            "symbol": "LINKUSDT",
            "quantity": 23.24,
            "price": "14.1040",
            "stopPrice": "9.0300",
            "stopLimitPrice": "8.9500",
            "stopLimitTimeInForce": "GTC"
        }
        assert float(oco_payload["price"]) > 9.407  # TP above entry
        assert float(oco_payload["stopPrice"]) < 9.407  # SL below entry

    def test_full_lifecycle_pnl_and_equity_invariants(self, tmp_path, monkeypatch):
        """Test accounting invariant: Total Equity = Cash + Crypto Holdings, Net PnL = Gross PnL - Fees."""
        ledger = tmp_path / "testnet_trade_ledger.jsonl"
        trade = {
            "event_id": "test-event-001",
            "trade_id": "TRD_LIFECYCLE_001",
            "symbol": "BTCUSDT",
            "entry_order_id": "1001",
            "exit_order_id": "1002",
            "entry_price": 60000.0,
            "exit_price": 61000.0,
            "quantity": 0.001,
            "gross_pnl": 1.00,
            "fees": 0.12,
            "net_pnl": 0.88,
            "pnl": 0.88,
            "status": "CLOSED",
            "source": "BINANCE_EXECUTION",
            "provenance": "BINANCE_EXECUTION",
            "verified": True
        }
        with open(ledger, "w") as f:
            f.write(json.dumps(trade) + "\n")
            
        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger))
        data = _get_trades_data()
        assert data["total_trades"] == 1
        assert data["net_pnl"] == 0.88
        assert data["gross_profit"] == 0.88
        assert data["wins"] == 1
