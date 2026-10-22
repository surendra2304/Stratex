"""
api/auth.py — Consumer-Agnostic Role-Based API Key Authentication.

Key Roles:
- READ: Access to all /api/v1/status*, /api/v1/health*, and /api/v1/export* endpoints.
- CONTROL: Access to read + all /api/v1/control/* endpoints (pause, resume, strategy toggling, emergency panic stop).

Loads strictly from environment variables:
- TRADING_BOT_API_KEY_READ
- TRADING_BOT_API_KEY_CONTROL
"""

import hmac
import os
from functools import wraps

from flask import jsonify, request

from security_hardening import SecurityRateLimiter, mask_credential

# Consumer-agnostic keys
_KEY_ENV = {"READ": "TRADING_BOT_API_KEY_READ", "CONTROL": "TRADING_BOT_API_KEY_CONTROL"}
_PLACEHOLDERS = {"changeme", "change-me", "password", "secret", "read_key_default_secret_123", "control_key_default_secret_456"}


def _production_mode() -> bool:
    return os.getenv("ENVIRONMENT", "").strip().lower() in {"production", "prod"}


def _configured_key(role: str) -> str:
    return os.getenv(_KEY_ENV[role], "").strip()


def _strong_key(key: str) -> bool:
    return len(key) >= 32 and key.lower() not in _PLACEHOLDERS and len(set(key)) > 1

# Key permissions hierarchy
PERMISSIONS = {
    "READ": ["read"],
    "CONTROL": ["read", "control", "admin"]
}

# Dedicated rate limiters (60 req/min for general reads, 10 req/min for control)
api_rate_limiter = SecurityRateLimiter(max_requests=60, window_seconds=60)
control_rate_limiter = SecurityRateLimiter(max_requests=10, window_seconds=60)


def extract_api_key() -> str | None:
    """Extracts API key from X-API-Key or Authorization header."""
    key = request.headers.get("X-API-Key")
    if not key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            key = auth_header.split(" ")[1].strip()
    return key


def get_key_role(api_key: str) -> str | None:
    """Identifies role of the provided API key."""
    if not api_key:
        return None
    for role in _KEY_ENV:
        secret = _configured_key(role)
        if secret and hmac.compare_digest(api_key, secret):
            return role
    # Fallback to legacy BOT_API_KEY as CONTROL
    legacy_key = os.getenv("BOT_API_KEY", "")
    if legacy_key and not _production_mode() and hmac.compare_digest(api_key, legacy_key):
        return "CONTROL"
    return None


def require_permission(required_perm: str):
    """Decorator to enforce consumer-agnostic role-based access control on API endpoints."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # 1. Rate Limiting Check
            ip = request.remote_addr or "127.0.0.1"
            limiter = control_rate_limiter if required_perm in ["control", "admin"] else api_rate_limiter
            allowed, remaining, retry_after = limiter.is_allowed(ip)
            if not allowed:
                return jsonify({
                    "status": "ERROR",
                    "error": "RATE_LIMIT_EXCEEDED",
                    "message": f"Too many requests. Retry after {retry_after} seconds.",
                    "retry_after": retry_after
                }), 429

            # 2. Extract Key & Check Permissions
            if _production_mode():
                read_key = _configured_key("READ")
                control_key = _configured_key("CONTROL")
                if not _strong_key(read_key) or not _strong_key(control_key) or read_key == control_key:
                    return jsonify({
                        "status": "ERROR",
                        "error": "AUTH_NOT_CONFIGURED",
                        "message": "Unique TRADING_BOT_API_KEY_READ and TRADING_BOT_API_KEY_CONTROL secrets are required.",
                    }), 503

            key = extract_api_key()
            role = get_key_role(key)

            if not role:
                return jsonify({
                    "status": "ERROR",
                    "error": "UNAUTHORIZED",
                    "message": "Missing or invalid API key in X-API-Key header."
                }), 401

            user_perms = PERMISSIONS.get(role, [])
            if required_perm not in user_perms:
                return jsonify({
                    "status": "ERROR",
                    "error": "FORBIDDEN",
                    "message": f"API key role '{role}' lacks required '{required_perm}' permission."
                }), 403

            # Attach auth metadata to request context
            request.api_key_role = role
            request.api_key_masked = mask_credential(key)
            return fn(*args, **kwargs)
        return wrapper
    return decorator
