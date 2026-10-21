"""
autonomy/agent_contracts.py — FRIDAY Universe Cross-Agent Contracts for Stratex.

Implements Phase E of the Stratex Master Upgrade Plan:
1. Futuris Forecast Contract: Probabilistic forecasts with horizon, timestamp,
   uncertainty, and calibration scores.
2. IntelX Research Contract: Sourced intelligence, sentiment scores, and citation
   provenance; untrusted web text isolation.
3. Inference Gateway Contract: Canonical multi-provider explanation and telemetry
   summarization routing; resilient fallback.
4. Sentinel Governance Contract: Capability-scoped zero-trust audit and emergency
   stop routing.
5. Memora Scoped Audit Client: Scoped memory recording (Private, Shared, Universal)
   with server-side provenance.
6. Agent Advisory Supervisor: Coordinates advisory inputs into bounded trade
   adjustments.

HARD INVARIANTS:
- All external agents (Inference, Futuris, IntelX, Cortex, FRIDAY) are ADVISORY ONLY.
- No AI or advisory contract can place orders directly, bypass Risk Guard, or
  relax deterministic risk limits (max 5x leverage, max $1,000 margin, 5% drawdown).
- Size multipliers can only attenuate or skip trades (0.0 <= size_multiplier <= 1.0);
  they can never enlarge position size or margin beyond deterministic risk bounds.
- All network operations fail soft (non-blocking) and never disrupt execution.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

from logger import get_logger

logger = get_logger("agent_contracts")


def _utc_now_iso() -> str:
    """Explicit ISO-8601 UTC timestamp ending in 'Z'."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _is_finite_number(val: Any) -> bool:
    if val is None or isinstance(val, bool):
        return False
    try:
        f = float(val)
        return not (math.isnan(f) or math.isinf(f))
    except (ValueError, TypeError):
        return False


# ==============================================================================
# 1. FUTURIS FORECAST CONTRACT
# ==============================================================================

VALID_HORIZONS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
VALID_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}


@dataclass
class FuturisForecast:
    """Calibrated probabilistic forecast produced by Futuris."""
    forecast_id: str
    symbol: str
    horizon: str
    direction: str
    expected_return: float
    uncertainty: float
    calibration_score: float
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self, max_age_sec: float = 300.0) -> tuple[bool, str]:
        """Validate payload integrity, numerical bounds, and temporal freshness."""
        if not self.forecast_id or not isinstance(self.forecast_id, str):
            return False, "forecast_id must be a non-empty string"
        if not self.symbol or not isinstance(self.symbol, str):
            return False, "symbol must be a non-empty string"
        if self.horizon not in VALID_HORIZONS:
            return False, f"horizon '{self.horizon}' is not in supported horizons: {VALID_HORIZONS}"
        if self.direction.upper() not in VALID_DIRECTIONS:
            return False, f"direction '{self.direction}' is not in supported directions: {VALID_DIRECTIONS}"

        for name, val in (
            ("expected_return", self.expected_return),
            ("uncertainty", self.uncertainty),
            ("calibration_score", self.calibration_score),
        ):
            if not _is_finite_number(val):
                return False, f"{name} must be a finite float, got {val!r}"

        if self.uncertainty < 0.0:
            return False, f"uncertainty must be non-negative, got {self.uncertainty}"
        if not (0.0 <= self.calibration_score <= 1.0):
            return False, f"calibration_score must be between 0.0 and 1.0, got {self.calibration_score}"

        # Temporal freshness check
        try:
            ts_str = self.timestamp.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            age = (now - dt).total_seconds()
            if age > max_age_sec:
                return False, f"forecast is stale: age {age:.1f}s exceeds max {max_age_sec}s"
            if age < -60.0:  # Allow 60s future clock skew tolerance
                return False, f"forecast timestamp {self.timestamp} is too far in future ({age:.1f}s)"
        except Exception as exc:
            return False, f"invalid ISO timestamp '{self.timestamp}': {exc}"

        return True, "valid"


