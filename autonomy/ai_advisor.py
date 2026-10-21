"""
autonomy/ai_advisor.py — AI Strategic Intelligence Core for Stratex.

Integrates Google Gemini 3.5 Flash and FRIDAY Universe Inference Mesh to power:
1. Real-time quantitative trade evaluation & thesis generation across all market assets.
2. Dynamic, conversational 2-way AI chatbot on Telegram.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any

import requests

from logger import get_logger

logger = get_logger("ai_advisor")

GEMINI_PRIMARY_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
]


@dataclass(frozen=True)
class AITradeVerdict:
    """AI evaluation of a proposed quantitative setup."""

    approved: bool
    verdict: str  # "APPROVED", "DAMPENED", "REJECTED"
    confidence: float
    thesis: str
    risk_factors: list[str] = field(default_factory=list)
    model_used: str = "gemini-3.5-flash"


class StratexAIAdvisor:
    """High-intelligence AI reasoning engine using Gemini & Inference Mesh."""

    def __init__(
        self,
        gemini_api_key: str | None = None,
        inference_url: str | None = None,
        inference_key: str | None = None,
        openrouter_key: str | None = None,
    ):
        # 1. Authoritative Gemini API Key from Stratex environment
        self.gemini_key = (gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()

        # 2. FRIDAY Universe Central Inference Service
        self.inference_url = (inference_url or os.getenv("INFERENCE_URL", "https://friday-universe.duckdns.org/inference")).rstrip("/")
        self.inference_key = inference_key or os.getenv("INFERENCE_API_KEY", "")

        # 3. OpenRouter fallback key
        self.openrouter_key = (openrouter_key or os.getenv("FRIDAY_OPENROUTER_API_KEY", "")).strip()

    def _call_openrouter(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str | None:
        """Call OpenRouter fallback if configured."""
        if not self.openrouter_key:
            return None
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openrouter_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "google/gemini-2.5-flash",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return str(choices[0].get("message", {}).get("content", "")).strip()
        except Exception as exc:
            logger.debug(f"[AI_ADVISOR] OpenRouter fallback failed: {exc}")
        return None

    def _call_inference_mesh(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str | None:
        """Call central FRIDAY Universe Inference service if active."""
        if not self.inference_url:
            return None
        url = f"{self.inference_url}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.inference_key:
            headers["Authorization"] = f"Bearer {self.inference_key}"

        payload = {
            "model": "gemini-3.5-flash",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return str(choices[0].get("message", {}).get("content", "")).strip()
        except Exception as exc:
            logger.debug(f"[AI_ADVISOR] Inference mesh call bypassed: {type(exc).__name__}")
        return None

    def _call_gemini_direct(self, system_prompt: str, user_prompt: str) -> str | None:
        """Execute direct Gemini SDK call using authorized GEMINI_API_KEY."""
        if not self.gemini_key:
            return None
        try:
            from google import genai
            client = genai.Client(api_key=self.gemini_key)
            combined_prompt = f"{system_prompt}\n\nUser: {user_prompt}"

            for model in GEMINI_PRIMARY_MODELS:
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=combined_prompt,
                    )
                    if response and response.text:
                        return response.text.strip()
                except Exception as inner_exc:
                    logger.debug(f"[AI_ADVISOR] Gemini model {model} attempt: {inner_exc}")
        except Exception as exc:
            logger.warning(f"[AI_ADVISOR] Direct Gemini invocation failed: {exc}")
        return None

    def _call_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str | None:
        """Route to central Inference service or direct Gemini."""
        # Route 1: Direct Gemini API Key
        result = self._call_gemini_direct(system_prompt, user_prompt)
        if result:
            return result

        # Route 2: Inference Mesh
        result = self._call_inference_mesh(system_prompt, user_prompt, max_tokens=max_tokens)
        if result:
            return result

        # Route 3: OpenRouter fallback if key provided
        result = self._call_openrouter(system_prompt, user_prompt, max_tokens=max_tokens)
        if result:
            return result

        return None

    @staticmethod
    def _fail_closed_verdict(symbol: str, reason: str) -> AITradeVerdict:
        """Return a non-approving verdict when AI evidence is absent or invalid."""
        return AITradeVerdict(
            approved=False,
            verdict="REJECTED",
            confidence=0.0,
            thesis=f"AI approval withheld for {symbol}: {reason}",
            risk_factors=[
                reason,
                "Trade approval requires a valid, schema-compliant AI verdict.",
            ],
            model_used="unavailable",
        )

    def evaluate_trade_candidate(
        self,
        symbol: str,
        timeframe: str,
        action: str,
        current_price: float,
        stop_loss: float,
        take_profit: float,
        risk_reward_ratio: float,
        indicators: dict[str, Any],
    ) -> AITradeVerdict:
        """Subject trade proposal to LLM hedge-fund risk critique."""
        system_prompt = (
            "You are Stratex AI, the elite Strategic Quantitative Planner of the FRIDAY Universe. "
            "You evaluate multi-asset market opportunities (Forex, Metals, Commodities, Indices, Crypto CFDs). "
            "Respond ONLY in valid JSON format with keys: "
            "'approved' (boolean), 'verdict' ('APPROVED'|'DAMPENED'|'REJECTED'), "
            "'confidence' (float 0.0-1.0), 'thesis' (concise string explaining rationale), "
            "'risk_factors' (list of strings)."
        )

        user_prompt = (
            f"Asset: {symbol}\n"
            f"Timeframe: {timeframe}\n"
            f"Action: {action}\n"
            f"Current Price: {current_price}\n"
            f"Stop Loss: {stop_loss}\n"
            f"Take Profit: {take_profit}\n"
            f"Reward/Risk Ratio: {risk_reward_ratio}\n"
            f"Technical Context: {json.dumps(indicators)}\n\n"
            "Evaluate if this setup provides genuine statistical expectancy. Output JSON only."
        )

        try:
            raw = self._call_llm(system_prompt, user_prompt, max_tokens=350)
        except Exception as exc:
            logger.warning(
                "[AI_ADVISOR] Provider routing failed; trade approval withheld (%s)",
                type(exc).__name__,
            )
            return self._fail_closed_verdict(symbol, "AI provider call failed.")

        if not isinstance(raw, str) or not raw.strip():
            return self._fail_closed_verdict(symbol, "No AI provider returned a response.")

        try:
            cleaned = raw.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            parsed = json.loads(cleaned.strip())
            if not isinstance(parsed, dict):
                raise ValueError("response must be a JSON object")

            required = {"approved", "verdict", "confidence", "thesis", "risk_factors"}
            missing = required.difference(parsed)
            if missing:
                raise ValueError("response is missing required fields")

            approved = parsed["approved"]
            if type(approved) is not bool:
                raise ValueError("'approved' must be a boolean")

            verdict_value = parsed["verdict"]
            if not isinstance(verdict_value, str):
                raise ValueError("'verdict' must be a string")
            verdict = verdict_value.strip().upper()
            if verdict not in {"APPROVED", "DAMPENED", "REJECTED"}:
                raise ValueError("'verdict' is not a supported decision")

            confidence_value = parsed["confidence"]
            if isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
                raise ValueError("'confidence' must be a number")
            confidence = float(confidence_value)
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("'confidence' must be finite and between 0 and 1")

            thesis_value = parsed["thesis"]
            if not isinstance(thesis_value, str) or not thesis_value.strip() or len(thesis_value) > 2000:
                raise ValueError("'thesis' must be a non-empty string of at most 2000 characters")

            factors_value = parsed["risk_factors"]
            if not isinstance(factors_value, list) or len(factors_value) > 20:
                raise ValueError("'risk_factors' must be a list of at most 20 strings")
            if any(
                not isinstance(factor, str) or not factor.strip() or len(factor) > 500
                for factor in factors_value
            ):
                raise ValueError("each risk factor must be a non-empty string of at most 500 characters")

            consistent = (
                (verdict == "REJECTED" and approved is False)
                or (verdict in {"APPROVED", "DAMPENED"} and approved is True)
            )
            if not consistent:
                raise ValueError("'approved' and 'verdict' express conflicting decisions")

            return AITradeVerdict(
                approved=approved,
                verdict=verdict,
                confidence=confidence,
                thesis=thesis_value.strip(),
                risk_factors=[factor.strip() for factor in factors_value],
                model_used="gemini-3.5-flash",
            )
        except (TypeError, ValueError, OverflowError) as exc:
            logger.warning(
                "[AI_ADVISOR] Invalid provider verdict; trade approval withheld (%s)",
                type(exc).__name__,
            )
            return self._fail_closed_verdict(
                symbol,
                "AI provider returned invalid or schema-inconsistent output.",
            )

    def generate_chat_response(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Generate conversational, genius-level responses for Telegram chat."""
        system_prompt = (
            "You are Stratex (Agent 4 of the FRIDAY Universe), the Strategic Quantitative Planner and Autonomous Trading Operator.\n"
            "You speak with confidence, precision, intelligence, and a sharp hedge-fund quant demeanor.\n"
            "Use clean, compact bullet points and relevant emojis (📊, 🟢, 🔴, ⚡, 🛡️, 💰).\n"
            "You are actively scanning ALL available market assets (Forex majors, crosses, Gold, Silver, Oil, Indices, and Crypto) "
            "across multiple timeframes on MetaTrader 5 demo execution.\n"
            "Never fabricate fake live money claims. Be direct, helpful, and transparent."
        )

        ctx_str = f"Live Context: {json.dumps(context or {})}"
        full_user_prompt = f"{ctx_str}\n\nUser Message: {user_message}"

        llm_reply = self._call_llm(system_prompt, full_user_prompt, max_tokens=500)
        if llm_reply:
            return llm_reply

        # Fallback if no LLM responded
        return (
            f"🧠 <b>Stratex Quantitative Intelligence</b>\n\n"
            f"Received: <i>\"{user_message}\"</i>\n\n"
            "• <b>Status</b>: Active & Monitoring MetaTrader 5 markets.\n"
            "• <b>Universe</b>: Universal Asset Coverage (Forex Majors/Crosses, Gold, Silver, Indices, Commodities).\n"
            "• <b>Regime</b>: Volatility-normalized ATR Chandelier + Kalman state-space tracking.\n"
            "• <b>Execution Guard</b>: 2% hard stop-loss risk cap with Chandelier Exits.\n"
            "• <b>AI Mesh</b>: Futuris forecasts & Sentinel zero-trust risk active."
        )


_ADVISOR_INSTANCE: StratexAIAdvisor | None = None


def get_ai_advisor() -> StratexAIAdvisor:
    global _ADVISOR_INSTANCE
    if _ADVISOR_INSTANCE is None:
        _ADVISOR_INSTANCE = StratexAIAdvisor()
    return _ADVISOR_INSTANCE
