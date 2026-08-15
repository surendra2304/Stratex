"""
profitability_gate.py — Expected Net Edge gate

Strategy type semantics
-----------------------
PROBABILISTIC (e.g. ML):
    confidence is a calibrated probability of winning (output of predict_proba).
    prob_win = confidence directly.

RULE_BASED (e.g. ADX+EMA):
    confidence carries NO probabilistic meaning.
    prob_win is derived from win_rate_prior — the OOS-validated historical
    win rate carried inside the SignalResult namedtuple.
    We NEVER invent a prob_win for rule-based strategies.

If strategy_type is unknown or missing, the gate uses prob_win = 0.5
(neutral assumption) and logs a warning — it does NOT silently use 1.0.
"""

from logger import get_logger
from research_phase9.cost_engine import CostEngine

logger = get_logger("profitability_gate")

_UNKNOWN_WIN_RATE_FALLBACK = 0.5   # conservative neutral; never optimistic


class ProfitabilityGate:
    def __init__(self, cost_engine=None):
        # Default to standard Binance Taker config for strict execution evaluation
        self.cost_engine = cost_engine or CostEngine.get_binance_taker_config()

    def evaluate_signal(
        self,
        symbol,
        side,
        entry_price,
        sl_price,
        tp_price,
        signal_result,          # accepts SignalResult namedtuple OR raw float (legacy ML)
    ):
        """
        Calculate Expected Net Return and accept/reject the signal.

        Parameters
        ----------
        signal_result : SignalResult namedtuple | float | None
            - If SignalResult: read strategy_type and win_rate_prior / confidence.
            - If float: legacy ML path — treat as calibrated probability.
            - If None: use neutral fallback 0.5 with a warning.

        Returns
        -------
        (bool, dict)  — (accepted, metrics)
        """
        import config

        # ------------------------------------------------------------------
        # 1. Determine prob_win based on strategy type
        # ------------------------------------------------------------------
        strategy_type = _resolve_strategy_type(signal_result)

        if strategy_type == "RULE_BASED":
            # Use the OOS-validated win rate prior embedded in the signal.
            win_rate_prior = getattr(signal_result, "win_rate_prior", None)
            if win_rate_prior is None or not (0.0 < win_rate_prior < 1.0):
                logger.warning(
                    f"[PROFIT GATE] RULE_BASED signal for {symbol} has invalid "
                    f"win_rate_prior={win_rate_prior}. Using neutral fallback {_UNKNOWN_WIN_RATE_FALLBACK}."
                )
                prob_win = _UNKNOWN_WIN_RATE_FALLBACK
            else:
                prob_win = win_rate_prior
            prob_source = f"RULE_BASED/OOS_PRIOR={prob_win:.4f}"

        elif strategy_type == "PROBABILISTIC":
            # ML path: confidence is a calibrated predict_proba output.
            if isinstance(signal_result, (int, float)):
                raw_conf = float(signal_result)
            else:
                raw_conf = getattr(signal_result, "confidence", None)

            if raw_conf is None or not (0.0 <= raw_conf <= 1.0):
                logger.warning(
                    f"[PROFIT GATE] PROBABILISTIC signal for {symbol} has invalid "
                    f"confidence={raw_conf}. Using neutral fallback {_UNKNOWN_WIN_RATE_FALLBACK}."
                )
                prob_win = _UNKNOWN_WIN_RATE_FALLBACK
            else:
                prob_win = raw_conf
            prob_source = f"PROBABILISTIC/ML_CONF={prob_win:.4f}"

        else:
            # Unknown/missing strategy_type — refuse to invent a probability.
            logger.warning(
                f"[PROFIT GATE] Unknown strategy_type='{strategy_type}' for {symbol} {side}. "
                f"Using neutral fallback {_UNKNOWN_WIN_RATE_FALLBACK}. "
                "This should NOT happen in production — fix the strategy module."
            )
            prob_win = _UNKNOWN_WIN_RATE_FALLBACK
            prob_source = f"UNKNOWN/FALLBACK={prob_win:.4f}"

        prob_loss = 1.0 - prob_win

        # ------------------------------------------------------------------
        # 2. Calculate gross moves
        # ------------------------------------------------------------------
        if side in ("BUY", "LONG"):
            reward_pct = (tp_price - entry_price) / entry_price
            risk_pct   = (entry_price - sl_price) / entry_price
            predicted_move = tp_price - entry_price
        else:  # SELL / SHORT
            reward_pct = (entry_price - tp_price) / entry_price
            risk_pct   = (sl_price - entry_price) / entry_price
            predicted_move = entry_price - tp_price

        if reward_pct <= 0 or risk_pct <= 0:
            return False, {
                "decision": "REJECTED",
                "reason": "INVALID_RISK_REWARD",
                "details": f"Reward: {reward_pct:.4f}, Risk: {risk_pct:.4f}",
                "strategy_type": strategy_type,
                "prob_source": prob_source,
                "confidence": prob_win,
            }

        # ------------------------------------------------------------------
        # 3. Expected value calculation — consistent with benchmark
        #    E[gross] = P(win)×reward - P(loss)×risk
        #    E[net]   = E[gross] - total_friction
        # ------------------------------------------------------------------
        expected_gross_return = (prob_win * reward_pct) - (prob_loss * risk_pct)
        total_friction_pct    = self.cost_engine.get_total_friction()
        expected_net_return   = expected_gross_return - total_friction_pct

        min_edge  = getattr(config, "MINIMUM_EXPECTED_EDGE", 0.0005)
        is_accepted = expected_net_return >= min_edge
        reason    = "POSITIVE_EDGE" if is_accepted else "NEGATIVE_EXPECTED_NET_RETURN"

        # ------------------------------------------------------------------
        # 4. Diagnostic logging
        # ------------------------------------------------------------------
        logger.info(f"--- [PROFIT GATE: {symbol} {side}] ---")
        logger.info(f"  Strategy Type   : {strategy_type}")
        logger.info(f"  Prob Source     : {prob_source}")
        logger.info(f"  Prob Win        : {prob_win:.4f} | Prob Loss: {prob_loss:.4f}")
        logger.info(f"  Reward Pct      : {reward_pct:.5f} | Risk Pct: {risk_pct:.5f}")
        logger.info(
            f"  Expected Gross  : ({prob_win:.4f}×{reward_pct:.5f}) - "
            f"({prob_loss:.4f}×{risk_pct:.5f}) = {expected_gross_return:.6f}"
        )
        logger.info(f"  Friction        : {total_friction_pct:.6f}")
        logger.info(f"  Expected Net    : {expected_net_return:.6f} | Threshold: >={min_edge:.5f}")
        logger.info(f"  Decision        : {'ACCEPTED' if is_accepted else 'REJECTED'}")
        logger.info(f"---------------------------------------")

        metrics = {
            "expected_gross_return": expected_gross_return,
            "total_friction":        total_friction_pct,
            "expected_net_return":   expected_net_return,
            "reward_pct":            reward_pct,
            "risk_pct":              risk_pct,
            "prob_win":              prob_win,
            "confidence":            prob_win,     # legacy key kept for log_opportunity compat
            "strategy_type":         strategy_type,
            "prob_source":           prob_source,
            "predicted_move":        predicted_move,
            "holding_horizon":       "4H_RULE_BASED" if strategy_type == "RULE_BASED" else "VARIABLE",
            "decision":              "ACCEPTED" if is_accepted else "REJECTED",
            "reason":                reason,
        }

        if not is_accepted:
            logger.info(
                f"[PROFIT GATE] Rejected {symbol} {side} — "
                f"Expected Net: {expected_net_return:.5f} < {min_edge:.5f}"
            )
            return False, metrics

        logger.info(
            f"[PROFIT GATE] Accepted {symbol} {side} — "
            f"Expected Net: {expected_net_return:.5f} >= {min_edge:.5f}"
        )
        return True, metrics


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _resolve_strategy_type(signal_result):
    """
    Determine the strategy type from the signal.

    SignalResult namedtuple carries strategy_type explicitly.
    Legacy float (ML confidence) maps to PROBABILISTIC.
    None or unknown → "UNKNOWN".
    """
    if signal_result is None:
        return "UNKNOWN"
    if isinstance(signal_result, (int, float)):
        # Legacy ML path — caller passed raw confidence float
        return "PROBABILISTIC"
    strategy_type = getattr(signal_result, "strategy_type", None)
    if strategy_type in ("RULE_BASED", "PROBABILISTIC"):
        return strategy_type
    return "UNKNOWN"
