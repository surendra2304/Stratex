"""
tests/test_decay_smoothing.py — Unit tests for Qanat-inspired Signal Decay Smoother.
"""
from stratex_upgrade.decay import SignalDecaySmoother


def test_single_signal_conviction():
    smoother = SignalDecaySmoother(decay_steps=4, confirmation_threshold=0.50)
    res = smoother.update("BTCUSDT", "15m", "adx_ema", "BUY", 1.0)
    assert res.raw_signal == 1.0
    # First signal has no past history, smoothed = 1.0
    assert res.smoothed_signal == 1.0
    assert res.conviction == 1.0
    assert res.is_confirmed is True
    assert res.action == "BUY"
    assert res.persisted_bars == 1


def test_alternating_twitches_filtered_as_noise():
    """Rapid twitching between BUY and SELL must be smoothed and filtered as noise."""
    smoother = SignalDecaySmoother(decay_steps=4, confirmation_threshold=0.50)
    # Step 1: BUY
    smoother.update("BTCUSDT", "15m", "noise_strat", "BUY", 1.0)
    # Step 2: SELL (twitch)
    # History: [+1.0, -1.0], weights [1, 2], sum = 3
    # smoothed = (1 * 1.0 + 2 * (-1.0)) / 3 = -1/3 = -0.3333
    res2 = smoother.update("BTCUSDT", "15m", "noise_strat", "SELL", 1.0)
    assert abs(res2.smoothed_signal - (-1.0 / 3.0)) < 1e-5
    assert res2.conviction < 0.50
    assert res2.is_confirmed is False
    assert res2.action == "FILTERED"


def test_sustained_trend_reaches_full_conviction():
    """A sustained signal across consecutive bars accumulates maximum conviction."""
    smoother = SignalDecaySmoother(decay_steps=4, confirmation_threshold=0.50)
    for i in range(4):
        res = smoother.update("ETHUSDT", "1h", "trend_strat", "BUY", 1.0)
        assert res.is_confirmed is True
        assert res.action == "BUY"

    assert res.persisted_bars == 4
    assert res.smoothed_signal == 1.0
    assert res.conviction == 1.0


def test_decay_linear_weights_calculation():
    """Verify exact linear weighting formula: sum(k * s_k) / sum(k)."""
    smoother = SignalDecaySmoother(decay_steps=3, confirmation_threshold=0.40)
    # Ingest 3 signals: BUY(0.2), BUY(0.6), BUY(1.0)
    smoother.update("SOLUSDT", "15m", "test", "BUY", 0.2)
    smoother.update("SOLUSDT", "15m", "test", "BUY", 0.6)
    res3 = smoother.update("SOLUSDT", "15m", "test", "BUY", 1.0)

    # Expected: (1 * 0.2 + 2 * 0.6 + 3 * 1.0) / (1 + 2 + 3) = (0.2 + 1.2 + 3.0) / 6 = 4.4 / 6 = 0.73333...
    expected = (1 * 0.2 + 2 * 0.6 + 3 * 1.0) / 6.0
    assert abs(res3.smoothed_signal - expected) < 1e-5
    assert res3.is_confirmed is True


def test_decay_reset():
    smoother = SignalDecaySmoother(decay_steps=4)
    smoother.update("BTCUSDT", "15m", "strat", "BUY", 1.0)
    smoother.reset("BTCUSDT")
    res = smoother.update("BTCUSDT", "15m", "strat", "HOLD", 0.0)
    assert res.persisted_bars == 0