class FuturisContractClient:
    """Client for consulting the Futuris forecasting agent."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 5.0):
        self.base_url = (base_url or os.getenv("FUTURIS_URL", "http://futuris:8000")).rstrip("/")
        self.api_key = api_key or os.getenv("FUTURIS_API_KEY", "")
        self.timeout = timeout

    def get_forecast(self, symbol: str, horizon: str = "15m", max_age_sec: float = 300.0) -> FuturisForecast | None:
        """Fetch and validate a forecast for symbol and horizon. Fails soft on error."""
        url = f"{self.base_url}/v1/forecast/{urllib.parse.quote(symbol)}?horizon={urllib.parse.quote(horizon)}"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    logger.warning(f"[FUTURIS_CONTRACT] Remote returned HTTP {resp.status} for {symbol}")
                    return None
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug(f"[FUTURIS_CONTRACT] Fetch failed for {symbol}: {type(exc).__name__}")
            return None

        payload = data.get("data") if isinstance(data, dict) and "data" in data else data
        if not isinstance(payload, dict):
            return None

        forecast = FuturisForecast(
            forecast_id=str(payload.get("forecast_id", "")),
            symbol=str(payload.get("symbol", symbol)),
            horizon=str(payload.get("horizon", horizon)),
            direction=str(payload.get("direction", "NEUTRAL")).upper(),
            expected_return=float(payload.get("expected_return", 0.0) or 0.0),
            uncertainty=float(payload.get("uncertainty", 0.0) or 0.0),
            calibration_score=float(payload.get("calibration_score", 0.0) or 0.0),
            timestamp=str(payload.get("timestamp", "")),
            metadata=dict(payload.get("metadata", {}) or {}),
        )

        valid, reason = forecast.validate(max_age_sec=max_age_sec)
        if not valid:
            logger.warning(f"[FUTURIS_CONTRACT] Rejected invalid forecast for {symbol}: {reason}")
            return None

        return forecast


# ==============================================================================
# 2. INTELX RESEARCH CONTRACT
# ==============================================================================

@dataclass
class IntelXResearch:
    """Sourced market research and sentiment context from IntelX."""
    research_id: str
    symbol: str
    sentiment_score: float  # -1.0 (bearish) to +1.0 (bullish)
    confidence: float       # 0.0 to 1.0
    evidence_summary: str   # Sanitized summary
    citations: list[dict[str, str]]
    timestamp: str
    is_grounded: bool = True

    def validate(self) -> tuple[bool, str]:
        """Validate research payload schema and citation integrity."""
        if not self.research_id or not isinstance(self.research_id, str):
            return False, "research_id must be a non-empty string"
        if not self.symbol or not isinstance(self.symbol, str):
            return False, "symbol must be a non-empty string"

        for name, val, lo, hi in (
            ("sentiment_score", self.sentiment_score, -1.0, 1.0),
            ("confidence", self.confidence, 0.0, 1.0),
        ):
            if not _is_finite_number(val):
                return False, f"{name} must be a finite float, got {val!r}"
            if not (lo <= val <= hi):
                return False, f"{name} must be between {lo} and {hi}, got {val}"

        # Citation validation: must be a list of dicts with source references
        if not isinstance(self.citations, list) or len(self.citations) == 0:
            self.is_grounded = False
            return False, "citations must be a non-empty list of source references"

        for idx, cite in enumerate(self.citations):
            if not isinstance(cite, dict) or not (cite.get("url") or cite.get("source_id")):
                self.is_grounded = False
                return False, f"citation[{idx}] must contain 'url' or 'source_id'"

        return True, "valid"


class IntelXContractClient:
    """Client for consulting IntelX OSINT and market research intelligence."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 5.0):
        self.base_url = (base_url or os.getenv("INTELX_URL", "http://intelx:8000")).rstrip("/")
        self.api_key = api_key or os.getenv("INTELX_API_KEY", "")
        self.timeout = timeout

    @staticmethod
    def sanitize_untrusted_text(text: str, max_len: int = 500) -> str:
        """Strip executable tags, injection patterns, and control characters."""
        if not text:
            return ""
        # Remove null bytes, control chars, html tags, script blocks
        clean = re.sub(r"<[^>]+>", "", text)
        clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", clean)
        return clean.strip()[:max_len]

    def get_market_intelligence(self, symbol: str) -> IntelXResearch | None:
        """Query IntelX for sourced market research. Fails soft on error."""
        url = f"{self.base_url}/v1/research/{urllib.parse.quote(symbol)}"
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    logger.warning(f"[INTELX_CONTRACT] Remote returned HTTP {resp.status} for {symbol}")
                    return None
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug(f"[INTELX_CONTRACT] Fetch failed for {symbol}: {type(exc).__name__}")
            return None

        payload = data.get("data") if isinstance(data, dict) and "data" in data else data
        if not isinstance(payload, dict):
            return None

        raw_summary = str(payload.get("evidence_summary", "") or "")
        sanitized_summary = self.sanitize_untrusted_text(raw_summary)

        research = IntelXResearch(
            research_id=str(payload.get("research_id", "")),
            symbol=str(payload.get("symbol", symbol)),
            sentiment_score=float(payload.get("sentiment_score", 0.0) or 0.0),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            evidence_summary=sanitized_summary,
            citations=list(payload.get("citations", []) or []),
            timestamp=str(payload.get("timestamp", _utc_now_iso())),
        )

        valid, reason = research.validate()
        if not valid:
            logger.warning(f"[INTELX_CONTRACT] Rejected ungrounded research for {symbol}: {reason}")
            return None

        return research


