"""
analysis/multi_timeframe.py — Cross-Timeframe Signal Consensus & Momentum Alignment Engine.

Capabilities:
1. Multi-Timeframe Ingestion: Ingests 1m, 5m, 15m, 1h, 4h, 1d series.
2. Weighted Consensus: Computes aggregate directional score with higher weights on macro trends.
3. Divergence & Conflict Detection: Flags when higher timeframe trend conflicts with low timeframe counter-trend entry.
"""

from typing import Any

import pandas as pd

import features


class MultiTimeframeAnalyzer:
    """
    Evaluates multi-timeframe candle datasets to establish trend alignment and filter low-probability trades.
    """

    DEFAULT_WEIGHTS = {
        "1m": 0.05,
        "5m": 0.15,
        "15m": 0.20,
        "1h": 0.30,
        "4h": 0.20,
        "1d": 0.10
    }

    def __init__(self, tf_weights: dict[str, float] | None = None):
        self.weights = tf_weights or self.DEFAULT_WEIGHTS

    def evaluate_trend_for_dataframe(self, df: pd.DataFrame) -> tuple[int, float]:
        """
        Determines trend direction (+1 for bullish, -1 for bearish, 0 for neutral) and strength (0..1).
        """
        if df is None or len(df) < 30:
            return 0, 0.0

        df_feat = features.add_features(df.copy())
        last = df_feat.iloc[-1]

        close = float(last["close"])
        ema_fast = float(last.get("ema_9", close))
        ema_slow = float(last.get("ema_21", close))
        rsi = float(last.get("rsi_14", 50.0))
        adx = float(last.get("adx", 20.0))

        score = 0
        if close > ema_fast > ema_slow:
            score += 1
        elif close < ema_fast < ema_slow:
            score -= 1

        if rsi > 55:
            score += 1
        elif rsi < 45:
            score -= 1

        direction = 1 if score > 0 else (-1 if score < 0 else 0)
        strength = min(1.0, adx / 50.0) if adx > 0 else 0.5
        return direction, strength

    def compute_consensus(
        self,
        timeframe_dfs: dict[str, pd.DataFrame]
    ) -> dict[str, Any]:
        """
        Computes weighted consensus score across available timeframes.
        Consensus score ranges from -1.0 (Strong Bearish) to +1.0 (Strong Bullish).
        """
        if not timeframe_dfs:
            return {"consensus_score": 0.0, "recommended_bias": "NEUTRAL", "alignment": "NONE"}

        total_weight = 0.0
        weighted_score = 0.0
        tf_results = {}

        for tf, df in timeframe_dfs.items():
            w = self.weights.get(tf, 0.10)
            direction, strength = self.evaluate_trend_for_dataframe(df)
            tf_results[tf] = {"direction": direction, "strength": strength, "weight": w}
            weighted_score += (direction * strength) * w
            total_weight += w

        final_score = weighted_score / max(total_weight, 1e-6)
        final_score = round(max(-1.0, min(1.0, final_score)), 4)

        if final_score >= 0.35:
            bias = "BULLISH"
        elif final_score <= -0.35:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        # Check alignment consistency
        directions = [res["direction"] for res in tf_results.values() if res["direction"] != 0]
        all_aligned = len(directions) >= 2 and all(d == directions[0] for d in directions)

        return {
            "consensus_score": final_score,
            "recommended_bias": bias,
            "all_aligned": all_aligned,
            "timeframe_details": tf_results
        }
