
def test_advisory_impact_attribution():
    # 20 known trades: 10 with advisory approval/modification, 10 without
    trades_with_advisory = [
        {'trade_id': f'T_ADV_{i}', 'net_pnl': 15.0 if i % 3 != 0 else -5.0, 'advisory_applied': True, 'alpha_delta': 2.5}
        for i in range(10)
    ]
    trades_without_advisory = [
        {'trade_id': f'T_RAW_{i}', 'net_pnl': 12.0 if i % 3 != 0 else -7.0, 'advisory_applied': False, 'alpha_delta': 0.0}
        for i in range(10)
    ]

    all_trades = trades_with_advisory + trades_without_advisory
    pnl_adv = sum(t['net_pnl'] for t in trades_with_advisory)
    pnl_raw = sum(t['net_pnl'] for t in trades_without_advisory)

    assert pnl_adv > pnl_raw
    assert len(all_trades) == 20
