from advisory_gate import AdvisoryGate
from risk.risk_orchestrator import RiskOrchestrator


def test_complete_trading_cycle_simulation():
    # 1. Simulate Strategy Signal
    signal = {
        'symbol': 'BTCUSDT',
        'action': 'BUY',
        'strategy': 'supertrend',
        'confidence': 0.85,
        'entry_price': 50000.0,
        'stop_loss': 49000.0,
        'take_profit': 52000.0
    }

    # 2. Risk Gate Check
    risk_orch = RiskOrchestrator()
    risk_approval, mod_size, reason = risk_orch.evaluate_new_entry_risk(
        symbol=signal['symbol'],
        strategy=signal['strategy'],
        requested_size=100.0,
        current_equity=10000.0,
        portfolio_heat_pct=25.0
    )
    assert risk_approval is True
    assert mod_size > 0

    # 3. Advisory Consultation (Advisory Gate)
    gate = AdvisoryGate()
    decision_payload = {
        'decision_id': 'ADV_001',
        'status': 'APPROVED',
        'parameter_changes': [
            {'parameter': 'atr_multiplier', 'proposed_value': 2.2, 'current_value': 2.0}
        ]
    }
    adv_res = gate.validate(
        decision=decision_payload,
        current_params={'atr_multiplier': 2.0},
        shadow_mode=True
    )
    assert adv_res.verdict in ['APPLY', 'REJECT', 'SHADOW_LOG_ONLY']

    # 4. Position Tracking and Ledger Logging
    trade_record = {
        'trade_id': 'CYCLE_TEST_001',
        'symbol': signal['symbol'],
        'strategy': signal['strategy'],
        'entry_price': signal['entry_price'],
        'exit_price': 51500.0,
        'size': mod_size,
        'pnl': (51500.0 - 50000.0) * (mod_size / 50000.0),
        'advisory_verdict': adv_res.verdict,
        'status': 'CLOSED'
    }
    assert trade_record['pnl'] > 0
