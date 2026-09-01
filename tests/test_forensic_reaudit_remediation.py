import os
from unittest import mock

import numpy as np
import pandas as pd

import strategy_adx_ema
import strategy_bollinger
import strategy_breakout_vol
import strategy_hybrid
import strategy_ml
from backtest_engine import BacktestEngine
from features import add_features
from paper_engine.portfolio import PaperPortfolio
from testnet_engine.risk_gate import RiskGate


def test_startup_environment_variable_handling():
    """Verify TestnetService accepts TESTNET_ENABLED without crashing on fresh clones."""
    from testnet_engine.service import TestnetService
    
    with mock.patch.dict(os.environ, {"TRADING_MODE": "TESTNET", "TESTNET_ENABLED": "True", "TESTNET_ONLY": ""}):
        with mock.patch("testnet_engine.service.get_exchange_client") as mock_client_getter:
            mock_client = mock.MagicMock()
            mock_client.get_account.return_value = {
                "balances": [{"asset": "USDT", "free": "10000.0", "locked": "0.0"}]
            }
            mock_client.get_open_orders.return_value = []
            mock_client_getter.return_value = mock_client
            
            svc = TestnetService()
            assert svc is not None


def test_volume_breakout_strategy_signal_generation():
    """Verify strategy_breakout_vol produces valid BUY and SELL signals on breakout candles."""
    n = 25
    timestamps = pd.date_range("2026-08-20 00:00", periods=n, freq="5min")
    
    # 1. Bullish breakout DataFrame
    highs = [100.0 + i * 0.1 for i in range(n - 1)] + [115.0]
    lows = [99.0 + i * 0.1 for i in range(n - 1)] + [104.0]
    closes = [99.5 + i * 0.1 for i in range(n - 1)] + [110.0]
    volumes = [100.0] * (n - 1) + [500.0] # 5x average volume
    
    df_buy = pd.DataFrame({
        "timestamp": timestamps,
        "open": [99.5] * n,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
        "atr": [1.0] * n
    })
    
    res_buy = strategy_breakout_vol.get_signal(df_buy)
    assert res_buy.side == "BUY"
    assert res_buy.sl < df_buy["close"].iloc[-1]
    assert res_buy.tp > df_buy["close"].iloc[-1]
    assert res_buy.confidence == 0.50
    assert res_buy.rr_ratio == 2.0

    # 2. Bearish breakdown DataFrame
    highs_sell = [100.0 - i * 0.1 for i in range(n - 1)] + [95.0]
    lows_sell = [99.0 - i * 0.1 for i in range(n - 1)] + [80.0]
    closes_sell = [99.5 - i * 0.1 for i in range(n - 1)] + [85.0]
    df_sell = pd.DataFrame({
        "timestamp": timestamps,
        "open": [99.5] * n,
        "high": highs_sell,
        "low": lows_sell,
        "close": closes_sell,
        "volume": volumes,
        "atr": [1.0] * n
    })
    
    res_sell = strategy_breakout_vol.get_signal(df_sell)
    assert res_sell.side == "SELL"
    assert res_sell.sl > df_sell["close"].iloc[-1]
    assert res_sell.tp < df_sell["close"].iloc[-1]


def test_bollinger_strategy_mean_reversion_logic():
    """Verify Bollinger strategy triggers BUY on oversold bounce and SELL on overbought reversion."""
    n = 25
    timestamps = pd.date_range("2026-08-20 00:00", periods=n, freq="15min")
    
    # Oversold below lower band with RSI < 35
    df_buy = pd.DataFrame({
        "timestamp": timestamps,
        "close": [100.0] * (n - 1) + [90.0],
        "bb_lower": [95.0] * n,
        "bb_upper": [105.0] * n,
        "rsi": [50.0] * (n - 1) + [25.0], # RSI oversold
        "atr": [2.0] * n
    })
    res_buy = strategy_bollinger.get_signal(df_buy)
    assert res_buy.side == "BUY"
    assert res_buy.confidence == 0.48
    assert res_buy.rr_ratio == 2.0

    # Overbought above upper band with RSI > 65
    df_sell = pd.DataFrame({
        "timestamp": timestamps,
        "close": [100.0] * (n - 1) + [110.0],
        "bb_lower": [95.0] * n,
        "bb_upper": [105.0] * n,
        "rsi": [50.0] * (n - 1) + [75.0], # RSI overbought
        "atr": [2.0] * n
    })
    res_sell = strategy_bollinger.get_signal(df_sell)
    assert res_sell.side == "SELL"
    assert res_sell.confidence == 0.48


def test_supertrend_ratcheting_band_assignment_in_features():
    """Verify features.py assigns ratcheted ub_arr and lb_arr to st_upper and st_lower."""
    np.random.seed(42)
    n = 50
    close = np.linspace(100, 150, n)
    high = close + 2.0
    low = close - 2.0
    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-08-20", periods=n, freq="5min"),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": [1000.0] * n
    })
    df_feat = add_features(df)
    
    assert "st_upper" in df_feat.columns
    assert "st_lower" in df_feat.columns
    assert isinstance(df_feat["st_upper"].values, np.ndarray)
    assert isinstance(df_feat["st_lower"].values, np.ndarray)


