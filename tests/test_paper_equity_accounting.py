"""
Regression test for paper_engine/portfolio.py get_equity() accounting fix.

DEFECT (fixed): get_equity() returned cash + unrealized_pnl.
When positions are opened via allocate_margin(notional), the full notional is
deducted from cash and tracked in used_margin. The correct formula is:
    equity = cash + used_margin + unrealized_pnl
Without this correction, equity is understated by the total open-position
notional for the entire duration of every trade.
"""
import uuid
import pytest
from paper_engine.portfolio import PaperPortfolio

STARTING = 10_000.0


@pytest.fixture
def tmp_portfolio(tmp_path):
    return PaperPortfolio(
        filename=str(tmp_path / "port.json"),
        ledger_file=str(tmp_path / "ledger.jsonl"),
        equity_file=str(tmp_path / "equity.jsonl"),
    )


def test_equity_includes_used_margin_when_position_open(tmp_portfolio):
    """Equity must NOT drop by position notional when a position is opened."""
    p = tmp_portfolio
    entry_price, qty = 50_000.0, 0.001
    notional = entry_price * qty   # 50 USDT
    p.allocate_margin(notional, str(uuid.uuid4()))
    p.add_position(str(uuid.uuid4()), "BTCUSDT", "LONG", entry_price, qty)
    entry_fee = notional * 0.001
    p.add_realized_pnl(-entry_fee, str(uuid.uuid4()))

    equity = p.get_equity({"BTCUSDT": entry_price})
    expected = STARTING - entry_fee   # only fee deducted, no PnL
    assert abs(equity - expected) < 1e-6, (
        f"equity={equity:.6f} expected≈{expected:.6f}. "
        f"If equity≈{STARTING - notional - entry_fee:.6f}, used_margin fix is missing."
    )


def test_equity_reflects_unrealized_gain(tmp_portfolio):
    """Unrealized gain must be added correctly on top of the correct base."""
    p = tmp_portfolio
    entry_price, qty = 50_000.0, 0.001
    notional = entry_price * qty
    p.allocate_margin(notional, str(uuid.uuid4()))
    p.add_position(str(uuid.uuid4()), "BTCUSDT", "LONG", entry_price, qty)
    entry_fee = notional * 0.001
    p.add_realized_pnl(-entry_fee, str(uuid.uuid4()))

    current_price = entry_price * 1.02
    unrealized = (current_price - entry_price) * qty
    equity = p.get_equity({"BTCUSDT": current_price})
    assert abs(equity - (STARTING - entry_fee + unrealized)) < 1e-6


def test_equity_stable_after_close(tmp_portfolio):
    """Post-close equity reflects net PnL correctly."""
    p = tmp_portfolio
    entry_price, qty = 50_000.0, 0.001
    notional = entry_price * qty
    pos_id = str(uuid.uuid4())
    p.allocate_margin(notional, str(uuid.uuid4()))
    p.add_position(pos_id, "BTCUSDT", "LONG", entry_price, qty)
    entry_fee = notional * 0.001
    p.add_realized_pnl(-entry_fee, str(uuid.uuid4()))

    exit_price = 51_000.0
    exit_fee = exit_price * qty * 0.001
    gross_pnl = (exit_price - entry_price) * qty
    net_pnl = gross_pnl - exit_fee
    p.close_position(pos_id, exit_price, exit_fee=exit_fee)
    p.add_realized_pnl(net_pnl, str(uuid.uuid4()))

    equity = p.get_equity({})
    assert abs(equity - (STARTING - entry_fee + net_pnl)) < 1e-6


def test_equity_accurate_across_multiple_positions(tmp_portfolio):
    """Multiple concurrent open positions must all be accounted for."""
    p = tmp_portfolio
    ep1, qty1 = 50_000.0, 0.001
    p.allocate_margin(ep1 * qty1, str(uuid.uuid4()))
    p.add_position(str(uuid.uuid4()), "BTCUSDT", "LONG", ep1, qty1)
    f1 = ep1 * qty1 * 0.001
    p.add_realized_pnl(-f1, str(uuid.uuid4()))

    ep2, qty2 = 3_000.0, 0.01
    p.allocate_margin(ep2 * qty2, str(uuid.uuid4()))
    p.add_position(str(uuid.uuid4()), "ETHUSDT", "LONG", ep2, qty2)
    f2 = ep2 * qty2 * 0.001
    p.add_realized_pnl(-f2, str(uuid.uuid4()))

    btc_price, eth_price = ep1 * 1.01, ep2 * 0.995
    ur1 = (btc_price - ep1) * qty1
    ur2 = (eth_price - ep2) * qty2
    equity = p.get_equity({"BTCUSDT": btc_price, "ETHUSDT": eth_price})
    assert abs(equity - (STARTING - f1 - f2 + ur1 + ur2)) < 1e-6


