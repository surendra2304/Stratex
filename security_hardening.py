"""
security_hardening.py — Production Security, Credential Protection, Rate Limiting & Anomaly Detection.

Provides:
1. Multi-tier API Key Authentication (Read-only, Control, FRIDAY scoped keys).
2. Token-bucket Rate Limiter per key/IP (60 req/min read, 10 req/min control).
3. Request payload validation & deep sanitization against injection.
4. Cryptographic audit trail in control_audit.jsonl (with HMAC / SHA-256 chain).
5. Webhook HMAC signature verification for external webhooks.
6. Endpoint abuse & anomaly detection (failed auth tracking, unusual IP pattern alerts).
7. Credential masking, rotation support without downtime, and encryption at rest utilities.
"""

import datetime
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import jsonify, request

from logger import get_logger

logger = get_logger("security_hardening")

CONTROL_AUDIT_LOG_FILE = os.getenv("CONTROL_AUDIT_LOG_FILE", "control_audit.jsonl")

# --- Secret material policy -------------------------------------------------
# NEVER fall back to a hardcoded secret shipped in source: anyone who can read
# the repository could then forge audit-chain signatures and webhook payloads.
# If SECURITY_SECRET_KEY is unset we generate an ephemeral per-process secret
# (signatures stay internally consistent for this process lifetime but do not
# survive restarts) and scream about it in the logs. Production MUST set it.
_env_audit_secret = os.getenv("SECURITY_SECRET_KEY", "").strip()
if _env_audit_secret:
    _SECRET_KEY = _env_audit_secret.encode("utf-8")
else:
    _SECRET_KEY = secrets.token_bytes(32)
    logger.critical(
        "[SECURITY] SECURITY_SECRET_KEY is NOT set. Generated an ephemeral "
        "per-process audit secret: control-audit signatures cannot be verified "
        "across restarts or processes. Set SECURITY_SECRET_KEY in the "
        "deployment environment."
    )

# Inbound webhook verification FAILS CLOSED when no secret is configured:
# an unset WEBHOOK_SECRET means no signature can ever validate.
_env_webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
WEBHOOK_SECRET = _env_webhook_secret or None
if WEBHOOK_SECRET is None:
    logger.critical(
        "[SECURITY] WEBHOOK_SECRET is NOT set. Inbound webhook signature "
        "verification is DISABLED (all signatures will be rejected). Set "
        "WEBHOOK_SECRET to accept signed webhooks."
    )

# Scopes
SCOPE_READ = "read"
SCOPE_CONTROL = "control"
SCOPE_FRIDAY = "friday"
SCOPE_ADMIN = "admin"

class SecurityRateLimiter:
    """
    In-memory Sliding Window Rate Limiter.
    Tracks requests per key (or IP) with configurable limits.
    """

    def __init__(self, default_limit: int = 60, window_seconds: int = 60, max_requests: int | None = None):
        self.default_limit = max_requests if max_requests is not None else default_limit
        self.max_requests = self.default_limit
        self.window_seconds = window_seconds
        self.records: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, identifier: str, limit: int | None = None) -> tuple[bool, int, int]:
        """
        Returns (is_allowed, remaining_requests, retry_after_seconds).
        """
        now = time.time()
        max_allowed = limit if limit is not None else self.default_limit

        with self._lock:
            if identifier not in self.records:
                self.records[identifier] = [now]
                return True, max_allowed - 1, 0

            cutoff = now - self.window_seconds
            valid_timestamps = [t for t in self.records[identifier] if t > cutoff]
            self.records[identifier] = valid_timestamps

            if len(valid_timestamps) < max_allowed:
                valid_timestamps.append(now)
                remaining = max_allowed - len(valid_timestamps)
                return True, remaining, 0
            else:
                oldest = valid_timestamps[0]
                retry_after = int(self.window_seconds - (now - oldest)) + 1
                return False, 0, max(1, retry_after)

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            active_keys = {k: len([t for t in v if t > cutoff]) for k, v in self.records.items()}
            return {
                "window_seconds": self.window_seconds,
                "tracked_identifiers": len(active_keys),
                "active_counts": active_keys
            }


# Global rate limiter instances
_read_rate_limiter = SecurityRateLimiter(default_limit=60, window_seconds=60)
_control_rate_limiter = SecurityRateLimiter(default_limit=10, window_seconds=60)
_ip_rate_limiter = SecurityRateLimiter(default_limit=100, window_seconds=3600)


