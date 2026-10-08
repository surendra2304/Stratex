"""numeric_safety.py — fail-closed numeric helpers for risk, sizing and execution code.

Why this exists
---------------
IEEE-754 ``NaN`` compares *false* against everything. Risk code written as
``if drawdown >= limit: halt()`` or ``if price <= 0: reject()`` therefore
silently treats ``NaN`` as "within limits" — the most dangerous possible
default for a trading system. A single NaN close price, a corrupt position
record or a telemetry glitch could:

* disable exposure / drawdown / daily-loss limits (``NaN > max`` is False),
* reset a tripped circuit breaker (the "else" branch runs),
* produce ``NaN`` or ``inf`` order quantities (``min(nan, cap)`` is ``nan``;
  ``inf * fraction`` is ``inf``), or crash later in ``math.floor(nan)``,
* pick the *largest* Kelly bet (``min(0.99, nan)`` is ``0.99``).

Booleans are another trap: ``True`` is the integer ``1`` in Python, so a
config typo becomes a price of 1.0.

The helpers below make the safe interpretation explicit and uniform:
non-finite or non-numeric input is *invalid*, and invalid input means "do not
trade / treat as breached", never "proceed".
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any


class InvalidNumericInput(ValueError):
    """A numeric input required for a risk/sizing decision is missing or invalid."""

    def __init__(self, name: str, value: Any, requirement: str):
        super().__init__(f"{name}={value!r} is invalid: {requirement}")
        self.name = name
        self.value = value
        self.requirement = requirement


def finite_float(value: Any) -> float | None:
    """``float(value)`` when it is a real, finite number; otherwise ``None``.

    Rejects ``None``, booleans, non-numeric strings, ``NaN`` and ``±inf``
    (including strings such as ``"nan"`` and ``"1e309"``). Numeric strings and
    numpy/Decimal scalars are accepted.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError, InvalidOperation):
        return None
    return number if math.isfinite(number) else None


def is_finite_number(value: Any) -> bool:
    return finite_float(value) is not None


def positive_float(value: Any) -> float | None:
    """Finite and strictly greater than zero, else ``None``."""
    number = finite_float(value)
    return number if number is not None and number > 0 else None


def non_negative_float(value: Any) -> float | None:
    """Finite and ``>= 0``, else ``None``."""
    number = finite_float(value)
    return number if number is not None and number >= 0 else None


def require_finite(name: str, value: Any) -> float:
    number = finite_float(value)
    if number is None:
        raise InvalidNumericInput(name, value, "must be a finite number")
    return number


def require_positive(name: str, value: Any) -> float:
    number = positive_float(value)
    if number is None:
        raise InvalidNumericInput(name, value, "must be a finite number > 0")
    return number


def require_non_negative(name: str, value: Any) -> float:
    number = non_negative_float(value)
    if number is None:
        raise InvalidNumericInput(name, value, "must be a finite number >= 0")
    return number


def require_fraction(name: str, value: Any, *, allow_zero: bool = True, allow_one: bool = True) -> float:
    """A probability/fraction in ``[0, 1]`` (bounds optionally exclusive)."""
    number = finite_float(value)
    if number is None:
        raise InvalidNumericInput(name, value, "must be a finite fraction")
    low_ok = number > 0 or (allow_zero and number == 0)
    high_ok = number < 1 or (allow_one and number == 1)
    if not (low_ok and high_ok):
        raise InvalidNumericInput(name, value, "must be within [0, 1]")
    return number


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    """``numerator / denominator`` if both finite and the result is finite.

    Returns ``None`` (not ``0``) for a zero/invalid denominator so callers must
    decide explicitly what an undefined ratio means.
    """
    num = finite_float(numerator)
    den = finite_float(denominator)
    if num is None or den is None or den == 0:
        return None
    result = num / den
    return result if math.isfinite(result) else None


def clamp(value: float, low: float, high: float) -> float:
    """Clamp a *finite* value into ``[low, high]`` (raises on NaN)."""
    if not math.isfinite(value):
        raise InvalidNumericInput("value", value, "cannot clamp a non-finite number")
    return max(low, min(high, value))


def step_precision(step: float) -> int:
    """Number of decimals implied by an exchange step/tick size (``0.001`` → 3)."""
    if step >= 1:
        return 0
    text = format(Decimal(str(step)).normalize(), "f")
    return len(text.split(".")[1]) if "." in text else 0


def floor_to_step(quantity: Any, step: Any) -> float:
    """Floor ``quantity`` to a multiple of ``step`` without float drift.

    Returns ``0.0`` for non-positive or invalid quantities; raises
    :class:`InvalidNumericInput` for an invalid step (a zero/negative/NaN step
    would otherwise divide by zero or loop).
    """
    step_value = require_positive("step", step)
    qty = finite_float(quantity)
    if qty is None or qty <= 0:
        return 0.0
    try:
        d_qty = Decimal(repr(qty))
        d_step = Decimal(repr(step_value))
        units = (d_qty / d_step).to_integral_value(rounding="ROUND_FLOOR")
        floored = units * d_step
    except (InvalidOperation, OverflowError):
        return 0.0
    result = float(floored)
    return result if math.isfinite(result) and result > 0 else 0.0


def finite_values(values: Iterable[Any]) -> list[float]:
    """Finite floats from ``values`` (invalid entries dropped)."""
    out: list[float] = []
    for value in values:
        number = finite_float(value)
        if number is not None:
            out.append(number)
    return out


def all_finite(values: Iterable[Any]) -> bool:
    """True when every value is a finite number (and there is at least one)."""
    seen = False
    for value in values:
        seen = True
        if finite_float(value) is None:
            return False
    return seen


def safe_quantity(value: Any) -> float:
    """Return a tradeable quantity: finite and > 0, else ``0.0`` (no trade)."""
    number = positive_float(value)
    return number if number is not None else 0.0


def decimal_or_none(value: Any) -> Decimal | None:
    """Finite :class:`~decimal.Decimal` from ``value`` or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None
