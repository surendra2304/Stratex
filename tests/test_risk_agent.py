from stratex_agents.order_intent import TradeIntent
from stratex_agents.risk_agent import DeterministicRiskAgent


class FakeRiskGate:
    def __init__(self, result=(True, "RISK_OK", "")):
        self.result = result
        self.calls = []

    def evaluate_risk(self, *args):
        self.calls.append(args)
        return self.result


def binance_intent():
    return TradeIntent(
        venue="binance", symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.01, environment="binance_demo", stop_loss=95,
        take_profit=110, strategy="risk-test", client_order_id="stx-risk-agent-001",
    )


def mt5_intent():
    return TradeIntent(
        venue="mt5", symbol="XAUUSD.a", side="BUY", order_type="MARKET",
        quantity=0.1, environment="mt5_demo", stop_loss=2290,
        take_profit=2320, strategy="risk-test", client_order_id="stx-risk-agent-mt5-001",
    )


def context():
    return {
        "current_equity": 1000,
        "active_positions": {},
        "entry_price": 100,
        "data_health_status": "OK",
    }


def test_risk_agent_uses_existing_deterministic_binance_risk_gate():
    gate = FakeRiskGate()
    agent = DeterministicRiskAgent(
        binance_risk_gate=gate,
        execution_policy=lambda: (True, "ALLOWED_FUTURES"),
    )
    result = agent.authorize(binance_intent(), context())
    assert result.approved is True
    assert result.reason == "RISK_OK"
    assert len(gate.calls) == 1


def test_risk_agent_rejects_when_binance_risk_gate_denies():
    agent = DeterministicRiskAgent(
        binance_risk_gate=FakeRiskGate((False, "MAX_EXPOSURE_REACHED", "over limit")),
        execution_policy=lambda: (True, "ALLOWED_FUTURES"),
    )
    result = agent.authorize(binance_intent(), context())
    assert result.approved is False
    assert result.reason == "MAX_EXPOSURE_REACHED"
    assert "over limit" in result.details


def test_risk_agent_fails_closed_on_missing_context():
    gate = FakeRiskGate()
    agent = DeterministicRiskAgent(
        binance_risk_gate=gate,
        execution_policy=lambda: (True, "ALLOWED_FUTURES"),
    )
    result = agent.authorize(binance_intent(), {"current_equity": 1000})
    assert result.approved is False
    assert result.reason == "RISK_CONTEXT_INCOMPLETE"
    assert gate.calls == []


def test_risk_agent_fails_closed_on_execution_policy_denial():
    gate = FakeRiskGate()
    agent = DeterministicRiskAgent(
        binance_risk_gate=gate,
        execution_policy=lambda: (False, "PAPER_BLOCKED"),
    )
    result = agent.authorize(binance_intent(), context())
    assert result.approved is False
    assert result.reason == "EXECUTION_POLICY_BLOCKED"
    assert gate.calls == []


def test_risk_agent_does_not_reuse_binance_quantity_price_risk_for_mt5():
    agent = DeterministicRiskAgent(
        binance_risk_gate=FakeRiskGate(),
        execution_policy=lambda: (True, "ALLOWED_FUTURES"),
    )
    result = agent.authorize(mt5_intent(), context())
    assert result.approved is False
    assert result.reason == "MT5_INSTRUMENT_RISK_EVALUATOR_REQUIRED"


def test_risk_agent_can_use_explicit_broker_aware_mt5_evaluator():
    agent = DeterministicRiskAgent(
        execution_policy=lambda: (True, "ALLOWED_FUTURES"),
        mt5_risk_evaluator=lambda _intent, _ctx: (True, "MT5_RISK_OK", "broker sizing confirmed"),
    )
    result = agent.authorize(mt5_intent(), context())
    assert result.approved is True
    assert result.reason == "MT5_RISK_OK"
    assert result.details == "broker sizing confirmed"