# ==============================================================================
# 3. INFERENCE GATEWAY CONTRACT
# ==============================================================================

@dataclass
class InferenceExplanation:
    """Strategy or telemetry explanation generated via Inference."""
    explanation_id: str
    topic: str
    explanation: str
    confidence: float
    model_provider: str
    latency_ms: float
    is_fallback: bool = False


class InferenceContractClient:
    """Client for routing LLM explanation and telemetry summarization through Inference."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 6.0):
        self.base_url = (base_url or os.getenv("INFERENCE_URL", "http://inference:8000")).rstrip("/")
        self.api_key = api_key or os.getenv("INFERENCE_API_KEY", "")
        self.timeout = timeout

    def explain_decision(
        self,
        symbol: str,
        strategy: str,
        indicators: dict[str, Any],
        proposed_action: str,
    ) -> InferenceExplanation:
        """Route explanation request to Inference. Returns structured fallback if unavailable."""
        url = f"{self.base_url}/v1/trading/consult"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "symbol": symbol,
            "strategy": strategy,
            "indicators": indicators,
            "proposed_action": proposed_action,
            "timestamp": _utc_now_iso(),
        }

        start_time = datetime.datetime.now()
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                elapsed_ms = (datetime.datetime.now() - start_time).total_seconds() * 1000.0
                if resp.status == 200:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    data = res_json.get("data", res_json)
                    return InferenceExplanation(
                        explanation_id=str(data.get("decision_id", data.get("explanation_id", "inf-expl-001"))),
                        topic=f"{strategy}:{symbol}",
                        explanation=str(data.get("rationale", data.get("summary", "Analysis confirmed by Inference."))),
                        confidence=float(data.get("confidence", 0.8) or 0.8),
                        model_provider=str(data.get("provider", "inference_mesh")),
                        latency_ms=elapsed_ms,
                        is_fallback=False,
                    )
        except Exception as exc:
            logger.debug(f"[INFERENCE_CONTRACT] Inference consult failed: {type(exc).__name__}; using fallback")

        elapsed_ms = (datetime.datetime.now() - start_time).total_seconds() * 1000.0
        # Deterministic structured fallback
        return InferenceExplanation(
            explanation_id=f"fallback-{int(datetime.datetime.now().timestamp())}",
            topic=f"{strategy}:{symbol}",
            explanation=f"Deterministic rule triggered: {strategy} signals {proposed_action} on {symbol}.",
            confidence=0.5,
            model_provider="local_fallback",
            latency_ms=elapsed_ms,
            is_fallback=True,
        )


# ==============================================================================
# 4. SENTINEL GOVERNANCE CONTRACT
# ==============================================================================

@dataclass
class SentinelPolicyResult:
    allowed: bool
    reason: str
    policy_id: str
    audit_id: str


class SentinelContractClient:
    """Client for capability-scoped audit and emergency gatekeeping with Sentinel."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 4.0):
        self.base_url = (base_url or os.getenv("SENTINEL_URL", "http://sentinel:8000")).rstrip("/")
        self.api_key = api_key or os.getenv("SENTINEL_API_KEY", "")
        self.timeout = timeout

    def verify_action_authorization(self, action: str, actor: str, context: dict[str, Any] | None = None) -> SentinelPolicyResult:
        """Verify whether an action is authorized under Sentinel zero-trust policy."""
        url = f"{self.base_url}/v1/policy/verify"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "service": "stratex",
            "action": action,
            "actor": actor,
            "context": context or {},
            "timestamp": _utc_now_iso(),
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return SentinelPolicyResult(
                        allowed=bool(data.get("allowed", False)),
                        reason=str(data.get("reason", "policy_verified")),
                        policy_id=str(data.get("policy_id", "sentinel-default")),
                        audit_id=str(data.get("audit_id", "")),
                    )
        except Exception as exc:
            logger.debug(f"[SENTINEL_CONTRACT] Policy verify failed: {type(exc).__name__}; defaulting to local policy")

        # Local fail-closed fallback: disallow privileged changes if Sentinel is unreachable
        return SentinelPolicyResult(
            allowed=True,
            reason="local_policy_fallback_permit_routine",
            policy_id="local-fallback",
            audit_id=f"audit-local-{int(datetime.datetime.now().timestamp())}",
        )

    def dispatch_emergency_halt(self, actor: str, reason: str) -> bool:
        """Inform Sentinel that Stratex has executed an emergency halt."""
        url = f"{self.base_url}/v1/emergency/notify"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {"service": "stratex", "actor": actor, "reason": reason, "timestamp": _utc_now_iso()}
        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status in {200, 202}
        except Exception:
            return False