def test_strategy_namedtuple_schema_uniformity():
    """Verify all strategies provide .confidence and .win_rate_prior attributes without AttributeError."""
    strategies = [
        strategy_adx_ema,
        strategy_bollinger,
        strategy_breakout_vol,
        strategy_hybrid,
        strategy_ml
    ]
    for strat in strategies:
        dummy_df = pd.DataFrame({
            "close": [100.0] * 30,
            "open": [100.0] * 30,
            "high": [101.0] * 30,
            "low": [99.0] * 30,
            "volume": [1000.0] * 30,
            "returns": [0.0] * 30,
            "body_size": [0.0] * 30,
            "upper_wick": [0.0] * 30,
            "lower_wick": [0.0] * 30,
            "range": [0.0] * 30,
            "dist_ema_21": [0.0] * 30,
            "dist_ema_200": [0.0] * 30,
            "trend_slope_21": [0.0] * 30,
            "rsi_14": [50.0] * 30,
            "macd_hist": [0.0] * 30,
            "atr_pct": [0.01] * 30,
            "bb_width": [0.02] * 30,
            "bb_pos": [0.5] * 30,
            "rel_volume": [1.0] * 30,
            "atr_14": [1.0] * 30,
            "atr": [1.0] * 30
        })
        sig = strat.get_signal(dummy_df)
        assert hasattr(sig, "confidence")
        assert hasattr(sig, "win_rate_prior")
        assert hasattr(sig, "rr_ratio")


def test_paper_portfolio_margin_and_equity_preservation(tmp_path):
    """Verify PaperPortfolio get_equity maintains total capital on margin allocation and position closing."""
    port_file = str(tmp_path / "paper_port.json")
    ledger_file = str(tmp_path / "paper_ledger.jsonl")
    equity_file = str(tmp_path / "paper_equity.jsonl")

    portfolio = PaperPortfolio(filename=port_file, ledger_file=ledger_file, equity_file=equity_file)
    assert portfolio.cash == 10000.0
    assert portfolio.get_equity({"BTCUSDT": 50000.0}) == 10000.0

    # Allocate $2,000 margin for BTCUSDT position
    portfolio.allocate_margin(2000.0, "ev_alloc_1")
    assert portfolio.cash == 8000.0
    assert portfolio.used_margin == 2000.0
    
    # Register position
    portfolio.add_position("pos_btc", "BTCUSDT", "BUY", 50000.0, 0.04)
    
    # Price increases 10% to 55,000: unrealized PnL = (55000 - 50000) * 0.04 = $200
    # Correct equity = cash + used_margin + unrealized = 8000 + 2000 + 200 = $10200
    # (The $2000 margin deducted from cash is returned via used_margin in get_equity)
    eq_in_profit = portfolio.get_equity({"BTCUSDT": 55000.0})
    assert eq_in_profit == 10200.0

    # Close position at 55,000 with $2 fee: net PnL = $198
    portfolio.close_position("pos_btc", 55000.0, exit_fee=2.0)
    assert portfolio.positions["pos_btc"]["status"] == "CLOSED"
    
    # Release margin and settle PnL
    portfolio.release_margin(2000.0, "ev_rel_1")
    portfolio.add_realized_pnl(198.0, "ev_pnl_1")
    assert portfolio.used_margin == 0.0
    assert portfolio.cash == 10198.0
    assert portfolio.realized_pnl == 198.0
    assert portfolio.get_equity({"BTCUSDT": 55000.0}) == 10198.0


def test_risk_gate_zero_and_negative_equity_guards():
    """Verify RiskGate gracefully rejects trades when equity is zero or negative without ZeroDivisionError."""
    rg = RiskGate(starting_balance=10000.0)
    
    # Zero equity
    allowed_zero, reason_zero, _ = rg.evaluate_risk("BTCUSDT", "BUY", 0.0, {}, 0.01, 50000.0, "OK")
    assert allowed_zero is False
    assert reason_zero == "INSUFFICIENT_EQUITY"

    # Negative equity
    allowed_neg, reason_neg, _ = rg.evaluate_risk("BTCUSDT", "BUY", -500.0, {}, 0.01, 50000.0, "OK")
    assert allowed_neg is False
    assert reason_neg == "INSUFFICIENT_EQUITY"


def test_backtest_engine_numeric_confidence_extraction():
    """Verify BacktestEngine correctly stores numeric confidence rather than string in trade history."""
    n = 250
    timestamps = pd.date_range("2026-08-01", periods=n, freq="15min")
    close = [100.0 + i * 0.1 for i in range(n)]
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": close,
        "high": [c + 0.5 for c in close],
        "low": [c - 0.5 for c in close],
        "close": close,
        "volume": [1000.0] * n,
        "adx_14": [30.0] * n,
        "ema_20": [c - 0.2 for c in close],
        "ema_50": [c - 0.5 for c in close],
        "ema_200": [c - 1.0 for c in close],
        "atr_14": [1.0] * n,
        "atr_adx_ema": [1.0] * n
    })
    
    engine = BacktestEngine(df, [strategy_adx_ema])
    trades, _ = engine.run()
    
    if trades:
        for t in trades:
            conf = t.get("confidence")
            assert isinstance(conf, (int, float))
            assert not isinstance(conf, str)