class SecurityMonitor:
    """
    Self-security monitoring:
    1. Endpoint abuse tracking (consecutive failed auths from IP -> temporary block & alert).
    2. Unusual API usage tracking (control action from novel IP).
    3. Session & Auth failure audit logs.
    """

    def __init__(self):
        self.failed_auth_attempts: dict[str, list[float]] = {}
        self.recent_auth_failures: list[dict[str, Any]] = []
        self.known_control_ips: set = {"127.0.0.1", "localhost", "::1"}
        self.alerts: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record_auth_failure(self, ip_address: str, endpoint: str, key_provided: str = ""):
        now = time.time()
        with self._lock:
            if ip_address not in self.failed_auth_attempts:
                self.failed_auth_attempts[ip_address] = []
            self.failed_auth_attempts[ip_address].append(now)

            # Keep last 1 hour of failures
            cutoff = now - 3600
            self.failed_auth_attempts[ip_address] = [t for t in self.failed_auth_attempts[ip_address] if t > cutoff]

            fail_entry = {
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "ip": ip_address,
                "endpoint": endpoint,
                "key_preview": mask_credential(key_provided) if key_provided else "NONE",
                "failures_in_window": len(self.failed_auth_attempts[ip_address])
            }
            self.recent_auth_failures.append(fail_entry)
            if len(self.recent_auth_failures) > 100:
                self.recent_auth_failures = self.recent_auth_failures[-100:]

            # Check threshold: > 5 failed attempts in 5 minutes -> Abuse alert
            recent_5m = [t for t in self.failed_auth_attempts[ip_address] if t > now - 300]
            if len(recent_5m) >= 5:
                alert = {
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "type": "ENDPOINT_ABUSE_DETECTED",
                    "ip": ip_address,
                    "count": len(recent_5m),
                    "action": "TEMPORARY_RATE_LIMIT"
                }
                self.alerts.append(alert)
                logger.critical(f"[SECURITY_ALERT] 🚨 Endpoint abuse detected from IP {ip_address}: {len(recent_5m)} auth failures in 5 min")

    def is_ip_blocked(self, ip_address: str) -> bool:
        now = time.time()
        with self._lock:
            fails = self.failed_auth_attempts.get(ip_address, [])
            recent_fails = [t for t in fails if t > now - 300]
            return len(recent_fails) >= 10

    def check_unusual_control_ip(self, ip_address: str, endpoint: str) -> bool:
        with self._lock:
            if ip_address not in self.known_control_ips:
                self.known_control_ips.add(ip_address)
                # First time seen for control
                alert = {
                    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                    "type": "NEW_CONTROL_ORIGIN_IP",
                    "ip": ip_address,
                    "endpoint": endpoint
                }
                self.alerts.append(alert)
                logger.warning(f"[SECURITY_NOTICE] Control endpoint {endpoint} called from new IP address: {ip_address}")
                return True
            return False

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "recent_auth_failures": self.recent_auth_failures[-20:],
                "recent_alerts": self.alerts[-20:],
                "blocked_ips_count": sum(1 for fails in self.failed_auth_attempts.values() if len([t for t in fails if t > time.time() - 300]) >= 10),
                "total_failure_records": len(self.recent_auth_failures)
            }


_security_monitor = SecurityMonitor()


def get_configured_api_keys() -> dict[str, dict[str, Any]]:
    """
    Returns valid active API keys mapped to their allowed scopes.
    Supports comma-separated keys for seamless zero-downtime key rotation.
    """
    keys_map = {}

    def add_keys(env_var: str, role: str, scopes: list[str]):
        raw_val = os.getenv(env_var, "").strip()
        if not raw_val:
            return
        for k in raw_val.split(","):
            k = k.strip()
            if k:
                keys_map[k] = {"role": role, "scopes": scopes, "source": env_var}

    # 1. Admin / Master Bot Key (Full control)
    add_keys("BOT_API_KEY", "ADMIN", [SCOPE_READ, SCOPE_CONTROL, SCOPE_FRIDAY, SCOPE_ADMIN])
    add_keys("API_KEY_CONTROL", "CONTROL", [SCOPE_READ, SCOPE_CONTROL])
    add_keys("API_KEY_READONLY", "READONLY", [SCOPE_READ])
    add_keys("API_KEY_FRIDAY", "FRIDAY", [SCOPE_READ, SCOPE_CONTROL, SCOPE_FRIDAY])

    return keys_map


