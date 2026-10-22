"""api/validation.py — strict, typed parsing of JSON request bodies.

Handlers historically coerced request fields inline (``float(payload.get(
"quantity", 0.01))``, ``str(...).upper()``, ``int(...)``). Any client that sent
a JSON object, list, ``null`` or boolean for one of those fields triggered a
``TypeError``/``ValueError`` and a 500; ``float("NaN")`` / ``float("inf")``
slipped *through* and poisoned downstream risk arithmetic; ``True`` silently
became ``1.0``.

This module gives every handler the same small vocabulary:

* each ``get_*`` helper returns a value of exactly the advertised type or
  raises :class:`RequestValidationError` naming the offending field;
* numbers must be real and finite (booleans are not numbers; ``"NaN"`` and
  ``1e309`` are rejected) and may be range-checked;
* strings may be length-limited, upper-cased, matched against a pattern or a
  closed set of choices;
* booleans are strict (only JSON ``true``/``false``; ``"false"`` is an error,
  not a truthy string).

:func:`install_validation_error_handler` (called from
``api.request_guard.install_request_guards``) turns the exception into a
uniform ``400 {"status": "ERROR", "error": "INVALID_REQUEST", "field": ...}``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from flask import Flask, jsonify, request

T = TypeVar("T")


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "MISSING"


MISSING: Any = _Missing()

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
TIMEFRAMES = frozenset({"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"})


class RequestValidationError(ValueError):
    """A request field is missing, mistyped or out of range (→ HTTP 400)."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message

    def to_response_body(self) -> dict[str, Any]:
        return {
            "status": "ERROR",
            "error": "INVALID_REQUEST",
            "field": self.field,
            "message": f"{self.field}: {self.message}",
        }