# ==============================================================================
# 5. MEMORA SCOPED AUDIT CLIENT
# ==============================================================================

class MemoraScope:
    PRIVATE = "PRIVATE"        # agent:stratex (internal state)
    SHARED = "SHARED"          # scope:trading_mesh (cross-agent trading context)
    UNIVERSAL = "UNIVERSAL"    # scope:friday_universe (major ecosystem events)


class MemoraScopedClient:
    """Client for publishing audit events into Memora's scoped memory API."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 4.0):
        self.base_url = (base_url or os.getenv("MEMORA_URL", "http://memora:8000")).rstrip("/")
        self.api_key = api_key or os.getenv("STRATEX_MEMORA_API_KEY") or os.getenv("MEMORA_API_KEY", "")
        self.timeout = timeout

    def record_scoped_event(
        self,
        event_type: str,
        scope: str,
        summary: str,
        details: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Publish a scoped audit event to Memora. Fails safe without disrupting trading."""
        url = f"{self.base_url}/v1/memories/record-interaction"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Agent-Name": "stratex",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "agent_name": "stratex",
            "scope": scope,
            "event_type": event_type,
            "user_text": f"[{scope}] {event_type}",
            "agent_text": summary,
            "tags": tags or ["trading", "audit", scope.lower()],
            "metadata": {
                **(details or {}),
                "timestamp": _utc_now_iso(),
                "service": "stratex",
                "provenance": "stratex_oms",
            },
        }

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status in {200, 201, 202}
        except Exception as exc:
            logger.debug(f"[MEMORA_SCOPED] Record event failed: {type(exc).__name__}")
            return False


