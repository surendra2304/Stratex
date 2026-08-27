"""
ai_universe_client.py — HTTP client for AI-Universe Advisory Intelligence.

Integrates the Trading Bot with AI-Universe (POST /v1/trading/consult).
Advisory only:
- Queries AI-Universe on a schedule or event triggers.
- Returns validated AIUniverseDecision dictionaries.
- Fails soft (returns None) on network issues, timeouts, or malformed responses.
- Zero downtime tolerance: trading loop is never blocked or crashed.
"""

import json
import logging
from typing import Any, Dict, Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from logger import get_logger

logger = get_logger("ai_universe_client")


class AIUniverseClient:
    """Client for consulting the external AI-Universe multi-agent intelligence platform."""

    REQUIRED_DECISION_FIELDS = {"decision_id", "status", "confidence", "parameter_changes"}

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: int = 120,
        max_retries: int = 2,
        api_key: Optional[str] = None
    ) -> None:
        self.base_url = (base_url or "http://localhost:8000").rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_key = api_key

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

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Algorithmic-Trading-Bot/1.0"
        }
        if self.api_key:
            headers["X-FRIDAY-API-Key"] = self.api_key
        return headers

    def consult(self, telemetry_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Submits trading telemetry to AI-Universe (POST /v1/trading/consult) and returns
        the validated decision dictionary or None on any failure.
        """
        url = f"{self.base_url}/v1/trading/consult"
        try:
            logger.info(f"[AI_UNIVERSE_CLIENT] Sending consultation request to {url}...")
            response = self.session.post(
                url,
                json=telemetry_payload or {},
                headers=self._get_headers(),
                timeout=self.timeout
            )

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
                logger.warning(f"[AI_UNIVERSE_CLIENT] Malformed AIUniverseDecision: parameter_changes must be a list")
                return None

            logger.info(
                f"[AI_UNIVERSE_CLIENT] Received decision {data.get('decision_id')} | Status: {data.get('status')} | Confidence: {data.get('confidence')}"
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
        Hits GET /v1/trading/consult/health (or fallback GET /health) to verify AI-Universe availability.
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