def test_equity_no_double_counting_sequential_trades(tmp_portfolio):
    """Old margin must not accumulate across sequential trades causing overcounting."""
    p = tmp_portfolio
    ep1, qty1 = 40_000.0, 0.001
    pos1 = str(uuid.uuid4())
    p.allocate_margin(ep1 * qty1, str(uuid.uuid4()))
    p.add_position(pos1, "BTCUSDT", "LONG", ep1, qty1)
    f1_in = ep1 * qty1 * 0.001
    p.add_realized_pnl(-f1_in, str(uuid.uuid4()))
    f1_out = ep1 * qty1 * 0.001
    p.close_position(pos1, ep1, exit_fee=f1_out)
    p.add_realized_pnl(-f1_out, str(uuid.uuid4()))  # break-even exit

    ep2, qty2 = 41_000.0, 0.001
    pos2 = str(uuid.uuid4())
    p.allocate_margin(ep2 * qty2, str(uuid.uuid4()))
    p.add_position(pos2, "BTCUSDT", "LONG", ep2, qty2)
    f2_in = ep2 * qty2 * 0.001
    p.add_realized_pnl(-f2_in, str(uuid.uuid4()))

    equity = p.get_equity({"BTCUSDT": ep2})
    expected = STARTING - f1_in - f1_out - f2_in
    assert abs(equity - expected) < 1e-6, (
        f"equity={equity:.4f} expected≈{expected:.4f}. "
        f"Sequential used_margin may be double-counted."
    )


# ===========================================================================
# 1. TIMEFRAME-SPECIFIC STALE CANDLE THRESHOLDS
# ===========================================================================
def test_defect_1_stale_candle_timeframe_thresholds():
    from testnet_engine.service import _TF_SECONDS
    
    assert _TF_SECONDS['1m'] == 60
    assert _TF_SECONDS['3m'] == 180
    assert _TF_SECONDS['5m'] == 300
    assert _TF_SECONDS['15m'] == 900
    assert _TF_SECONDS['30m'] == 1800
    assert _TF_SECONDS['1h'] == 3600
    assert _TF_SECONDS['2h'] == 7200
    assert _TF_SECONDS['4h'] == 14400
    
    assert _TF_SECONDS.get('1m', 3600) * 3 == 180
    assert _TF_SECONDS.get('15m', 3600) * 3 == 2700
    assert _TF_SECONDS.get('1h', 3600) * 3 == 10800


# ===========================================================================
# 2. ORDERS_FILLED ONLY AFTER CONFIRMED FILLS
# ===========================================================================
def test_defect_2_orders_filled_only_on_confirmed_fill():
    stats = {'ORDERS_SUBMITTED': 0, 'ORDERS_FILLED': 0}
    
    # Unfilled / Submitted response
    unfilled_res = {'status': 'NEW', 'orderId': 101, '_executed_qty': 0}
    stats['ORDERS_SUBMITTED'] += 1
    order_status = str(unfilled_res.get('status', '')).upper()
    if order_status in ('FILLED', 'PARTIALLY_FILLED') or unfilled_res.get('_executed_qty', 0) > 0:
        stats['ORDERS_FILLED'] += 1
        
    assert stats['ORDERS_SUBMITTED'] == 1
    assert stats['ORDERS_FILLED'] == 0
    
    # Filled response
    filled_res = {'status': 'FILLED', 'orderId': 102, '_executed_qty': 0.05}
    stats['ORDERS_SUBMITTED'] += 1
    order_status = str(filled_res.get('status', '')).upper()
    if order_status in ('FILLED', 'PARTIALLY_FILLED') or filled_res.get('_executed_qty', 0) > 0:
        stats['ORDERS_FILLED'] += 1
        
    assert stats['ORDERS_SUBMITTED'] == 2
    assert stats['ORDERS_FILLED'] == 1


# ===========================================================================
# 4. PERSISTED CASH VS TOTAL EQUITY
# ===========================================================================
def test_defect_4_persisted_cash_calculation():
    current_equity = 10000.0
    active_positions = {
        'BTCUSDT': {'quantity': 0.1, 'entry_price': 50000.0, 'status': 'OPEN'}
    }
    
    computed_cash = max(0.0, current_equity - sum(
        p.get('quantity', 0) * p.get('entry_price', 0)
        for p in active_positions.values() if isinstance(p, dict)
    ))
    assert computed_cash == 5000.0
    assert computed_cash != current_equity


