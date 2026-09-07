"""
stratex_upgrade/qanat_portfolio.py — Qanat-inspired Cross-Sectional Portfolio Construction.

Mathematical formulation based on Qanat (fidetolabs/qanat examples/equity/steps/portfolio.py):
    "risk-adjusted momentum, nudged by tone:
     score = momentum / vol_annual.clip(lower=1e-6) + 0.25 * tone
     edge = picked['score'].clip(lower=0.0)
     weight = (edge / edge.sum()) if edge.sum() > 0 else 1.0 / len(picked)"

Adapted for crypto systematic trading in Stratex:
    score = expected_net_return / max(0.001, atr_pct)
    Weights are allocated strictly proportional to net statistical edge, normalized by
    asset volatility (ATR%), and capped strictly by MAX_SINGLE_ASSET_EXPOSURE.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PortfolioTarget:
    symbol: str
    side: str
    target_weight: float
    score: float
    expected_net_return: float
    volatility_atr_pct: float
    persisted_bars: int


class QanatPortfolioAllocator:
    """
    Constructs cross-sectional portfolio allocations prioritizing highest risk-adjusted edge.
    """

    def __init__(
        self,
        max_total_exposure: float = 0.05,        # 5% default total portfolio exposure
        max_single_exposure: float = 0.02,       # 2% max per single symbol
        min_score_hurdle: float = 0.05,          # Minimum risk-adjusted score
    ):
        self.max_total_exposure = float(max_total_exposure)
        self.max_single_exposure = float(max_single_exposure)
        self.min_score_hurdle = float(min_score_hurdle)

    def score_candidate(
        self,
        expected_net_return: float,
        atr_pct: float,
        confidence: float = 1.0,
        persisted_bars: int = 1,
    ) -> float:
        """
        Calculate Qanat risk-adjusted edge score.

        Formula:
            score = (expected_net_return * confidence) / max(0.001, atr_pct)
            Bonus scaling: * min(1.5, 1.0 + 0.1 * (persisted_bars - 1))
        """
        if expected_net_return <= 0:
            return 0.0

        vol_floor = max(0.001, float(atr_pct))
        base_score = (float(expected_net_return) * float(confidence)) / vol_floor
        persistence_multiplier = min(1.5, 1.0 + 0.1 * max(0, persisted_bars - 1))
        
        return round(base_score * persistence_multiplier, 6)

    def allocate(
        self,
        candidates: list[dict],
        current_equity: float,
    ) -> list[PortfolioTarget]:
        """
        Allocate target position weights across qualified candidates.

        Parameters
        ----------
        candidates : list of dicts with:
            - symbol, side, expected_net_return, atr_pct, confidence, persisted_bars
        current_equity : float

        Returns
        -------
        list of PortfolioTarget
        """
        if not candidates or current_equity <= 0:
            return []

        scored = []
        for c in candidates:
            exp_net = float(c.get("expected_net_return", 0.0))
            atr_pct = float(c.get("atr_pct", 0.01))
            conf = float(c.get("confidence", 1.0))
            bars = int(c.get("persisted_bars", 1))

            score = self.score_candidate(exp_net, atr_pct, conf, bars)
            if score >= self.min_score_hurdle:
                scored.append({
                    "symbol": c["symbol"],
                    "side": c["side"],
                    "score": score,
                    "expected_net_return": exp_net,
                    "atr_pct": atr_pct,
                    "persisted_bars": bars,
                    "raw_candidate": c
                })

        if not scored:
            return []

        # Sort descending by score
        scored.sort(key=lambda x: x["score"], reverse=True)

        # Compute edge-proportional weights
        total_score = sum(s["score"] for s in scored)
        targets: list[PortfolioTarget] = []

        remaining_total_exposure = self.max_total_exposure

        for s in scored:
            if remaining_total_exposure <= 0:
                break

            # Proportion of total score
            raw_weight = (s["score"] / total_score) * self.max_total_exposure
            # Bound by max single asset exposure and remaining room
            bounded_weight = min(self.max_single_exposure, raw_weight, remaining_total_exposure)
            
            if bounded_weight >= 0.001:  # at least 0.1% allocation
                targets.append(PortfolioTarget(
                    symbol=s["symbol"],
                    side=s["side"],
                    target_weight=round(bounded_weight, 5),
                    score=s["score"],
                    expected_net_return=s["expected_net_return"],
                    volatility_atr_pct=s["atr_pct"],
                    persisted_bars=s["persisted_bars"],
                ))
                remaining_total_exposure -= bounded_weight

        return targets
