"""
tests/test_strategy_profitability_and_trailing.py
Verifies:
1. supertrend is VALIDATED in PRODUCTION_STRATEGY_REGISTRY and accepted by governance_filter_strategies().
2. All active strategies enforce asymmetric Risk/Reward (>= 2.0:1) and protective stop losses (>= 1.5x ATR).
3. Trailing stop engine accepts trades with state='PROTECTED' or status='OPEN'.
4. Breakeven moves SL to cover entry + fee buffer at R >= 1.0.
5. Dynamic trailing stop trails behind market price at R >= 1.5.
"""

import pandas as pd
import pytest
import config
from config_strategy import PRODUCTION_STRATEGY_REGISTRY
from testnet_engine.service import governance_filter_strategies
from testnet_engine.trailing import compute_trail_target, stage_for_r_multiple, trailing_cycle
import strategy_supertrend
import strategy_bb_reversion
import strategy_factory_winners


def test_supertrend_governance_validation():
    """Verify supertrend is VALIDATED and passes governance filter."""
    assert "supertrend" in PRODUCTION_STRATEGY_REGISTRY
    entry = PRODUCTION_STRATEGY_REGISTRY["supertrend"]
    assert entry["status"] == "VALIDATED", "supertrend must be VALIDATED in registry"
    assert entry["rr_ratio"] >= 2.0, "supertrend must have RR ratio >= 2.0"

    filtered = governance_filter_strategies(config.ACTIVE_STRATEGIES)
    assert "supertrend" in filtered, "supertrend must pass governance_filter_strategies"
    assert "5m" in filtered["supertrend"]


def test_active_strategies_asymmetric_risk_reward():
    """Verify all active strategies enforce asymmetric RR >= 2.0 and SL >= 1.5x ATR."""
    assert strategy_bb_reversion._RR_RATIO >= 2.0
    assert strategy_bb_reversion._SL_ATR >= 1.5

    df = pd.DataFrame({
        "open": [100.0] * 40,
        "high": [101.0] * 40,
        "low": [99.0] * 40,
        "close": [100.0] * 40,
        "volume": [1000.0] * 40
    })

    for winner_fn in [
        strategy_factory_winners.get_signal_winner_1,
        strategy_factory_winners.get_signal_winner_2,
        strategy_factory_winners.get_signal_winner_3,
        strategy_factory_winners.get_signal_winner_4,
        strategy_factory_winners.get_signal_winner_5,
    ]:
        res = winner_fn(df)
        assert res.rr_ratio >= 2.0, f"{winner_fn.__name__} RR must be >= 2.0"


def test_trailing_stages_and_targets():
    """Verify breakeven and trailing stage transitions and target prices."""
    assert stage_for_r_multiple(0.8) is None
    assert stage_for_r_multiple(1.0) == "BREAKEVEN"
    assert stage_for_r_multiple(1.5) == "TRAILING"

    # BUY trade: entry 100, SL 98 (risk = 2.0), TP 106
    # Price reaches 102.5 (+1.25R -> BREAKEVEN)
    be_sl, stage = compute_trail_target("BUY", 100.0, 102.5, 98.0, 106.0, 2.0, "BREAKEVEN")
    assert stage == "BREAKEVEN"
    assert be_sl > 100.0, "Breakeven SL must be above entry price to cover fee buffer"
    assert be_sl < 102.5, "Breakeven SL must be below current price"

    # Price reaches 104.0 (+2.0R -> TRAILING with ATR=1.0)
    trail_sl, t_stage = compute_trail_target("BUY", 100.0, 104.0, be_sl, 106.0, 2.0, "TRAILING", 1.0)
    assert t_stage == "TRAILING"
    assert trail_sl > be_sl, "Trailing SL must ratchet upward"
    assert trail_sl < 104.0


def test_trailing_cycle_accepts_protected_state(monkeypatch):
    """Verify trailing_cycle processes trades where state='PROTECTED' and status is not set."""
    mock_trade = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "entry_price": 50000.0,
        "sl_price": 49000.0,
        "tp_price": 53000.0,
        "quantity": 0.01,
        "state": "PROTECTED",
        "is_futures": True,
        "last_trail_time": 0,
    }

    trades_saved = []
    def mock_load():
        return [mock_trade]
    def mock_save(trades):
        trades_saved.extend(trades)

    monkeypatch.setattr("execution._load_active_trades", mock_load)
    monkeypatch.setattr("execution._save_active_trades", mock_save)

    class MockClient:
        def futures_symbol_ticker(self, symbol):
            return {"price": "51500.0"}
        def futures_cancel_order(self, **kwargs):
            pass

    class MockService:
        client = MockClient()
        def get_atr(self, sym):
            return 500.0

    mock_service = MockService()

    def mock_rebuild(client, symbol, side, qty, entry_price, new_sl, tp_price):
        return {"tp_order_id": 999, "sl_order_id": 888}

    monkeypatch.setattr("testnet_engine.trailing._rebuild_futures_protection", mock_rebuild)

    trailing_cycle(mock_service)

    assert len(trades_saved) == 1
    updated = trades_saved[0]
    assert updated["sl_price"] > 50000.0, "SL should have been trailed above entry price"
    assert updated["trail_stage"] in ["BREAKEVEN", "TRAILING"]