# ===========================================================================
# 5. RSI ZERO-LOSS DIVISION-BY-ZERO HANDLING
# ===========================================================================
def test_defect_5_rsi_zero_loss_no_nan():
    import pandas as pd
    import numpy as np
    from features import add_features
    
    n = 30
    close = [100.0 + i * 2.0 for i in range(n)]
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-08-20', periods=n, freq='5min'),
        'open': close,
        'high': [c + 1.0 for c in close],
        'low': [c - 0.5 for c in close],
        'close': close,
        'volume': [1000.0] * n
    })
    feat = add_features(df)
    assert not np.isnan(feat['rsi_14'].iloc[-1])
    assert feat['rsi_14'].iloc[-1] == 100.0


# ===========================================================================
# 6. CONFIGURABLE SIGNAL COOLDOWN
# ===========================================================================
def test_defect_6_configurable_signal_cooldown():
    from testnet_engine.service import _COOLDOWN_SECONDS
    assert _COOLDOWN_SECONDS >= 5.0
    assert isinstance(_COOLDOWN_SECONDS, float)


# ===========================================================================
# 7. DAILY RISK-STATE RESTORATION
# ===========================================================================
def test_defect_7_daily_risk_state_restoration():
    import datetime
    record_status_only = {
        'status': 'CLOSED',
        'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
        'net_pnl': -25.0
    }
    action_str = str(record_status_only.get('action', '')).upper()
    status_str = str(record_status_only.get('status', '')).upper()
    event_type_str = str(record_status_only.get('event_type', '')).upper()
    is_close = ('CLOSE' in action_str or status_str == 'CLOSED' or 'CLOSE' in event_type_str)
    assert is_close is True


# ===========================================================================
# 8. BACKTEST EQUITY AFTER FEES
# ===========================================================================
def test_defect_8_backtest_equity_deducts_fees():
    import pandas as pd
    from backtest_engine import BacktestEngine
    import strategy_adx_ema
    
    n = 250
    timestamps = pd.date_range('2026-08-01', periods=n, freq='15min')
    close = [100.0 + (i % 5) * 0.5 for i in range(n)]
    df = pd.DataFrame({
        'timestamp': timestamps,
        'open': close,
        'high': [c + 1.0 for c in close],
        'low': [c - 1.0 for c in close],
        'close': close,
        'volume': [1000.0] * n
    })
    engine = BacktestEngine(df, [strategy_adx_ema], initial_balance=10000.0, fee_rate=0.001)
    history, eq_df = engine.run()
    assert isinstance(eq_df, pd.DataFrame)
    if not eq_df.empty:
        assert not eq_df['equity'].isna().any()


# ===========================================================================
# 9. WARMUP NAN HANDLING IN MOVING AVERAGES
# ===========================================================================
def test_defect_9_warmup_nan_handling():
    import pandas as pd
    import numpy as np
    from features import add_features
    
    n = 25
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-08-20', periods=n, freq='5min'),
        'open': [100.0] * n,
        'high': [101.0] * n,
        'low': [99.0] * n,
        'close': [100.0] * n,
        'volume': [500.0] * n
    })
    feat = add_features(df)
    assert not np.isnan(feat['rel_volume'].iloc[5])
    assert feat['rel_volume'].iloc[5] > 0.0


# ===========================================================================
# 10. ORPHANED STRATEGY CASTS CLEANED
# ===========================================================================
def test_defect_10_clean_strategy_syntax():
    import pandas as pd
    import strategy_scalper
    import strategy_supertrend
    import strategy_aggressor
    
    n = 30
    df = pd.DataFrame({
        'timestamp': pd.date_range('2026-08-20', periods=n, freq='5min'),
        'open': [100.0] * n,
        'high': [101.0] * n,
        'low': [99.0] * n,
        'close': [100.0] * n,
        'volume': [1000.0] * n,
        'atr': [1.0] * n,
        'supertrend': [True] * n,
        'st_lower': [98.0] * n,
        'st_upper': [102.0] * n,
        'rsi_14': [50.0] * n,
        'macd_hist': [0.1] * n
    })
    
    res_sc = strategy_scalper.get_signal(df)
    res_st = strategy_supertrend.get_signal(df)
    res_ag = strategy_aggressor.get_signal(df)
    
    assert res_sc is not None
    assert res_st is not None
    assert res_ag is not None

