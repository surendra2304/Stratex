"""market_data_quality.py — validation of OHLCV candles before they reach indicators.

Every live and research path builds candles from exchange payloads whose
fields arrive as strings. Without a single gate, malformed rows reached the
indicator/strategy path silently: ``NaN``/``inf`` prices, zero or negative
prices, ``high < low``, negative volume, duplicate timestamps with conflicting
values, out-of-order rows, and a stale feed. Strategies then evaluated the
wrong bar or produced signals from fabricated values.

:func:`sanitize_ohlcv` is that gate. It never invents data: invalid rows are
dropped (not repaired), exact duplicates are collapsed, conflicting
duplicates keep the latest revision and are reported, and the result carries
a :class:`CandleQualityReport` whose ``status`` tells callers whether the
frame may drive a decision:

* ``OK`` — nothing was wrong.
* ``DEGRADED`` — historical rows were dropped/collapsed or bars are missing,
  but the newest bar is valid; usable, with a warning.
* ``UNUSABLE`` — the newest bar was invalid (a decision would silently use
  an older bar), too large a share of rows was invalid, too few rows remain,
  or the feed is stale relative to ``now``. Callers must not trade on it.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close")
OK = "OK"
DEGRADED = "DEGRADED"
UNUSABLE = "UNUSABLE"

_INTERVAL_UNITS = {"s": "s", "m": "min", "h": "h", "d": "D", "w": "W"}

# Share of invalid rows above which the series as a whole is not trusted.
DEFAULT_MAX_INVALID_FRACTION = 0.05


@dataclass
class CandleQualityReport:
    rows_in: int = 0
    rows_out: int = 0
    dropped_non_finite: int = 0
    dropped_non_positive: int = 0
    dropped_inconsistent: int = 0
    dropped_negative_volume: int = 0
    dropped_bad_timestamp: int = 0
    exact_duplicates: int = 0
    conflicting_duplicates: int = 0
    out_of_order: int = 0
    gaps: int = 0
    last_row_dropped: bool = False
    stale_seconds: float | None = None
    status: str = OK
    reasons: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status != UNUSABLE

    @property
    def dropped_invalid(self) -> int:
        return (
            self.dropped_non_finite
            + self.dropped_non_positive
            + self.dropped_inconsistent
            + self.dropped_negative_volume
            + self.dropped_bad_timestamp
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["dropped_invalid"] = self.dropped_invalid
        return data

    def summary(self) -> str:
        parts = [f"{self.status}", f"rows {self.rows_in}->{self.rows_out}"]
        if self.reasons:
            parts.append("; ".join(self.reasons))
        return " | ".join(parts)


def interval_to_timedelta(interval: str | None) -> pd.Timedelta | None:
    """``"15m"`` → 15 minutes, ``"4h"`` → 4 hours, ``"1d"`` → 1 day; ``None`` if unknown."""
    if not interval or not isinstance(interval, str):
        return None
    text = interval.strip()
    if len(text) < 2:
        return None
    suffix = text[-1]
    if suffix == "M":  # months have no fixed length
        return None
    unit = _INTERVAL_UNITS.get(suffix.lower())
    if unit is None:
        return None
    try:
        count = int(text[:-1])
    except ValueError:
        return None
    if count <= 0:
        return None
    return pd.Timedelta(count, unit=unit)


def _to_utc_timestamps(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        converted = pd.to_datetime(series, errors="coerce")
    elif pd.api.types.is_numeric_dtype(series):
        # Exchange payloads use epoch milliseconds.
        converted = pd.to_datetime(series, unit="ms", errors="coerce")
    else:
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().all():
            converted = pd.to_datetime(numeric, unit="ms", errors="coerce")
        else:
            converted = pd.to_datetime(series, errors="coerce", utc=True)
    if getattr(converted.dt, "tz", None) is None:
        converted = converted.dt.tz_localize("UTC")
    else:
        converted = converted.dt.tz_convert("UTC")
    return converted


def sanitize_ohlcv(
    df: pd.DataFrame | None,
    *,
    interval: str | None = None,
    timestamp_col: str = "timestamp",
    min_rows: int = 1,
    max_invalid_fraction: float = DEFAULT_MAX_INVALID_FRACTION,
    now: pd.Timestamp | None = None,
    max_staleness_bars: float = 3.0,
    require_volume: bool = True,
) -> tuple[pd.DataFrame, CandleQualityReport]:
    """Validate and normalize an OHLCV frame. See the module docstring.

    The returned frame is sorted by ``timestamp_col`` (oldest first), has a
    fresh RangeIndex, numeric float OHLC(V) columns, and keeps the caller's
    timestamp dtype (naive stays naive, aware stays aware). Extra columns are
    preserved. ``now`` enables the staleness check (live paths only — never
    pass it for historical/backtest data).
    """
    report = CandleQualityReport()
    if df is None or len(df) == 0:
        report.status = UNUSABLE
        report.reasons.append("no rows")
        return (pd.DataFrame() if df is None else df.copy()), report

    missing = [col for col in (timestamp_col, *PRICE_COLUMNS) if col not in df.columns]
    if missing:
        report.rows_in = len(df)
        report.status = UNUSABLE
        report.reasons.append(f"missing columns {missing}")
        return df.iloc[0:0].copy(), report

    frame = df.copy()
    report.rows_in = len(frame)
    original_ts = frame[timestamp_col]
    ts_utc = _to_utc_timestamps(original_ts)
    position = np.arange(len(frame))

    numeric_cols = [*PRICE_COLUMNS] + (["volume"] if "volume" in frame.columns else [])
    for col in numeric_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype(float)

    prices = frame[list(PRICE_COLUMNS)].to_numpy(dtype=float)
    finite = np.isfinite(prices).all(axis=1)
    bad_ts = ts_utc.isna().to_numpy()
    if "volume" in frame.columns:
        volume = frame["volume"].to_numpy(dtype=float)
        volume_finite = np.isfinite(volume)
        finite &= volume_finite
        negative_volume = volume_finite & (volume < 0)
    else:
        negative_volume = np.zeros(len(frame), dtype=bool)
        if require_volume:
            report.reasons.append("no volume column")
    with np.errstate(invalid="ignore"):
        positive = (prices > 0).all(axis=1)
        o, h, low, c = prices[:, 0], prices[:, 1], prices[:, 2], prices[:, 3]
        consistent = (h >= low) & (h >= np.maximum(o, c)) & (low <= np.minimum(o, c))

    invalid_ts = bad_ts
    invalid_nonfinite = ~invalid_ts & ~finite
    invalid_nonpositive = ~invalid_ts & finite & ~positive
    invalid_negvol = ~invalid_ts & finite & positive & negative_volume
    invalid_inconsistent = ~invalid_ts & finite & positive & ~negative_volume & ~consistent
    invalid = invalid_ts | invalid_nonfinite | invalid_nonpositive | invalid_negvol | invalid_inconsistent

    report.dropped_bad_timestamp = int(invalid_ts.sum())
    report.dropped_non_finite = int(invalid_nonfinite.sum())
    report.dropped_non_positive = int(invalid_nonpositive.sum())
    report.dropped_negative_volume = int(invalid_negvol.sum())
    report.dropped_inconsistent = int(invalid_inconsistent.sum())

    # "Newest" is decided on the raw timestamps, before any sorting.
    if not bad_ts.all():
        newest_ts = ts_utc.max()
        newest_rows = (ts_utc == newest_ts).to_numpy()
        report.last_row_dropped = bool((newest_rows & invalid).any() and not (newest_rows & ~invalid).any())
    else:
        report.last_row_dropped = True

    frame = frame.loc[~invalid].copy()
    frame["_ts_utc"] = ts_utc[~invalid].to_numpy()
    frame["_pos"] = position[~invalid]

    # Out-of-order is judged in arrival order, ignoring exact repeats (a
    # re-sent identical bar is a duplicate, not a time-travel event).
    arrival = frame.drop_duplicates(subset=["_ts_utc", *numeric_cols], keep="first")["_ts_utc"]
    if len(arrival) > 1:
        report.out_of_order = int((arrival.diff().dt.total_seconds() < 0).sum())

    # Duplicates: identical rows collapse silently (pagination overlap);
    # differing rows keep the latest revision and are reported.
    if frame["_ts_utc"].duplicated().any():
        value_cols = numeric_cols
        dup_mask = frame["_ts_utc"].duplicated(keep=False)
        dup_groups = frame.loc[dup_mask].groupby("_ts_utc")
        for _, group in dup_groups:
            distinct = group[value_cols].drop_duplicates()
            if len(distinct) > 1:
                report.conflicting_duplicates += len(group) - 1
            else:
                report.exact_duplicates += len(group) - 1
        frame = frame.sort_values(["_ts_utc", "_pos"]).drop_duplicates("_ts_utc", keep="last")

    frame = frame.sort_values("_ts_utc", kind="stable")
    step = interval_to_timedelta(interval)
    if step is not None and len(frame) > 1:
        deltas = frame["_ts_utc"].diff().dropna()
        report.gaps = int((deltas > step * 1.5).sum())

    if now is not None and step is not None and len(frame):
        now_utc = pd.Timestamp(now)
        now_utc = now_utc.tz_localize("UTC") if now_utc.tzinfo is None else now_utc.tz_convert("UTC")
        newest_open = frame["_ts_utc"].iloc[-1]
        # A closed bar opened at T closes at T+step; it is stale once several
        # further bars should have closed. Bars far in the future are equally
        # untrustworthy (clock skew or a bad payload).
        stale = (now_utc - (newest_open + step)).total_seconds()
        report.stale_seconds = round(stale, 3)
        if stale > step.total_seconds() * max_staleness_bars:
            report.reasons.append(f"stale feed: newest bar closed {stale:.0f}s ago")
        elif (newest_open - now_utc).total_seconds() > step.total_seconds():
            report.reasons.append("newest bar is in the future")

    frame = frame.drop(columns=["_ts_utc", "_pos"]).reset_index(drop=True)
    report.rows_out = len(frame)

    issues = []
    if report.dropped_invalid:
        issues.append(f"{report.dropped_invalid} invalid row(s) dropped")
    if report.conflicting_duplicates:
        issues.append(f"{report.conflicting_duplicates} conflicting duplicate(s)")
    if report.out_of_order:
        issues.append(f"{report.out_of_order} out-of-order row(s)")
    if report.gaps:
        issues.append(f"{report.gaps} gap(s)")
    report.reasons = issues + report.reasons

    unusable = []
    if report.last_row_dropped:
        unusable.append("newest bar invalid")
    if report.rows_out < max(1, int(min_rows)):
        unusable.append(f"only {report.rows_out} valid row(s) (< {min_rows})")
    if report.rows_in and report.dropped_invalid / report.rows_in > max_invalid_fraction:
        unusable.append(f"invalid share {report.dropped_invalid / report.rows_in:.1%} > {max_invalid_fraction:.0%}")
    if any(reason.startswith(("stale feed", "newest bar is in the future")) for reason in report.reasons):
        unusable.append("feed not current")
    if unusable:
        report.status = UNUSABLE
        report.reasons = unusable + [r for r in report.reasons if r not in unusable]
    elif issues:
        report.status = DEGRADED
    return frame, report


def validate_kline_values(open_: Any, high: Any, low: Any, close: Any, volume: Any) -> str | None:
    """Reason a single streamed kline is unusable, or ``None`` when it is valid."""
    values = {}
    for name, raw in (("open", open_), ("high", high), ("low", low), ("close", close), ("volume", volume)):
        if raw is None or isinstance(raw, bool):
            return f"missing {name}"
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return f"non-numeric {name}"
        if not math.isfinite(value):
            return f"non-finite {name}"
        values[name] = value
    if min(values["open"], values["high"], values["low"], values["close"]) <= 0:
        return "non-positive price"
    if values["volume"] < 0:
        return "negative volume"
    if values["high"] < values["low"] or values["high"] < max(values["open"], values["close"]) \
            or values["low"] > min(values["open"], values["close"]):
        return "inconsistent high/low"
    return None
