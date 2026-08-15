from logger import get_logger
from research_phase9.cost_engine import CostEngine

logger = get_logger("profitability_gate")

class ProfitabilityGate:
    def __init__(self, cost_engine=None):
        # Default to standard Binance Taker config for strict execution evaluation
        self.cost_engine = cost_engine or CostEngine.get_binance_taker_config()

    def evaluate_signal(self, symbol, side, entry_price, sl_price, tp_price, confidence):
        """
        Calculates Expected Net Return based on structural probabilities and costs.
        Rejects the signal if the expected net return is negative.
        """
        import config
        # 1. Calculate Gross Moves
        if side == "BUY" or side == "LONG":
            reward_pct = (tp_price - entry_price) / entry_price
            risk_pct = (entry_price - sl_price) / entry_price
            predicted_move = (tp_price - entry_price)
        else: # SELL
            reward_pct = (entry_price - tp_price) / entry_price
            risk_pct = (sl_price - entry_price) / entry_price
            predicted_move = (entry_price - tp_price)

        # Basic input validation
        if reward_pct <= 0 or risk_pct <= 0:
            return False, {
                "decision": "REJECTED",
                "reason": "INVALID_RISK_REWARD",
                "details": f"Reward: {reward_pct:.4f}, Risk: {risk_pct:.4f}"
            }

        # 2. Estimate structural probability
        # If the strategy provides confidence (like ML), use it.
        # If confidence is missing or not a probability, assume neutral 0.5 as base structure.
        prob_win = confidence if (confidence is not None and 0.0 <= confidence <= 1.0) else 0.5
        prob_loss = 1.0 - prob_win

        # 3. Calculate Expected Gross Return
        expected_gross_return = (prob_win * reward_pct) - (prob_loss * risk_pct)

        # 4. Calculate total friction (Round-trip)
        total_friction_pct = self.cost_engine.get_total_friction()

        # 5. Calculate Expected Net Return
        # We apply the total friction against every trade, win or lose.
        expected_net_return = expected_gross_return - total_friction_pct

        # Phase 5: Expected Net Edge Threshold
        min_edge = getattr(config, "MINIMUM_EXPECTED_EDGE", 0.0005)
        is_accepted = expected_net_return >= min_edge
        reason = "PASSED" if is_accepted else "NEGATIVE_EXPECTED_NET_RETURN"
        
        # DIAGNOSTIC: Log profitability calculations directly to system logger for transparency
        logger.info(f"--- [PROFIT GATE: {symbol} {side}] ---")
        logger.info(f"  Confidence (Prob Win): {prob_win:.4f} | Prob Loss: {prob_loss:.4f}")
        logger.info(f"  Reward Pct: {reward_pct:.5f} | Risk Pct: {risk_pct:.5f}")
        logger.info(f"  Expected Gross Return: ({prob_win:.4f} * {reward_pct:.5f}) - ({prob_loss:.4f} * {risk_pct:.5f}) = {expected_gross_return:.6f}")
        logger.info(f"  Total Friction (Fees+Slippage): {total_friction_pct:.6f}")
        logger.info(f"  Expected Net Edge: {expected_gross_return:.6f} - {total_friction_pct:.6f} = {expected_net_return:.6f}")
        logger.info(f"  Threshold: >= {min_edge:.5f} | Result: {'ACCEPTED' if is_accepted else 'REJECTED'}")
        logger.info(f"---------------------------------------")
        # 6. Evaluation Output
        metrics = {
            "expected_gross_return": expected_gross_return,
            "total_friction": total_friction_pct,
            "expected_net_return": expected_net_return,
            "reward_pct": reward_pct,
            "risk_pct": risk_pct,
            "confidence": prob_win,
            "predicted_move": predicted_move,
            "holding_horizon": "4_HOURS_EST", # Mocked for Phase 5 reporting
            "decision": "ACCEPTED" if is_accepted else "REJECTED",
            "reason": "POSITIVE_EDGE" if is_accepted else "NEGATIVE_EDGE",
        }

        if metrics["decision"] == "REJECTED":
            logger.info(f"[PROFIT GATE] Rejected {symbol} {side} - Expected Net Return: {expected_net_return:.5f} <= 0")
            return False, metrics
            
        logger.info(f"[PROFIT GATE] Accepted {symbol} {side} - Expected Net Return: {expected_net_return:.5f} > 0")
        return True, metrics