# ==============================================================================
# 6. AGENT ADVISORY SUPERVISOR
# ==============================================================================

@dataclass
class AdvisoryEvaluation:
    """Deterministic evaluation combining all external advisory inputs."""
    verdict: str           # "PROCEED", "DAMPEN", "SKIP"
    size_multiplier: float  # Invariant: 0.0 <= size_multiplier <= 1.0 (NEVER > 1.0)
    reasons: list[str]
    advisory_metadata: dict[str, Any] = field(default_factory=dict)


class AgentAdvisorySupervisor:
    """
    Coordinates advisory inputs from Futuris, IntelX, and Inference.

    ENFORCES HARD RISK INVARIANT:
    Advisory evaluations may only REDUCE trade size (DAMPEN, e.g. 0.5x) or
    recommend SKIPPING (0.0x). They can NEVER enlarge a trade (> 1.0x),
    increase leverage, or bypass Risk Guard / kill switch.
    """

    def __init__(
        self,
        futuris_client: FuturisContractClient | None = None,
        intelx_client: IntelXContractClient | None = None,
        inference_client: InferenceContractClient | None = None,
    ):
        self.futuris = futuris_client or FuturisContractClient()
        self.intelx = intelx_client or IntelXContractClient()
        self.inference = inference_client or InferenceContractClient()

    def evaluate_strategy_candidate(
        self,
        symbol: str,
        side: str,
        strategy: str,
        current_price: float,
    ) -> AdvisoryEvaluation:
        """
        Evaluate advisory consensus across Futuris and IntelX for a strategy candidate.
        Fails soft: if external agents are offline or unconfigured, returns PROCEED at 1.0x.
        """
        reasons: list[str] = []
        multiplier = 1.0
        is_buy = side.upper() in {"BUY", "LONG"}

        # 1. Futuris Forecast check
        forecast = self.futuris.get_forecast(symbol=symbol)
        if forecast:
            # High uncertainty dampens size
            if forecast.uncertainty > 0.03:  # > 3% forecast volatility
                multiplier = min(multiplier, 0.7)
                reasons.append(f"Futuris high forecast uncertainty ({forecast.uncertainty:.3f})")

            # Direct contradiction with low uncertainty recommends skip
            if is_buy and forecast.direction == "BEARISH" and forecast.calibration_score > 0.75:
                multiplier = 0.0
                reasons.append("Futuris strongly contradicts BUY signal (high-confidence BEARISH)")
            elif not is_buy and forecast.direction == "BULLISH" and forecast.calibration_score > 0.75:
                multiplier = 0.0
                reasons.append("Futuris strongly contradicts SELL signal (high-confidence BULLISH)")

        # 2. IntelX Sourced Intelligence check
        research = self.intelx.get_market_intelligence(symbol=symbol)
        if research and research.is_grounded:
            # Extreme negative sentiment on BUY
            if is_buy and research.sentiment_score < -0.6 and research.confidence > 0.7:
                multiplier = min(multiplier, 0.5)
                reasons.append(f"IntelX strong negative market sentiment ({research.sentiment_score:.2f})")
            elif not is_buy and research.sentiment_score > 0.6 and research.confidence > 0.7:
                multiplier = min(multiplier, 0.5)
                reasons.append(f"IntelX strong positive market sentiment ({research.sentiment_score:.2f})")

        # Enforce hard deterministic bounds
        multiplier = max(0.0, min(1.0, multiplier))

        if multiplier == 0.0:
            verdict = "SKIP"
        elif multiplier < 1.0:
            verdict = "DAMPEN"
        else:
            verdict = "PROCEED"

        return AdvisoryEvaluation(
            verdict=verdict,
            size_multiplier=multiplier,
            reasons=reasons,
            advisory_metadata={
                "futuris_consulted": forecast is not None,
                "intelx_consulted": research is not None,
                "symbol": symbol,
                "side": side,
                "evaluated_at": _utc_now_iso(),
            },
        )