def json_body() -> dict[str, Any]:
    """The request's JSON object body (``{}`` when absent or empty).

    The global request guard already rejects non-object JSON bodies on
    mutating routes; this helper re-checks so it is safe on any route.
    """
    data = request.get_json(force=True, silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RequestValidationError("body", "must be a JSON object")
    return data


def _lookup(body: dict[str, Any], name: str, default: Any) -> tuple[bool, Any]:
    if name in body and body[name] is not None:
        return True, body[name]
    if default is MISSING:
        raise RequestValidationError(name, "is required")
    return False, default


def get_str(
    body: dict[str, Any],
    name: str,
    default: Any = MISSING,
    *,
    choices: Iterable[str] | None = None,
    pattern: re.Pattern[str] | None = None,
    max_len: int = 256,
    min_len: int = 0,
    upper: bool = False,
    lower: bool = False,
    strip: bool = True,
) -> Any:
    present, value = _lookup(body, name, default)
    if not present:
        return value
    if not isinstance(value, str):
        raise RequestValidationError(name, f"must be a string, got {type(value).__name__}")
    if strip:
        value = value.strip()
    if upper:
        value = value.upper()
    if lower:
        value = value.lower()
    if len(value) > max_len:
        raise RequestValidationError(name, f"must be at most {max_len} characters")
    if len(value) < min_len:
        raise RequestValidationError(name, f"must be at least {min_len} characters")
    if choices is not None:
        allowed = set(choices)
        if value not in allowed:
            raise RequestValidationError(name, f"must be one of {sorted(allowed)}")
    if pattern is not None and not pattern.match(value):
        raise RequestValidationError(name, f"has an invalid format (expected {pattern.pattern})")
    return value


def get_symbol(body: dict[str, Any], name: str = "symbol", default: Any = MISSING) -> Any:
    """Exchange symbol such as ``BTCUSDT`` (upper-cased; ``/`` and ``-`` removed)."""
    present, value = _lookup(body, name, default)
    if not present:
        return value
    if not isinstance(value, str):
        raise RequestValidationError(name, f"must be a string, got {type(value).__name__}")
    normalized = value.strip().upper().replace("/", "").replace("-", "")
    if not SYMBOL_RE.match(normalized):
        raise RequestValidationError(name, "must be an exchange symbol such as BTCUSDT")
    return normalized


def _check_range(name: str, number: float, minimum: float | None, maximum: float | None,
                 gt: float | None, lt: float | None) -> None:
    if minimum is not None and number < minimum:
        raise RequestValidationError(name, f"must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise RequestValidationError(name, f"must be <= {maximum}")
    if gt is not None and not number > gt:
        raise RequestValidationError(name, f"must be > {gt}")
    if lt is not None and not number < lt:
        raise RequestValidationError(name, f"must be < {lt}")


def coerce_finite_float(name: str, value: Any, *, allow_numeric_string: bool = True) -> float:
    """Convert ``value`` to a finite float or raise :class:`RequestValidationError`."""
    if isinstance(value, bool):
        raise RequestValidationError(name, "must be a number, got boolean")
    if isinstance(value, (int, float)):
        number = float(value)
    elif allow_numeric_string and isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise RequestValidationError(name, "must be a number") from None
    else:
        raise RequestValidationError(name, f"must be a number, got {type(value).__name__}")
    if not math.isfinite(number):
        raise RequestValidationError(name, "must be a finite number")
    return number


def get_float(
    body: dict[str, Any],
    name: str,
    default: Any = MISSING,
    *,
    min: float | None = None,  # noqa: A002 - mirrors the JSON-schema keyword
    max: float | None = None,  # noqa: A002
    gt: float | None = None,
    lt: float | None = None,
    allow_numeric_string: bool = True,
) -> Any:
    present, value = _lookup(body, name, default)
    if not present:
        return value
    number = coerce_finite_float(name, value, allow_numeric_string=allow_numeric_string)
    _check_range(name, number, min, max, gt, lt)
    return number


def get_int(
    body: dict[str, Any],
    name: str,
    default: Any = MISSING,
    *,
    min: int | None = None,  # noqa: A002
    max: int | None = None,  # noqa: A002
    allow_numeric_string: bool = True,
) -> Any:
    present, value = _lookup(body, name, default)
    if not present:
        return value
    if isinstance(value, bool):
        raise RequestValidationError(name, "must be an integer, got boolean")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        number = int(value)
    elif allow_numeric_string and isinstance(value, str) and re.fullmatch(r"\s*[+-]?\d{1,18}\s*", value):
        number = int(value)
    else:
        raise RequestValidationError(name, f"must be an integer, got {type(value).__name__}")
    if min is not None and number < min:
        raise RequestValidationError(name, f"must be >= {min}")
    if max is not None and number > max:
        raise RequestValidationError(name, f"must be <= {max}")
    return number


def get_bool(body: dict[str, Any], name: str, default: Any = MISSING) -> Any:
    """Strict boolean: only JSON ``true`` / ``false`` are accepted."""
    present, value = _lookup(body, name, default)
    if not present:
        return value
    if not isinstance(value, bool):
        raise RequestValidationError(name, f"must be a JSON boolean (true/false), got {type(value).__name__}")
    return value


def get_dict(body: dict[str, Any], name: str, default: Any = MISSING, *, max_keys: int = 256) -> Any:
    present, value = _lookup(body, name, default)
    if not present:
        return dict(value) if isinstance(value, dict) else value
    if not isinstance(value, dict):
        raise RequestValidationError(name, f"must be a JSON object, got {type(value).__name__}")
    if len(value) > max_keys:
        raise RequestValidationError(name, f"must have at most {max_keys} keys")
    return dict(value)


def get_list(
    body: dict[str, Any],
    name: str,
    default: Any = MISSING,
    *,
    max_len: int = 10_000,
    min_len: int = 0,
    item: Callable[[str, Any], T] | None = None,
) -> Any:
    """A JSON array; ``item(field_name, value)`` validates/converts each element."""
    present, value = _lookup(body, name, default)
    if not present:
        return list(value) if isinstance(value, list) else value
    if not isinstance(value, list):
        raise RequestValidationError(name, f"must be a JSON array, got {type(value).__name__}")
    if len(value) > max_len:
        raise RequestValidationError(name, f"must have at most {max_len} items")
    if len(value) < min_len:
        raise RequestValidationError(name, f"must have at least {min_len} items")
    if item is None:
        return list(value)
    return [item(f"{name}[{index}]", element) for index, element in enumerate(value)]


def object_item(field: str, value: Any) -> dict[str, Any]:
    """``get_list`` item validator: each element must be a JSON object."""
    if not isinstance(value, dict):
        raise RequestValidationError(field, f"must be a JSON object, got {type(value).__name__}")
    return value


def finite_float_item(field: str, value: Any) -> float:
    """``get_list`` item validator: each element must be a finite number."""
    return coerce_finite_float(field, value)


def install_validation_error_handler(app: Flask) -> None:
    """Render :class:`RequestValidationError` as a JSON 400 everywhere."""

    @app.errorhandler(RequestValidationError)
    def _handle_validation_error(exc: RequestValidationError):  # pragma: no cover - exercised via routes
        return jsonify(exc.to_response_body()), 400


# ── query-string parameters ─────────────────────────────────────────────────
#
# Query values always arrive as strings. Syntax errors (text, NaN/inf,
# fractional values for integers, oversized input) are client errors and
# raise RequestValidationError (→ 400). Out-of-range *numbers* are clamped
# into [min, max] so existing dashboards asking for "limit=5000" still get a
# bounded answer instead of an error — but never an unbounded one.

_QUERY_INT_RE = re.compile(r"[+-]?\d{1,18}")
_QUERY_MAX_LEN = 256
_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})


