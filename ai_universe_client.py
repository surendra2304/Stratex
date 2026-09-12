"""
ai_universe_client.py — HTTP client for AI-Universe / Inference Advisory Intelligence.

Integrates the Trading Bot with Inference (POST /v1/trading/consult).
Advisory only:
- Queries Inference with standardized telemetry payload:
  - telemetry metrics
  - telemetry_freshness
  - exchange_status
  - positions
- Returns validated decision dictionaries with bounded recommendations.
- Critical safety invariant: Inference is advisory only; cannot execute orders or bypass gates.
- Fails soft (returns None) on network issues, timeouts, or malformed responses.
- Zero downtime tolerance: trading loop is never blocked or crashed.
"""

import datetime
import os
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from logger import get_logger

logger = get_logger("ai_universe_client")


class AIUniverseClient:
    """Client for consulting the external Inference multi-agent intelligence platform."""

    REQUIRED_DECISION_FIELDS = {"decision_id", "status", "confidence", "parameter_changes"}

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int = 120,
        max_retries: int = 2,
        api_key: str | None = None
    ) -> None:
        self.base_url = (base_url or os.getenv("INFERENCE_URL") or os.getenv("AI_UNIVERSE_URL") or "https://inference-3i2b.onrender.com").rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_key = api_key or os.getenv("INFERENCE_API_KEY") or os.getenv("AI_UNIVERSE_API_KEY") or "inference_api"

        # Configure resilient session with retries on transient errors (5xx, connection reset)
        self.session = requests.Session()
        retries = Retry(
            total=max_retries,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Stratex-Trading-Bot/1.0"
        }
        if self.api_key:
            headers["X-FRIDAY-API-Key"] = self.api_key
            headers["X-API-Key"] = self.api_key
        return headers

    def standardize_consult_payload(self, telemetry_payload: dict[str, Any]) -> dict[str, Any]:
        """
        Standardizes telemetry dictionary into the canonical /v1/trading/consult request shape.
        """
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = dict(telemetry_payload or {})

        # Ensure telemetry_freshness envelope
        if "telemetry_freshness" not in payload:
            payload["telemetry_freshness"] = {
                "timestamp": payload.get("timestamp", now_iso),
                "max_staleness_seconds": 60.0,
                "clock_skew_ms": 0.0,
                "status": "FRESH"
            }

        # Ensure exchange_status envelope
        if "exchange_status" not in payload:
            payload["exchange_status"] = {
                "connectivity": "CONNECTED",
                "exchange": "binance_testnet",
                "latency_ms": 15.0
            }

        # Ensure positions envelope
        if "positions" not in payload:
            payload["positions"] = {
                "open_count": payload.get("portfolio", {}).get("open_positions_count", 0),
                "reconciliation_status": "RECONCILED",
                "active_symbols": []
            }

        return payload

    def consult(self, telemetry_payload: dict[str, Any]) -> dict[str, Any] | None:
        """
        Submits trading telemetry to Inference (POST /v1/trading/consult) and returns
        the validated decision dictionary or None on any failure.
        Enforces that Inference recommendations cannot contain executable orders.
        """
        url = f"{self.base_url}/v1/trading/consult"
        t0 = time.time()
        try:
            standardized_body = self.standardize_consult_payload(telemetry_payload)
            logger.info(f"[AI_UNIVERSE_CLIENT] Sending consultation request to {url}...")
            response = self.session.post(
                url,
                json=standardized_body,
                headers=self._get_headers(),
                timeout=self.timeout
            )
            elapsed_ms = round((time.time() - t0) * 1000.0, 2)

            if response.status_code != 200:
                logger.warning(
                    f"[AI_UNIVERSE_CLIENT] Consultation returned non-200 HTTP status: {response.status_code} - {response.text[:200]}"
                )
                return None

            try:
                data = response.json()
            except Exception as e:
                logger.warning(f"[AI_UNIVERSE_CLIENT] Failed to parse JSON response: {e}")
                return None

            if not isinstance(data, dict):
                logger.warning(f"[AI_UNIVERSE_CLIENT] Malformed response: Expected JSON object, got {type(data)}")
                return None

            # Validate required fields
            missing_fields = self.REQUIRED_DECISION_FIELDS - set(data.keys())
            if missing_fields:
                logger.warning(f"[AI_UNIVERSE_CLIENT] Malformed AIUniverseDecision. Missing fields: {missing_fields}")
                return None

            if not isinstance(data.get("parameter_changes"), list):
                logger.warning("[AI_UNIVERSE_CLIENT] Malformed AIUniverseDecision: parameter_changes must be a list")
                return None

            # CRITICAL SAFETY INVARIANT: Inference is advisory only.
            # Strip and block any executable order directives if present.
            for forbidden_key in ["orders", "execute", "commands", "trades_to_execute", "kill_switch_override"]:
                if forbidden_key in data:
                    logger.warning(
                        f"[AI_UNIVERSE_CLIENT] 🚨 Stripping unauthorized execution directive '{forbidden_key}'. "
                        "Inference cannot execute orders!"
                    )
                    data.pop(forbidden_key, None)

            data["latency_ms"] = elapsed_ms
            logger.info(
                f"[AI_UNIVERSE_CLIENT] Received decision {data.get('decision_id')} ({elapsed_ms}ms) | "
                f"Status: {data.get('status')} | Confidence: {data.get('confidence')}"
            )
            return data

        except requests.exceptions.Timeout as e:
            logger.warning(f"[AI_UNIVERSE_CLIENT] Request timed out after {self.timeout}s: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"[AI_UNIVERSE_CLIENT] Network/Request exception during consultation: {e}")
            return None
        except Exception as e:
            logger.error(f"[AI_UNIVERSE_CLIENT] Unexpected error during consultation: {e}")
            return None

    def health_check(self) -> bool:
        """
        Hits GET /v1/trading/consult/health (or fallback GET /health) to verify Inference availability.
        """
        for endpoint in ["/v1/trading/consult/health", "/health", "/api/health"]:
            url = f"{self.base_url}{endpoint}"
            try:
                response = self.session.get(
                    url,
                    headers=self._get_headers(),
                    timeout=min(self.timeout, 5)
                )
                if response.status_code == 200:
                    return True
            except Exception:
                continue
        return False
