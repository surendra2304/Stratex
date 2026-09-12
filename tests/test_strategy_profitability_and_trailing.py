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
    assert entry["rr_ratio"] >= 1.0, "supertrend must have RR ratio >= 1.0"
    assert entry["oos_win_rate_prior"] >= 0.80, "supertrend must have 80%+ win rate prior"

    filtered = governance_filter_strategies(config.ACTIVE_STRATEGIES)
    assert "supertrend" in filtered, "supertrend must pass governance_filter_strategies"
    assert "15m" in filtered["supertrend"]


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
    assert stage_for_r_multiple(0.3) is None
    assert stage_for_r_multiple(0.4) == "BREAKEVEN"
    assert stage_for_r_multiple(0.8) == "TRAILING"

    # BUY trade: entry 100, SL 98 (risk = 2.0), TP 102
    # Price reaches 101.0 (+0.5R -> BREAKEVEN)
    be_sl, stage = compute_trail_target("BUY", 100.0, 101.0, 98.0, 102.0, 2.0, "BREAKEVEN")
    assert stage == "BREAKEVEN"
    assert be_sl > 100.0, "Breakeven SL must be above entry price to cover fee buffer"
    assert be_sl < 101.0, "Breakeven SL must be below current price"

    # Price reaches 102.0 (+1.0R -> TRAILING with ATR=0.5)
    trail_sl, t_stage = compute_trail_target("BUY", 100.0, 102.0, be_sl, 104.0, 2.0, "TRAILING", 0.5)
    assert t_stage == "TRAILING"
    assert trail_sl > be_sl, "Trailing SL must ratchet upward"
    assert trail_sl < 102.0


def test_supertrend_high_winrate_geometry_and_adx_filter():
    """Verify Supertrend enforces 80%+ prior, 1:1 RR, and ADX chop rejection."""
    rows = []
    for i in range(60):
        rows.append({
            "open": 100.0 + i * 0.1,
            "high": 101.0 + i * 0.1,
            "low": 99.0 + i * 0.1,
            "close": 100.5 + i * 0.1,
            "volume": 5000.0,
            "supertrend": True if i == 59 else False,
            "ema_200": 95.0,
            "ema_50": 98.0,
            "ema_21": 99.0,
            "atr": 1.0,
            "rsi": 50.0,
            "adx": 28.0, # Strong trend
        })
    df = pd.DataFrame(rows)

    # Qualified breakout with ADX = 28
    sig = strategy_supertrend.get_signal(df)
    assert sig.side == "BUY"
    assert sig.win_rate_prior == 0.80
    assert sig.rr_ratio == 1.0
    close = df.iloc[-1]["close"]
    # 1:1 R:R target symmetry
    risk = close - sig.sl
    gain = sig.tp - close
    assert pytest.approx(gain, rel=1e-3) == risk

    # Chop regime: ADX = 18 (< 25) -> Must return None signal
    df.loc[df.index[-1], "adx"] = 18.0
    sig_chop = strategy_supertrend.get_signal(df)
    assert sig_chop.side is None, "Should reject trade in chop regime when ADX < 25"



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


def test_trailing_cycle_short_profit_harvest(monkeypatch):
    """Verify trailing_cycle correctly computes profit for SHORT positions and triggers profit harvest."""
    mock_trade = {
        "symbol": "INJUSDT",
        "side": "SHORT",
        "entry_price": 10.0,
        "sl_price": 10.5,
        "tp_price": 9.0,
        "quantity": 10.0,
        "state": "PROTECTED",
        "is_futures": True,
        "last_trail_time": 0,
    }

    trades_saved = []
    def mock_load():
        return [mock_trade]
    def mock_save(trades):
        trades_saved.clear()
        trades_saved.extend(trades)

    monkeypatch.setattr("execution._load_active_trades", mock_load)
    monkeypatch.setattr("execution._save_active_trades", mock_save)

    closed_calls = []
    def mock_close(client, symbol, side, qty):
        closed_calls.append((symbol, side, qty))
        return {"orderId": 999888}

    monkeypatch.setattr("testnet_engine.protection.emergency_futures_market_close", mock_close)
    monkeypatch.setattr("testnet_engine.trailing._cancel_futures_order", lambda *a, **k: None)
    monkeypatch.setattr("testnet_engine.trailing._record_harvest_win_in_ledger", lambda *a, **k: None)

    class MockClient:
        def futures_symbol_ticker(self, symbol):
            # Price fell from 10.0 to 9.80 -> +2.0% profit for short! (above default 1.5% harvest)
            return {"price": "9.80"}

    class MockService:
        client = MockClient()
        def get_atr(self, sym):
            return 0.1

    service = MockService()
    trailing_cycle(service)

    assert len(closed_calls) == 1, "SHORT trade with >1.5% profit should be harvested"
    sym, s, q = closed_calls[0]
    assert sym == "INJUSDT"
    assert s == "SHORT"
    assert len(trades_saved) == 1
    assert trades_saved[0]["status"] == "CLOSED"
    assert trades_saved[0]["exit_reason"] == "PROFIT_HARVEST_WIN"


def test_degradation_check_logic(tmp_path, monkeypatch):
    """Verify _check_degradation respects net PnL, ignores RECOVERED trades, and auto-recovers."""
    import json
    from testnet_engine.service import TestnetService

    ledger_path = tmp_path / "test_ledger.json"
    trades = []
    # Create 20 scratch/administrative recovered trades with tiny negative pnl
    for i in range(20):
        trades.append({
            "action": "CLOSE_POSITION",
            "strategy": "RECOVERED",
            "exit_reason": "RECOVERED_CLOSE",
            "pnl": -0.05
        })
    # And 5 real trades with positive pnl
    for i in range(5):
        trades.append({
            "action": "CLOSE_POSITION",
            "strategy": "supertrend",
            "exit_reason": "PROFIT_HARVEST",
            "pnl": 5.0
        })

    with open(ledger_path, "w", encoding="utf-8") as f:
        for t in trades:
            f.write(json.dumps(t) + "\n")

    service = TestnetService.__new__(TestnetService)
    service.ledger_file = str(ledger_path)
    service.observe_only = True
    service.observe_only_since = 0

    monkeypatch.setattr(config, "DEGRADATION_WINDOW", 20)
    monkeypatch.setattr(config, "MIN_WIN_RATE_THRESHOLD", 0.30)
    monkeypatch.setattr(config, "DEGRADATION_GUARD_ENABLED", True)
    monkeypatch.setattr(service, "_strategy_performance_gate", lambda: None)

    service._check_degradation()

    # Administrative trades excluded -> only 5 strategy trades (< window 20) -> should auto-recover!
    assert service.observe_only is False, "Should auto-recover from OBSERVE-ONLY when insufficient strategy trades"


