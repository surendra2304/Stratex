import os
import pytest
from deployment.live_authorization import LiveAuthorizationVerifier, create_physical_authorization_file
from risk.live_enforcer import LiveRiskEnforcer

def test_live_authorization_and_enforcement_cycle(tmp_path):
    auth_file = str(tmp_path / '.live_trading_authorized')
    verifier = LiveAuthorizationVerifier(auth_file=auth_file)

    # 1. Unmet requirements -> refusal
    res = verifier.verify_all_authorizations()
    assert res.is_authorized is False

    # 2. Fulfill physical file
    create_physical_authorization_file(level=1, authorized_capital=1000.0, filepath=auth_file)
    assert os.path.exists(auth_file)

    # 3. Enforcer limit protection
    enforcer = LiveRiskEnforcer(level=1, initial_capital=1000.0)
    allowed, msg = enforcer.validate_new_entry('BTC/USDT', 50.0, [], 1000.0)
    assert allowed is True

    # Exceed level limit (150 > 5% of 1000)
    blocked, block_msg = enforcer.validate_new_entry('BTC/USDT', 150.0, [], 1000.0)
    assert blocked is False
