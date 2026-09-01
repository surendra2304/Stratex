from autonomy.operations_director import AutonomousOperationsDirector
from risk.live_enforcer import LiveRiskEnforcer


def test_emergency_procedures_scenarios():
    enforcer = LiveRiskEnforcer(level=1, initial_capital=1000.0)

    # Scenario 1: Kill switch
    res = enforcer.trigger_kill_switch(source='FRIDAY_VOICE_ASSISTANT')
    assert res['action'] == 'FLATTEN_ALL'
    assert enforcer.status.kill_switch_active is True

    # Scenario 2: Operations director drawdown response
    director = AutonomousOperationsDirector(autonomy_level=3)
    resp = director.run_high_frequency_cycle(current_drawdown_pct=13.5, active_positions_count=4)
    assert resp['action'] == 'FLATTEN_AND_HALT'
    assert resp['status'] == 'CRITICAL_DRAWDOWN'
