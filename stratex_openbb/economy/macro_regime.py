"""
stratex_openbb/economy/macro_regime.py — Cross-Asset Macroeconomic Regime Classifier.
Fuses DXY Dollar Index, 10-Year Treasury Yields, VIX Volatility Index, Gold, and Crypto Fear & Greed
into an authoritative MacroRegimeState (RISK_ON, RISK_OFF, NEUTRAL).
"""

import datetime
import logging
from typing import Optional

from stratex_openbb.models import MacroRegimeState
from stratex_openbb.providers.macro_free import FreeMacroProvider
from stratex_openbb.providers.sentiment_free import FreeSentimentProvider

logger = logging.getLogger("openbb.economy")


class MacroRegimeDetector:
    """Fuses cross-asset signals to classify the global macroeconomic regime."""

    def __init__(
        self,
        macro_provider: Optional[FreeMacroProvider] = None,
        sentiment_provider: Optional[FreeSentimentProvider] = None
    ):
        self.macro = macro_provider or FreeMacroProvider()
        self.sentiment = sentiment_provider or FreeSentimentProvider()

    def evaluate_regime(self, force_refresh: bool = False) -> MacroRegimeState:
        """
        Determines current macro market regime:
        - RISK_ON: Dollar soft/dropping, VIX low (<20), Sentiment healthy, tailwind for crypto.
        - RISK_OFF: Dollar surging, VIX high (>22), Fear & Greed low (<35), headwind for crypto.
        - NEUTRAL: Mixed or balanced signals.
        """
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            dxy = self.macro.get_indicator("DXY", force_refresh=force_refresh)
            vix = self.macro.get_indicator("VIX", force_refresh=force_refresh)
            us10y = self.macro.get_indicator("US10Y", force_refresh=force_refresh)
            gold = self.macro.get_indicator("GOLD", force_refresh=force_refresh)
            fng = self.sentiment.get_fear_and_greed(force_refresh=force_refresh)

            dxy_val = dxy.current_value
            vix_val = vix.current_value
            yield_val = us10y.current_value
            gold_val = gold.current_value
            fng_score = fng.score

            score = 0  # positive = risk-on, negative = risk-off

            # 1. VIX Impact
            if vix_val >= 25.0:
                score -= 3
            elif vix_val >= 20.0:
                score -= 1
            elif vix_val <= 15.0:
                score += 2
            elif vix_val <= 18.0:
                score += 1

            # 2. DXY Trend
            if dxy.trend == "BEARISH" or dxy.change_pct < -0.2:
                score += 2  # Weak dollar is bullish for crypto
            elif dxy.trend == "BULLISH" or dxy.change_pct > 0.2:
                score -= 2  # Strong dollar siphons liquidity

            # 3. Fear & Greed Sentiment
            if fng_score >= 60:
                score += 2
            elif fng_score >= 45:
                score += 1
            elif fng_score <= 25:
                score -= 3  # Extreme fear
            elif fng_score <= 40:
                score -= 1

            # 4. 10Y Yield Spike check
            if us10y.change_pct > 2.0:
                score -= 1  # Rapid yield jump tightens financial conditions
            elif us10y.change_pct < -2.0:
                score += 1

            # Determine regime and confidence
            if score >= 3:
                regime = "RISK_ON"
                confidence = min(0.95, 0.60 + (score * 0.07))
                summary = (
                    f"Favorable macro environment: VIX={vix_val:.1f}, DXY={dxy_val:.2f} ({dxy.trend}), "
                    f"Fear&Greed={fng_score} ({fng.classification}). Tailwinds for crypto momentum."
                )
            elif score <= -3:
                regime = "RISK_OFF"
                confidence = min(0.95, 0.60 + (abs(score) * 0.07))
                summary = (
                    f"Defensive macro environment: VIX elevated ({vix_val:.1f}), DXY={dxy_val:.2f} ({dxy.trend}), "
                    f"Fear&Greed={fng_score} ({fng.classification}). High macro headwind."
                )
            else:
                regime = "NEUTRAL"
                confidence = 0.50
                summary = (
                    f"Neutral / balanced macro regime: VIX={vix_val:.1f}, DXY={dxy_val:.2f}, "
                    f"Fear&Greed={fng_score}."
                )

            return MacroRegimeState(
                regime=regime,
                confidence=round(confidence, 2),
                summary=summary,
                dxy_value=dxy_val,
                us10y_yield=yield_val,
                vix_value=vix_val,
                gold_value=gold_val,
                fear_greed_score=fng_score,
                timestamp=now_iso
            )
        except Exception as e:
            logger.warning(f"[OPENBB_MACRO_REGIME] Error evaluating regime: {e}")
            return MacroRegimeState(
                regime="NEUTRAL",
                confidence=0.50,
                summary=f"Macro evaluation degraded ({e}). Operating in neutral fallback mode.",
                dxy_value=None,
                us10y_yield=None,
                vix_value=None,
                gold_value=None,
                fear_greed_score=None,
                timestamp=now_iso
            )