def sanitize_input(value: Any) -> Any:
    """Recursively sanitizes dictionary or string input against injection and control chars."""
    if isinstance(value, str):
        cleaned = re.sub(r"[<>{}\x00-\x1f]", "", value)
        return cleaned.strip()
    elif isinstance(value, dict):
        return {sanitize_input(k): sanitize_input(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [sanitize_input(x) for x in value]
    return value


def mask_credential(credential: str) -> str:
    """Safely masks API keys and tokens for logs (e.g. abcd****wxyz)."""
    if not credential:
        return "NOT_SET"
    if len(credential) <= 8:
        return "****"
    return f"{credential[:4]}****{credential[-4:]}"


def sign_audit_record(data_dict: dict[str, Any], prev_hash: str = "") -> str:
    """Computes SHA-256 HMAC digest for audit trail tamper-proofing."""
    serialized = json.dumps(data_dict, sort_keys=True)
    message = f"{prev_hash}:{serialized}".encode()
    return hmac.new(_SECRET_KEY, message, hashlib.sha256).hexdigest()


def log_control_action(action: str, user_or_key: str, details: dict[str, Any], status: str = "SUCCESS") -> dict[str, Any]:
    """Appends cryptographically signed audit record to control_audit.jsonl."""
    try:
        prev_hash = ""
        if os.path.exists(CONTROL_AUDIT_LOG_FILE):
            try:
                with open(CONTROL_AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                    if lines:
                        last_rec = json.loads(lines[-1])
                        prev_hash = last_rec.get("signature", "")
            except Exception:
                prev_hash = ""

        entry = {
            "id": f"AUD_{int(time.time() * 1000)}",
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "action": sanitize_input(action),
            "actor": mask_credential(user_or_key),
            "ip": request.remote_addr if request else "LOCAL",
            "status": status,
            "details": sanitize_input(details)
        }
        sig = sign_audit_record(entry, prev_hash=prev_hash)
        entry["signature"] = sig
        entry["prev_signature"] = prev_hash

        with open(CONTROL_AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        logger.info(f"[AUDIT] Action logged: {action} by {entry['actor']} [{status}]")
        return entry
    except Exception as e:
        logger.error(f"[AUDIT] Failed to write audit trail: {e}")
        return {}


def verify_audit_chain(records: list[dict[str, Any]]) -> bool:
    """Verifies that an audit log ledger has not been tampered with or truncated.

    Every audit record is signed at write time, so an *unsigned* record inside
    the ledger is itself evidence of tampering (signature-stripping attack)
    and must fail verification — it is NOT skipped.
    """
    prev_hash = ""
    for r in records:
        expected_sig = r.get("signature")
        if not expected_sig:
            logger.error(f"[SECURITY] Audit record missing signature (tampering evidence) — record ID: {r.get('id')}")
            return False
        data_to_verify = {k: v for k, v in r.items() if k not in ("signature", "prev_signature")}
        computed = sign_audit_record(data_to_verify, prev_hash=prev_hash)
        if computed != expected_sig:
            logger.error(f"[SECURITY] Audit trail signature mismatch detected for record ID: {r.get('id')}")
            return False
        prev_hash = expected_sig
    return True


def _load_audit_records(path: str | None = None) -> tuple[list[dict[str, Any]], int]:
    """Parse the audit ledger strictly.

    Returns (records, corrupt_line_count). A line that is not a JSON object is
    counted as corrupt — corruption must be surfaced, never silently skipped.
    """
    audit_path = path or CONTROL_AUDIT_LOG_FILE
    records: list[dict[str, Any]] = []
    corrupt = 0
    try:
        with open(audit_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except Exception:
                    corrupt += 1
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
                else:
                    corrupt += 1
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"[SECURITY] Audit ledger unreadable: {e}")
        corrupt += 1
    return records, corrupt


def audit_trail_integrity(path: str | None = None) -> str:
    """Compute the live audit-trail integrity verdict for the security report.

    Never returns a hardcoded VERIFIED: the ledger is actually read and the
    HMAC chain actually re-verified on every call.
    """
    records, corrupt = _load_audit_records(path)
    if corrupt:
        logger.error(f"[SECURITY] Audit ledger has {corrupt} corrupt/unparseable line(s)")
        return f"HMAC_CHAIN_DEGRADED: {corrupt} corrupt line(s) detected"
    if not records:
        return "NO_AUDIT_RECORDS"
    if verify_audit_chain(records):
        return "HMAC_CHAIN_VERIFIED"
    return "HMAC_CHAIN_BROKEN: signature mismatch detected"


def verify_webhook_signature(payload_bytes: bytes, signature_header: str, secret: str | None = None) -> bool:
    """
    Verifies HMAC-SHA256 signature on incoming webhook payloads.
    Signature header format can be hex digest or 'sha256=<hex>'.
    """
    if not signature_header or not payload_bytes:
        return False

    effective_secret = secret or WEBHOOK_SECRET
    if not effective_secret:
        # Fail closed: without a configured secret no webhook can be trusted.
        return False

    sec = effective_secret.encode("utf-8")
    clean_sig = signature_header.split("=")[-1].strip()
    expected = hmac.new(sec, payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, clean_sig)


def authenticate_request(required_scope: str = SCOPE_READ) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Validates X-API-KEY / X-BOT-API-KEY header and checks required scope.
    If no keys are configured in environment (open demo / dev mode), allows read gracefully.
    """
    configured_keys = get_configured_api_keys()

    # An unconfigured deployment must never grant authority it has no way to
    # verify. Anonymous READ stays open (public status/health pages depend on
    # it); every scope above read FAILS CLOSED. Granting SCOPE_CONTROL here let
    # an unauthenticated caller flatten positions, trip the panic kill switch
    # and mutate runtime config whenever BOT_API_KEY / API_KEY_CONTROL /
    # API_KEY_READONLY / API_KEY_FRIDAY were all unset — which is the shipped
    # default (.env.example leaves them empty, render.yaml marks them sync:false).
    if not configured_keys:
        if required_scope == SCOPE_READ:
            return True, "ANONYMOUS_DEV", {"role": "DEV", "scopes": [SCOPE_READ]}
        logger.critical(
            f"[SECURITY] Rejecting '{required_scope}' request to {request.path}: no API keys are "
            "configured (BOT_API_KEY / API_KEY_CONTROL / API_KEY_READONLY / API_KEY_FRIDAY are all "
            "unset). Control authority fails closed — set a key to enable this endpoint."
        )
        return False, "AUTH_NOT_CONFIGURED", None

    incoming_key = (
        request.headers.get("X-API-KEY")
        or request.headers.get("X-BOT-API-KEY")
        or request.headers.get("X-Bot-Api-Key")
        or request.headers.get("Authorization", "").replace("Bearer ", "")
    ).strip()

    ip = request.remote_addr or "127.0.0.1"

    if _security_monitor.is_ip_blocked(ip):
        return False, "IP_TEMPORARILY_BLOCKED", None

    if not incoming_key:
        _security_monitor.record_auth_failure(ip, request.path, "")
        return False, "MISSING_API_KEY", None

    key_info = configured_keys.get(incoming_key)
    if not key_info:
        _security_monitor.record_auth_failure(ip, request.path, incoming_key)
        return False, "INVALID_API_KEY", None

    if required_scope not in key_info.get("scopes", []):
        _security_monitor.record_auth_failure(ip, request.path, incoming_key)
        return False, f"INSUFFICIENT_SCOPE: required '{required_scope}'", key_info

    return True, None, key_info


def control_scope_denial(required_scope: str = SCOPE_CONTROL):
    """Inline control-scope guard for views that mix GET and POST in one function.

    ``require_api_scope`` is a decorator, so it cannot protect only the mutating
    branch of a combined ``methods=['GET','POST']`` view without also gating the
    read branch. This returns ``None`` when the caller is authorized, otherwise a
    ready-to-return ``(response, status_code)`` tuple:

      * 503 AUTH_NOT_CONFIGURED — no API key is configured at all. The server
        cannot verify anyone, so it must not accept control commands.
      * 401 UNAUTHORIZED        — keys are configured and this caller lacks one.
    """
    auth_ok, auth_err, _key_info = authenticate_request(required_scope=required_scope)
    if auth_ok:
        return None
    if auth_err == "AUTH_NOT_CONFIGURED":
        return jsonify({
            "status": "UNAUTHORIZED",
            "error": "AUTH_NOT_CONFIGURED",
            "message": "No API key is configured on this deployment; control endpoints "
                       "fail closed. Set BOT_API_KEY or API_KEY_CONTROL.",
        }), 503
    return jsonify({
        "status": "UNAUTHORIZED",
        "error": f"Authentication failed: {auth_err}",
    }), 401


def require_api_scope(scope: str = SCOPE_READ, is_control: bool = False):
    """
    Flask Decorator for role-based scope verification, rate limiting, and audit logging.
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated(*args, **kwargs):
            ip = request.remote_addr or "127.0.0.1"

            # 1. IP Rate Limiting Check
            allowed, remaining, retry_after = _ip_rate_limiter.is_allowed(ip)
            if not allowed:
                return jsonify({
                    "status": "RATE_LIMITED",
                    "error": f"IP Rate limit exceeded. Retry after {retry_after}s",
                    "retry_after": retry_after
                }), 429

            # 2. Key Scope & Auth Check
            auth_ok, auth_err, key_info = authenticate_request(required_scope=scope)
            if not auth_ok:
                # An unconfigured deployment cannot verify anyone: report 503 so
                # operators see a server-side misconfiguration rather than being
                # told their (impossible) credentials were wrong.
                if auth_err == "AUTH_NOT_CONFIGURED":
                    return jsonify({
                        "status": "UNAUTHORIZED",
                        "error": "AUTH_NOT_CONFIGURED",
                        "message": "No API key is configured on this deployment; control "
                                   "endpoints fail closed. Set BOT_API_KEY or API_KEY_CONTROL.",
                    }), 503
                return jsonify({
                    "status": "UNAUTHORIZED",
                    "error": f"Authentication failed: {auth_err}"
                }), 401

            # 3. Endpoint Rate Limiter (Read: 60/min, Control: 10/min)
            key_id = incoming_key = (
                request.headers.get("X-API-KEY")
                or request.headers.get("X-BOT-API-KEY")
                or ip
            )
            limiter = _control_rate_limiter if is_control else _read_rate_limiter
            rate_limit_max = 10 if is_control else 60

            k_allowed, k_remaining, k_retry = limiter.is_allowed(key_id, limit=rate_limit_max)
            if not k_allowed:
                return jsonify({
                    "status": "RATE_LIMITED",
                    "error": f"API Key rate limit exceeded ({rate_limit_max} req/min). Retry after {k_retry}s",
                    "retry_after": k_retry
                }), 429

            # 4. Control Action Logging & Anomaly Monitor
            if is_control:
                _security_monitor.check_unusual_control_ip(ip, request.path)
                try:
                    payload = request.get_json(silent=True) or {}
                    log_control_action(request.path, key_id, payload, status="SUCCESS")
                except Exception:
                    pass

            return f(*args, **kwargs)
        return decorated
    return decorator


def get_security_status_report() -> dict[str, Any]:
    """Generates complete status report for GET /api/v1/security/status."""
    configured_keys = get_configured_api_keys()
    keys_summary = {
        role: sum(1 for k in configured_keys.values() if k.get("role") == role)
        for role in ["ADMIN", "CONTROL", "READONLY", "FRIDAY"]
    }

    return {
        "status": "HEALTHY",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "auth_configuration": {
            "total_active_keys": len(configured_keys),
            "key_distribution": keys_summary,
            "rotation_support": "ACTIVE",
            "required_headers": ["X-API-KEY", "X-BOT-API-KEY"]
        },
        "rate_limiting": {
            "read_endpoints": {"limit_per_minute": 60, "limiter": "token_bucket"},
            "control_endpoints": {"limit_per_minute": 10, "limiter": "token_bucket"},
            "ip_abuse_limit": {"limit_per_hour": 100}
        },
        "webhook_verification": {
            "algorithm": "HMAC-SHA256",
            "status": "ENFORCED" if WEBHOOK_SECRET else "DISABLED",
            "header": "X-Hub-Signature-256"
        },
        "self_monitoring": _security_monitor.get_status(),
        "audit_trail": {
            "audit_file": CONTROL_AUDIT_LOG_FILE,
            "integrity": audit_trail_integrity()
        }
    }


# Backwards compatibility wrappers
def check_rate_limit(ip_address: str) -> tuple[bool, int, int]:
    return _ip_rate_limiter.is_allowed(ip_address)

class TradingAnomalyDetector:
    def __init__(self):
        self.order_timestamps: list[float] = []
        self._lock = threading.Lock()

    def record_order(self, notional: float) -> tuple[bool, str]:
        now = time.time()
        with self._lock:
            self.order_timestamps = [t for t in self.order_timestamps if now - t < 60.0]
            self.order_timestamps.append(now)

            if len(self.order_timestamps) > 10:
                reason = f"ORDER_FREQUENCY_SPIKE: {len(self.order_timestamps)} orders submitted in < 60s"
                logger.critical(f"[SECURITY_ANOMALY] 🚨 {reason}")
                return True, reason

            if notional > 50000.0:
                reason = f"EXCESSIVE_NOTIONAL: Order notional ${notional:.2f} exceeds safe threshold"
                logger.critical(f"[SECURITY_ANOMALY] 🚨 {reason}")
                return True, reason

        return False, ""

_anomaly_detector = TradingAnomalyDetector()

def check_order_anomaly(notional: float) -> tuple[bool, str]:
    return _anomaly_detector.record_order(notional)