def _query_raw(name: str) -> str | None:
    raw = request.args.get(name)
    if raw is None:
        return None
    text = raw.strip()
    if len(text) > _QUERY_MAX_LEN:
        raise RequestValidationError(name, f"must be at most {_QUERY_MAX_LEN} characters")
    if "\x00" in text:
        raise RequestValidationError(name, "must not contain NUL characters")
    return text or None


def query_int(name: str, default: int, *, min: int, max: int) -> int:  # noqa: A002
    """Integer query parameter, clamped into ``[min, max]``; missing/empty → ``default``."""
    raw = _query_raw(name)
    if raw is None:
        return default
    if not _QUERY_INT_RE.fullmatch(raw):
        raise RequestValidationError(name, "must be an integer")
    return max if int(raw) > max else min if int(raw) < min else int(raw)


def query_float(name: str, default: float, *, min: float, max: float) -> float:  # noqa: A002
    """Finite float query parameter, clamped into ``[min, max]``."""
    raw = _query_raw(name)
    if raw is None:
        return default
    try:
        number = float(raw)
    except ValueError:
        raise RequestValidationError(name, "must be a number") from None
    if not math.isfinite(number):
        raise RequestValidationError(name, "must be a finite number")
    return float(max) if number > max else float(min) if number < min else number


def query_bool(name: str, default: bool = False) -> bool:
    raw = _query_raw(name)
    if raw is None:
        return default
    word = raw.lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise RequestValidationError(name, "must be true or false")


def query_choice(name: str, default: str, choices: Iterable[str], *, case: str = "lower") -> str:
    """One of ``choices`` (compared after lower/upper-casing per ``case``)."""
    raw = _query_raw(name)
    if raw is None:
        return default
    value = raw.lower() if case == "lower" else raw.upper() if case == "upper" else raw
    allowed = set(choices)
    if value not in allowed:
        raise RequestValidationError(name, f"must be one of {sorted(allowed)}")
    return value


def query_symbol(name: str = "symbol", default: str | None = "BTCUSDT") -> str | None:
    """Exchange symbol (``BTCUSDT``): upper-cased, ``SYMBOL_RE`` enforced."""
    raw = _query_raw(name)
    if raw is None:
        return default
    value = raw.upper()
    if not SYMBOL_RE.match(value):
        raise RequestValidationError(name, "must be an exchange symbol such as BTCUSDT")
    return value


def query_timeframe(name: str = "timeframe", default: str = "1h") -> str:
    raw = _query_raw(name)
    if raw is None:
        return default
    if raw not in TIMEFRAMES:
        raise RequestValidationError(name, f"must be one of {sorted(TIMEFRAMES)}")
    return raw


def query_text(name: str, default: str | None = None, *, max_len: int = 128,
               pattern: re.Pattern[str] | None = SAFE_ID_RE) -> str | None:
    """Free-form filter value (status, strategy id, …) with a length/charset bound."""
    raw = _query_raw(name)
    if raw is None:
        return default
    if len(raw) > max_len:
        raise RequestValidationError(name, f"must be at most {max_len} characters")
    if pattern is not None and not pattern.match(raw):
        raise RequestValidationError(name, f"has an invalid format (expected {pattern.pattern})")
    return raw
