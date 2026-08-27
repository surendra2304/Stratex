"""
api/auth.py — Role-Based API Key Authentication & Access Control.

Key Roles:
- READ: Access to all /api/v1/status*, /api/v1/health*, and /api/v1/export* endpoints.
- CONTROL: Access to read + /api/v1/control/pause, /api/v1/control/resume, /api/v1/control/strategy/*, /api/v1/control/risk-limits.
- FRIDAY / ADMIN: Full access including /api/v1/control/panic and advisory overrides.

Loads from environment variables:
- TRADING_BOT_API_KEY_READ
- TRADING_BOT_API_KEY_CONTROL
- TRADING_BOT_API_KEY_FRIDAY
"""

import os
import time
from functools import wraps
from typing import Dict, Optional, Tuple
from flask import request, jsonify
from security_hardening import SecurityRateLimiter, mask_credential

# Default dev keys for graceful testing/fallback if unset
_DEFAULT_KEYS = {
    "READ": os.getenv("TRADING_BOT_API_KEY_READ", "read_key_default_secret_123"),
    "CONTROL": os.getenv("TRADING_BOT_API_KEY_CONTROL", "control_key_default_secret_456"),
    "FRIDAY": os.getenv("TRADING_BOT_API_KEY_FRIDAY", "friday_key_default_secret_789")
}

# Key permissions hierarchy
PERMISSIONS = {
    "READ": ["read"],
    "CONTROL": ["read", "control"],
    "FRIDAY": ["read", "control", "admin", "advisory_override"]
}

# Dedicated rate limiter for authentication & control endpoints (60 req/min for general, 10 req/min for control)
api_rate_limiter = SecurityRateLimiter(max_requests=60, window_seconds=60)
control_rate_limiter = SecurityRateLimiter(max_requests=10, window_seconds=60)


def extract_api_key() -> Optional[str]:
    """Extracts API key from X-API-Key or Authorization header."""
    key = request.headers.get("X-API-Key")
    if not key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            key = auth_header.split(" ")[1].strip()
    return key


def get_key_role(api_key: str) -> Optional[str]:
    """Identifies role of the provided API key."""
    if not api_key:
        return None
    for role, secret in _DEFAULT_KEYS.items():
        if api_key == secret:
            return role
    # Fallback to legacy BOT_API_KEY as CONTROL
    legacy_key = os.getenv("BOT_API_KEY", "")
    if legacy_key and api_key == legacy_key:
        return "CONTROL"
    return None


def require_permission(required_perm: str):
    """Decorator to enforce role-based access control on ecosystem endpoints."""
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
