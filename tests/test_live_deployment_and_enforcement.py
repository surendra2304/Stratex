import os
import json
import pytest
from deployment.live_authorization import LiveAuthorizationVerifier, create_physical_authorization_file
from deployment.capital_levels import get_level_spec, check_demotion_trigger, GRADUATED_LEVELS
from risk.live_enforcer import LiveRiskEnforcer
from ledger.live_ledger import LiveLedgerManager

def test_capital_levels_specs():
    l1 = get_level_spec(1)
    assert l1.min_capital == 500.0
    assert l1.max_capital == 1000.0
    assert l1.max_position_size_pct == 0.05
    assert l1.max_daily_loss_pct == 0.02
    assert l1.max_drawdown_limit_pct == 0.05

    l2 = get_level_spec(2)
    assert l2.min_capital == 2000.0
    assert l2.max_capital == 5000.0
    assert l2.max_strategies == 3

    l3 = get_level_spec(3)
    assert l3.max_capital == 25000.0

    l4 = get_level_spec(4)
    assert l4.allow_custom_params is True

def test_automatic_demotion_triggers():
    # Drawdown demotion at Level 2 -> Level 1
    demote, new_lvl, reason = check_demotion_trigger(current_level=2, current_drawdown_pct=9.5, consecutive_loss_days=0)
    assert demote is True
    assert new_lvl == 1

    # Consecutive loss days demotion
    demote_days, new_lvl_days, r_days = check_demotion_trigger(current_level=2, current_drawdown_pct=2.0, consecutive_loss_days=3)
    assert demote_days is True
    assert new_lvl_days == 1

def test_live_authorization_gates(tmp_path):
    auth_file = str(tmp_path / '.live_trading_authorized')
    verifier = LiveAuthorizationVerifier(auth_file=auth_file)
    
    # Missing physical file
    state = verifier.verify_all_authorizations()
    assert state.is_authorized is False
    assert any('Gate 2 Failed' in e for e in state.blocking_errors)

    # Create physical file
    create_physical_authorization_file(level=1, authorized_capital=1000.0, filepath=auth_file)
    assert os.path.exists(auth_file)

def test_live_risk_enforcer_invariants():
    enforcer = LiveRiskEnforcer(level=1, initial_capital=1000.0)

    # Normal entry allowed
    ok, msg = enforcer.validate_new_entry(
        symbol='BTC/USDT',
        notional=45.0, # 4.5% of 1000
        current_open_positions=[],
        current_equity=1000.0
    )
    assert ok is True

    # Position size exceeding 5% cap
    ok_large, msg_large = enforcer.validate_new_entry(
        symbol='BTC/USDT',
        notional=100.0, # 10% > 5%
        current_open_positions=[],
        current_equity=1000.0
    )
    assert ok_large is False
    assert 'exceeds Level 1 max position cap' in msg_large

    # Daily loss breach
    d_ok, d_msg = enforcer.evaluate_daily_loss(today_realized_pnl=-25.0, current_equity=975.0) # >  max loss
    assert d_ok is False
    assert enforcer.status.is_halted is True

    # Kill switch
    kill_res = enforcer.trigger_kill_switch(source='TEST_SUITE')
    assert kill_res['action'] == 'FLATTEN_ALL'
    assert enforcer.status.kill_switch_active is True

def test_isolated_live_ledger(tmp_path):
    t_file = str(tmp_path / 'live_trade_ledger.jsonl')
    e_file = str(tmp_path / 'live_equity_curve.jsonl')
    b_file = str(tmp_path / 'live_balance_events.jsonl')
    r_file = str(tmp_path / 'live_risk_events.jsonl')

    mgr = LiveLedgerManager(trade_file=t_file, equity_file=e_file, balance_file=b_file, risk_file=r_file)
    mgr.record_live_trade({'trade_id': 'LIVE_001', 'net_pnl': 15.50, 'symbol': 'BTC/USDT'})
    mgr.record_live_equity_snapshot(equity=1015.50, cash=950.0)

    reconciled, disc, msg = mgr.reconcile_exchange_balance(exchange_reported_balance=1015.50, local_calculated_balance=1015.50)
    assert reconciled is True
    assert disc == 0.0

    # Discrepancy > 0.5%
    bad_rec, bad_disc, bad_msg = mgr.reconcile_exchange_balance(exchange_reported_balance=950.0, local_calculated_balance=1015.50)
    assert bad_rec is False
    assert bad_disc > 0.5

def test_live_dashboard_endpoints():
    from dashboard import app
    client = app.test_client()

    res = client.get('/api/live/status')
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'OK'
    assert 'level_spec' in data
    assert 'voice_summary' in data

    post_halt = client.post('/api/live/emergency/halt')
    assert post_halt.status_code == 200

    post_flat = client.post('/api/live/emergency/flatten')
    assert post_flat.status_code == 200
    assert post_flat.get_json()['enforcer_action']['action'] == 'FLATTEN_ALL'
