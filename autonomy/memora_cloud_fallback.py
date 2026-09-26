"""Small cloud-only Memora client for agent images without the full SDK."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class MemoraClient:
    """HTTP adapter for agent containers; it never writes to an agent-local DB."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 5.0):
        self.base_url = (base_url or os.getenv("MEMORA_URL", "https://memora-cavc.onrender.com")).rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.last_error: str | None = None

    def _request(self, agent_name: str, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        agent = agent_name.strip().lower()
        key = self.api_key or os.getenv(f"{agent.upper()}_API_KEY")
        if not key:
            self.last_error = f"{agent.upper()}_API_KEY is not configured"
            return {"status": "error", "cloud": False, "error": self.last_error}
        headers = {
            "X-Agent-Name": agent,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
                result = json.loads(body.decode("utf-8")) if body else {}
                if response.status < 200 or response.status >= 300:
                    self.last_error = f"Memora HTTP {response.status}"
                    return {"status": "error", "cloud": False, "error": self.last_error}
                self.last_error = None
                if isinstance(result, dict):
                    result.setdefault("cloud", True)
                return result
        except urllib.error.HTTPError as exc:
            self.last_error = f"Memora HTTP {exc.code}"
            return {"status": "error", "cloud": False, "error": self.last_error}
        except Exception as exc:
            self.last_error = type(exc).__name__
            return {"status": "error", "cloud": False, "error": self.last_error}

    def record_interaction(self, agent_name: str, user_input: str, agent_output: str, event_type: str = "dialogue", tags=None, metadata=None):
        return self._request(agent_name, "/v1/memories/record-interaction", method="POST", payload={
            "agent_name": agent_name.lower(), "user_text": user_input, "agent_text": agent_output,
            "event_type": event_type, "tags": tags or [], "metadata": metadata or {},
        })

    def record_fact(self, agent_name: str, fact_text: str, category: str = "general", importance: float = 0.8, entities=None):
        return self._request(agent_name, "/v1/memories", method="POST", payload={
            "content_text": fact_text, "memory_type": "semantic", "source": f"agent:{agent_name.lower()}",
            "confidence": 1.0, "importance": importance,
            "provenance": {"category": category, "entities": entities or [agent_name.lower(), category]},
        })

    def learn_from_outcome(self, agent_name: str, task_name: str, status: str, error_log: str | None = None,
                           actions_taken: str | None = None, context: str | None = None, domain: str | None = None):
        return self._request(agent_name, "/v1/memories/learn-outcome", method="POST", payload={
            "agent_name": agent_name.lower(), "task_name": task_name, "status": status,
            "error_log": error_log, "actions_taken": actions_taken, "context": context, "domain": domain,
        })

    def recall_memories(self, agent_name: str, query: str, limit: int = 5):
        result = self._request(agent_name, f"/v1/memories/search?q={urllib.parse.quote(query)}&limit={limit}")
        if isinstance(result, dict) and result.get("status") == "error":
            logger.warning("Memora cloud recall failed (%s)", self.last_error or result.get("error", "unknown error"))
        return result if isinstance(result, list) else result.get("memories", []) if isinstance(result, dict) else []

    def recall_experience(self, agent_name: str, task_query: str, domain: str | None = None, limit: int = 5):
        params = {"limit": limit}
        if domain:
            params["domain"] = domain
        result = self._request(agent_name, f"/v1/memories/experience?{urllib.parse.urlencode(params)}")
        if isinstance(result, dict) and result.get("status") == "error":
            logger.warning("Memora cloud experience recall failed (%s)", self.last_error or result.get("error", "unknown error"))
        return result if isinstance(result, list) else result.get("memories", []) if isinstance(result, dict) else []

    def read_event_cursor(self, agent_name: str, consumer_id: str = "default"):
        query = urllib.parse.urlencode({"consumer_id": consumer_id})
        return self._request(agent_name, f"/v1/events/cursor?{query}")

    def poll_events(self, agent_name: str, after_id: int = 0, limit: int = 100):
        query = urllib.parse.urlencode({"after_id": after_id, "limit": limit})
        return self._request(agent_name, f"/v1/events?{query}")

    def acknowledge_event(self, agent_name: str, event_id: int, consumer_id: str = "default"):
        return self._request(agent_name, "/v1/events/ack", method="POST", payload={"event_id": int(event_id), "consumer_id": consumer_id})

    def build_self_upgrade_context(self, agent_name: str, task_query: str, domain: str | None = None) -> str:
        entries = self.recall_experience(agent_name, task_query, domain=domain)
        texts = [str(item.get("content_text", "")).strip() for item in entries if isinstance(item, dict)]
        return "\n".join(f"- {text}" for text in texts if text)

    def record_security_decision(self, action_type: str, approval_status: str, fingerprint: str, details: str):
        content = f"Security decision: {action_type}; result: {approval_status}; fingerprint: {fingerprint}. {details}"
        return self.record_fact("sentinel", content, category="security_decision", importance=0.9)

    def record_consultation(self, topic: str, outcome_summary: str, providers_used: list[str]):
        return self.record_interaction("inference", topic, outcome_summary, event_type="model_consultation", tags=providers_used)


memora_client = MemoraClient()
