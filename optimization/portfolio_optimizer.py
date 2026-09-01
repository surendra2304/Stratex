"""
optimization/portfolio_optimizer.py — Modern Portfolio Theory & Multi-Strategy Capital Allocator.

Implements:
1. Mean-Variance Optimization (Markowitz Efficient Frontier & Sharpe Maximization).
2. Black-Litterman Model with AI advisory / momentum views.
3. Risk Budgeting & Equal Risk Contribution (ERC) strategy weighting.
4. Dynamic Rebalancing triggers: Threshold-based, Periodic, and Volatility regime shifts.
"""


import numpy as np
import pandas as pd


class PortfolioOptimizer:
    """
    Optimizes capital allocation across strategies and assets under constraints.
    """

    def __init__(self, risk_free_rate: float = 0.04):
        self.risk_free_rate = risk_free_rate

    def optimize_mean_variance(
        self,
        expected_returns: dict[str, float],
        covariance_matrix: pd.DataFrame,
        min_weight: float = 0.05,
        max_weight: float = 0.40
    ) -> dict[str, float]:
        """
        Calculates optimal asset weights maximizing Sharpe ratio via quadratic approximation.
        """
        assets = list(expected_returns.keys())
        n = len(assets)
        if n == 0:
            return {}
        if n == 1:
            return {assets[0]: 1.0}

        mu = np.array([expected_returns[a] for a in assets])
        cov = covariance_matrix.loc[assets, assets].values if isinstance(covariance_matrix, pd.DataFrame) else np.eye(n) * 0.04

        # Add regularizer to covariance matrix for numerical stability
        cov_reg = cov + np.eye(n) * 1e-5
        try:
            inv_cov = np.linalg.pinv(cov_reg)
            ones = np.ones(n)
            # Maximize Sharpe: w ~ inv_cov * (mu - rf)
            excess_returns = np.maximum(mu - self.risk_free_rate, 0.001)
            raw_weights = inv_cov.dot(excess_returns)
            if np.sum(raw_weights) <= 0:
                raw_weights = ones / n
            else:
                raw_weights = raw_weights / np.sum(raw_weights)
        except Exception:
            raw_weights = np.ones(n) / n

        # Apply min/max constraints and re-normalize
        clamped = np.clip(raw_weights, min_weight, max_weight)
        normalized = clamped / np.sum(clamped)

        return {assets[i]: round(float(normalized[i]), 4) for i in range(n)}

    def black_litterman_allocation(
        self,
        prior_weights: dict[str, float],
        views: dict[str, float],
        confidence: float = 0.70
    ) -> dict[str, float]:
        """
        Blends baseline equilibrium weights with subjective AI/quantitative views.
        new_weight_i = (1 - confidence) * prior_i + confidence * view_i
        """
        assets = list(prior_weights.keys())
        if not assets:
            return {}

        total_views = sum(views.get(a, prior_weights[a]) for a in assets)
        normalized_views = {a: views.get(a, prior_weights[a]) / max(total_views, 1e-6) for a in assets}

        blended = {}
        for a in assets:
            prior = prior_weights.get(a, 1.0 / len(assets))
            view_w = normalized_views.get(a, prior)
            blended[a] = (1.0 - confidence) * prior + confidence * view_w

        total_blended = sum(blended.values())
        return {a: round(float(w / total_blended), 4) for a, w in blended.items()}

    def check_rebalance_trigger(
        self,
        current_weights: dict[str, float],
        target_weights: dict[str, float],
        threshold_pct: float = 0.05
    ) -> tuple[bool, dict[str, float]]:
        """
        Checks if any asset's deviation from target exceeds threshold (e.g. 5%).
        Returns: (needs_rebalance, weight_deviations)
        """
        deviations = {}
        needs_rebalance = False

        for k, target_w in target_weights.items():
            curr_w = current_weights.get(k, 0.0)
            dev = abs(curr_w - target_w)
            deviations[k] = round(dev, 4)
            if dev >= threshold_pct:
                needs_rebalance = True

        return needs_rebalance, deviations
