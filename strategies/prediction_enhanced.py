"""
strategies/prediction_enhanced.py — Prediction-Aware Strategy Filters & Confidence Modifiers.

CRITICAL INVARIANTS:
1. Predictions are FILTERS, never TRIGGERS. A strategy NEVER enters a trade from a prediction alone.
2. Predictions can only VETO or REDUCE position sizing, never force an entry.
3. Exit Acceleration: Accelerates stop/take profit exit if deep learning forecast strongly turns adverse.
"""

from intelligence.prediction_client import PredictionClient
from logger import get_logger

logger = get_logger("prediction_enhanced")


class PredictionEnhancedStrategyFilter:
    """
    Applies predictive intelligence as a risk and confidence filter on top of base strategy signals.
    """

    def __init__(
        self,
        prediction_client: PredictionClient | None = None,
        min_agreement_confidence: float = 0.65,
        veto_conflicting_signals: bool = True
    ):
        self.client = prediction_client or PredictionClient()
        self.min_agreement_confidence = min_agreement_confidence
        self.veto_conflicting_signals = veto_conflicting_signals

    def evaluate_entry_filter(
        self,
        symbol: str,
        strategy_signal: int,  # +1 for BUY, -1 for SELL, 0 for NONE
        base_size: float
    ) -> tuple[bool, float, str]:
        """
        Evaluates whether predictive intelligence allows the strategy entry and adjusts sizing.
        Returns: (allow_entry, modulated_size, reason)
        """
        if strategy_signal == 0:
            return False, 0.0, "NO_BASE_SIGNAL"

        pred = self.client.get_prediction(symbol)
        if not pred:
            return True, base_size, "NO_PREDICTION_FALLBACK_PERMITTED"

        # Check alignment
        is_bullish_signal = (strategy_signal > 0)
        pred_is_bullish = (pred.direction == "BULLISH")
        pred_is_bearish = (pred.direction == "BEARISH")

        # Conflict check
        if is_bullish_signal and pred_is_bearish and pred.confidence >= self.min_agreement_confidence:
            if self.veto_conflicting_signals:
                logger.info(f"[PRED_FILTER] 🚫 Vetoed BUY on {symbol}: AI-Universe Bearish forecast ({pred.confidence*100:.0f}%)")
                return False, 0.0, f"VETOED_BY_AI_BEARISH_PREDICTION_{int(pred.confidence*100)}PCT"
            else:
                # Reduce size by 50%
                return True, round(base_size * 0.5, 6), "REDUCED_SIZE_DUE_TO_AI_CONFLICT"

        if not is_bullish_signal and pred_is_bullish and pred.confidence >= self.min_agreement_confidence:
            if self.veto_conflicting_signals:
                logger.info(f"[PRED_FILTER] 🚫 Vetoed SELL on {symbol}: AI-Universe Bullish forecast ({pred.confidence*100:.0f}%)")
                return False, 0.0, f"VETOED_BY_AI_BULLISH_PREDICTION_{int(pred.confidence*100)}PCT"
            else:
                return True, round(base_size * 0.5, 6), "REDUCED_SIZE_DUE_TO_AI_CONFLICT"

        # Concurrence boost: If prediction agrees with high confidence, keep 100% sizing
        if (is_bullish_signal and pred_is_bullish) or (not is_bullish_signal and pred_is_bearish):
            return True, base_size, f"APPROVED_WITH_AI_CONCURRENCE_{int(pred.confidence*100)}PCT"

        return True, base_size, "APPROVED_NEUTRAL_PREDICTION"

    def check_early_exit_acceleration(
        self,
        symbol: str,
        current_side: str,  # "LONG" or "SHORT"
        current_pnl_pct: float
    ) -> tuple[bool, str]:
        """
        Evaluates whether an active position should exit early due to strong adverse forecast.
        """
        pred = self.client.get_prediction(symbol)
        if not pred or pred.confidence < 0.80:
            return False, "HOLD"

        if current_side == "LONG" and pred.direction == "BEARISH":
            return True, f"ACCELERATED_EXIT_STRONG_BEARISH_FORECAST_{int(pred.confidence*100)}PCT"

        if current_side == "SHORT" and pred.direction == "BULLISH":
            return True, f"ACCELERATED_EXIT_STRONG_BULLISH_FORECAST_{int(pred.confidence*100)}PCT"

        return False, "HOLD"
