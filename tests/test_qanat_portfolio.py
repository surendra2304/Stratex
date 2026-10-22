"""
tests/test_qanat_portfolio.py — Unit tests for Qanat-inspired cross-sectional portfolio construction.
"""
from stratex_upgrade.qanat_portfolio import QanatPortfolioAllocator


def test_score_candidate_properties():
    allocator = QanatPortfolioAllocator()
    # Negative net return -> score 0.0
    assert allocator.score_candidate(-0.01, 0.02) == 0.0
    assert allocator.score_candidate(0.0, 0.02) == 0.0

    # Same return, different volatility: lower vol must yield higher score
    score_low_vol = allocator.score_candidate(0.01, 0.01) # 1% return, 1% ATR
    score_high_vol = allocator.score_candidate(0.01, 0.04) # 1% return, 4% ATR
    assert score_low_vol > score_high_vol

    # Persistence bonus
    score_p1 = allocator.score_candidate(0.01, 0.01, persisted_bars=1)
    score_p3 = allocator.score_candidate(0.01, 0.01, persisted_bars=3)
    assert score_p3 > score_p1


def test_portfolio_allocation_bounds():
    allocator = QanatPortfolioAllocator(
        max_total_exposure=0.05,
        max_single_exposure=0.02,
        min_score_hurdle=0.05
    )

    candidates = [
        {"symbol": "BTCUSDT", "side": "BUY", "expected_net_return": 0.015, "atr_pct": 0.01, "confidence": 0.9, "persisted_bars": 3},
        {"symbol": "ETHUSDT", "side": "BUY", "expected_net_return": 0.010, "atr_pct": 0.015, "confidence": 0.8, "persisted_bars": 2},
        {"symbol": "SOLUSDT", "side": "BUY", "expected_net_return": 0.005, "atr_pct": 0.02, "confidence": 0.7, "persisted_bars": 1},
        {"symbol": "DOGEUSDT", "side": "BUY", "expected_net_return": -0.005, "atr_pct": 0.03, "confidence": 0.5, "persisted_bars": 1},
    ]

    targets = allocator.allocate(candidates, current_equity=10000.0)

    # DOGE has negative return, must not be allocated
    symbols_allocated = [t.symbol for t in targets]
    assert "DOGEUSDT" not in symbols_allocated
    assert "BTCUSDT" in symbols_allocated

    # Sum of weights must not exceed max_total_exposure
    total_weight = sum(t.target_weight for t in targets)
    assert total_weight <= 0.05 + 1e-6

    # Every single position must obey max_single_exposure (0.02)
    for t in targets:
        assert t.target_weight <= 0.02 + 1e-6
        assert t.score > 0.0

    # Highest score (BTC) should have largest or equal weight to second (ETH)
    btc_target = next(t for t in targets if t.symbol == "BTCUSDT")
    eth_target = next(t for t in targets if t.symbol == "ETHUSDT")
    assert btc_target.target_weight >= eth_target.target_weight
