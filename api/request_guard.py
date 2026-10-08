"""api/request_guard.py — app-wide request body validation and JSON error envelopes.

The live POST census (hostile bodies against every route) found 28 HTTP 500s:
handlers assumed ``request.get_json()`` returns a dict and crashed with
``AttributeError: 'list' object has no attribute 'get'`` on a JSON array body,
or converted Flask's own 400/415 into 500 inside broad ``except Exception``
blocks. Instead of trusting ~40 handlers individually, every mutating request
is validated once, before routing:

* a non-empty body must parse as JSON and its top level must be an object,
  otherwise the request is rejected with a JSON ``400`` (no route in this API
  accepts form data, raw text or a top-level JSON array);
* bodies larger than ``STRATEX_MAX_REQUEST_BYTES`` (default 1 MiB) are refused
  with ``413`` before they are buffered;
* unhandled exceptions on API paths return a generic JSON ``500`` (the
  traceback goes to the server log, never to the client).

Emergency stop endpoints are exempt from body validation: a malformed body must
never prevent an operator from engaging a panic/pause. Those handlers treat any
non-object body as ``{}`` themselves.
"""

from __future__ import annotations

import json
import os

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from api.validation import RequestValidationError
from security_hardening import EMERGENCY_ENDPOINTS

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_API_PREFIXES = ("/api/", "/v1/")


def _max_request_bytes() -> int:
    try:
        value = int(os.getenv("STRATEX_MAX_REQUEST_BYTES", str(1024 * 1024)))
    except ValueError:
        value = 1024 * 1024
    return max(1024, value)


def _error(status: int, code: str, message: str):
    return jsonify({"status": "ERROR", "error": code, "message": message}), status


def validate_json_object_body():
    """``before_request`` hook; returns an error response or ``None``."""
    if (
        request.method not in MUTATING_METHODS
        or request.endpoint is None  # unknown route: let Flask answer 404/405
        or request.endpoint in EMERGENCY_ENDPOINTS
    ):
        return None
    raw = request.get_data(cache=True)  # cached: handlers can still call get_json()
    if not raw or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return _error(400, "INVALID_JSON", "Request body must be valid UTF-8 JSON.")
    if not isinstance(parsed, dict):
        return _error(400, "JSON_OBJECT_REQUIRED", "The JSON request body must be an object.")
    return None


def install_request_guards(app: Flask) -> None:
    app.config.setdefault("MAX_CONTENT_LENGTH", _max_request_bytes())
    app.before_request(validate_json_object_body)

    @app.errorhandler(413)
    def _too_large(_exc):
        return _error(413, "REQUEST_TOO_LARGE", f"Request body exceeds {app.config['MAX_CONTENT_LENGTH']} bytes.")

    @app.errorhandler(RequestValidationError)
    def _invalid_request(exc: RequestValidationError):
        return jsonify(exc.to_response_body()), 400

    @app.errorhandler(Exception)
    def _unhandled(exc):
        if isinstance(exc, HTTPException):
            return exc  # 404/405/... keep Flask's normal handling
        if isinstance(exc, RequestValidationError):  # raised outside the view (hooks)
            return jsonify(exc.to_response_body()), 400
        app.logger.exception(f"Unhandled error on {request.method} {request.path}")
        if request.path.startswith(_API_PREFIXES):
            return _error(500, "INTERNAL_ERROR", "Unexpected server error; details were logged.")
        return "Internal Server Error", 500
