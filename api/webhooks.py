"""
api/webhooks.py — Reliable Webhook Event Emitter with HMAC Signatures & Retries.

Emits:
- trade.opened, trade.closed
- risk.threshold_warning, risk.limit_hit
- advisory.recommendation, advisory.applied
- system.degraded, system.recovered
"""

import os
import json
import time
import requests
import threading
from typing import Dict, List, Optional, Any
from security_hardening import sign_audit_record
from logger import get_logger

logger = get_logger("webhooks")


class EcosystemWebhookEmitter:
    """
    Dispatches outbound webhook events with HMAC SHA-256 signatures and exponential retry backoff.
    """

    def __init__(self):
        urls_str = os.getenv("WEBHOOK_URLS", "")
        self.webhook_urls = [u.strip() for u in urls_str.split(",") if u.strip()]
        self.dead_letter_queue: List[Dict[str, Any]] = []

    def emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Asynchronously dispatches webhook payload to registered endpoints."""
        payload = {
            "event": event_type,
            "timestamp": time.time(),
            "source": "trading_bot",
            "data": data
        }
        sig = sign_audit_record(payload)
        payload["signature"] = sig

        if not self.webhook_urls:
            return

        thread = threading.Thread(target=self._dispatch_with_retry, args=(payload,), daemon=True)
        thread.start()

    def _dispatch_with_retry(self, payload: Dict[str, Any], max_retries: int = 3) -> None:
        headers = {
            "Content-Type": "application/json",
            "X-Bot-Signature": payload.get("signature", "")
        }

        for url in self.webhook_urls:
            success = False
            for attempt in range(max_retries):
                try:
                    resp = requests.post(url, json=payload, headers=headers, timeout=4)
                    if resp.status_code in [200, 201, 202, 204]:
                        success = True
                        break
                except Exception as e:
                    time.sleep(0.5 * (2 ** attempt))

            if not success:
                logger.warning(f"[WEBHOOK] Failed to dispatch {payload['event']} to {url} after {max_retries} retries. Stored in DLQ.")
                self.dead_letter_queue.append({"url": url, "payload": payload, "failed_at": time.time()})
