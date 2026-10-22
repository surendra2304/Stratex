"""Regime-stratified validation: measure a strategy where it is actually tested.

Phase E2 follow-up. The first holdout ran on a single chronological window that
turned out to be a strong uptrend, which is the friendliest possible environment
for a trend-following strategy. Reporting an expectancy from that window alone
says very little, and a strategy that only works in a trend is not a strategy.

This module fixes the sampling, not the conclusion. It identifies drawdown
episodes *from the data itself* — peak-to-trough declines over a real window —
and runs the same simulation inside each one, so the strategy is measured in the
regime it is supposed to survive.

Two design choices worth stating:

* The windows are found by the data, not chosen by a human. Picking a drawdown by
  eye is how you end up testing the one that flatters the strategy.
* Every window is reported, including the ones the strategy fails. A regime
  breakdown that quietly omits its bad months is worse than no breakdown.

The verdict rule is unchanged from the ordinary holdout: below the minimum trade
count, no verdict is rendered, only ``INSUFFICIENT_EVIDENCE``. Drawdown windows
are short, so many will fall below it, and that is the honest answer rather than
a number computed from four trades.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from research.validation.holdout_validation import (
    MIN_PROFIT_FACTOR,
    MIN_TRADES_FOR_VERDICT,
    _fallback_atr,
    simulate,
)

#: A peak-to-trough decline of at least this much counts as a drawdown episode.
#: 25% is chosen to be unambiguously a bear phase rather than ordinary noise.
DRAWDOWN_THRESHOLD_PCT = 25.0

#: Episodes shorter than this are noise, not regimes.
MIN_DRAWDOWN_BARS = 24 * 7


@dataclass(frozen=True)
class DrawdownWindow:
    """One peak-to-trough decline, located in the data rather than by hand."""

    start: str
    trough: str
    end: str
    depth_pct: float
    bars: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def find_drawdown_windows(
    df: pd.DataFrame,
    threshold_pct: float = DRAWDOWN_THRESHOLD_PCT,
    min_bars: int = MIN_DRAWDOWN_BARS,
) -> list[DrawdownWindow]:
    """Locate every drawdown episode of at least ``threshold_pct`` in ``df``.

    Works on the running peak: where price falls ``threshold_pct`` or more from a
    prior high, the interval from that high to the eventual recovery is an
    episode. Episodes that never recover within the data are included to their
    last bar, which is the conservative choice — a drawdown still in progress is
    still a drawdown.
    """
    close = df["close"]
    if close.empty:
        return []

    # A drawdown is only "in" one when price is meaningfully below a prior high.
    below = close < close.cummax() * (1.0 - threshold_pct / 100.0)
    if not below.any():
        return []

    windows: list[DrawdownWindow] = []
    in_episode = False
    peak_index = None
    peak_value = float("-inf")
    trough_index = None
    trough_value = float("inf")

    for stamp, price in close.items():
        # Not currently in a drawdown: track the running peak.
        if not in_episode:
            if price > peak_value:
                peak_value = float(price)
                peak_index = stamp
            if peak_value > 0 and price <= peak_value * (1.0 - threshold_pct / 100.0):
                in_episode = True
                trough_value = float(price)
                trough_index = stamp
            continue

        # In an episode: track the trough until price recovers the prior peak.
        if price < trough_value:
            trough_value = float(price)
            trough_index = stamp
        if peak_value > 0 and price >= peak_value:
            if trough_index is not None and peak_index is not None:
                bars = int((trough_index - peak_index).total_seconds() // 3600) + 1
                if bars >= min_bars:
                    windows.append(
                        DrawdownWindow(
                            start=str(peak_index),
                            trough=str(trough_index),
                            end=str(stamp),
                            depth_pct=round((trough_value / peak_value - 1.0) * 100.0, 2),
                            bars=bars,
                        )
                    )
            in_episode = False
            peak_value = float(price)
            peak_index = stamp
            trough_value = float("inf")
            trough_index = None

    # An episode still open at the end of the data counts, measured to the last bar.
    if in_episode and trough_index is not None and peak_index is not None:
        bars = int((trough_index - peak_index).total_seconds() // 3600) + 1
        if bars >= min_bars:
            windows.append(
                DrawdownWindow(
                    start=str(peak_index),
                    trough=str(trough_index),
                    end=str(close.index[-1]),
                    depth_pct=round((trough_value / peak_value - 1.0) * 100.0, 2),
                    bars=bars,
                )
            )
    return windows


@dataclass
class RegimeResult:
    """How the strategy behaved inside one drawdown episode."""

    window: DrawdownWindow
    trades: int = 0
    expectancy: float | None = None
    profit_factor: float | None = None
    win_rate: float | None = None
    max_drawdown_pct: float | None = None
    net_return_pct: float | None = None
    verdict: str = "INSUFFICIENT_EVIDENCE"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": self.window.as_dict(),
            "trades": self.trades,
            "expectancy": self.expectancy,
            "profit_factor": self.profit_factor,
            "win_rate": self.win_rate,
            "max_drawdown_pct": self.max_drawdown_pct,
            "net_return_pct": self.net_return_pct,
            "verdict": self.verdict,
            "reasons": self.reasons,
        }

    def line(self) -> str:
        depth = f"{self.window.depth_pct:+.1f}%"
        span = f"{self.window.start[:10]}..{self.window.end[:10]}"
        if self.trades == 0:
            return f"  {span} {depth:>8}  0 trades — no signal fired | {self.verdict}"
        pf = "n/a" if self.profit_factor is None else f"{self.profit_factor:.3f}"
        wr = "n/a" if self.win_rate is None else f"{self.win_rate:.3f}"
        dd = "n/a" if self.max_drawdown_pct is None else f"{self.max_drawdown_pct:.2f}%"
        net = "n/a" if self.net_return_pct is None else f"{self.net_return_pct:+.2f}%"
        exp = "n/a" if self.expectancy is None else f"{self.expectancy:+.4f}"
        return (
            f"  {span} {depth:>8}  {self.trades:>4} trades  PF {pf:>6}  win {wr}  "
            f"DD {dd:>7}  net {net:>8}  exp {exp:>9} | {self.verdict}"
        )


def evaluate_window(
    df: pd.DataFrame,
    window: DrawdownWindow,
    signal_fn: Callable[[pd.DataFrame], str],
    sl_atr_mult: float,
    tp_atr_mult: float,
    atr: pd.Series,
    fee_rate: float,
    slippage_rate: float,
    risk_per_trade: float,
    min_trades: int = MIN_TRADES_FOR_VERDICT,
) -> RegimeResult:
    """Run the same simulation restricted to one drawdown episode.

    Indicator warm-up is taken from the bars *before* the window, because a real
    deployment would already have the moving averages warm when the decline began.
    The simulation is only permitted to open trades inside the window.
    """
    start = pd.Timestamp(window.start)
    end = pd.Timestamp(window.end)

    # Start the simulation at the window so no trade opens before it.
    first_index = int(df.index.searchsorted(start))
    # Leave a warm-up margin of 200 bars so EMA/ATR are settled at the open.
    sim_start = max(0, first_index - 200)
    sliced = df.iloc[sim_start:]
    # And stop it at the end of the episode. Without this the simulation runs on
    # to the end of the dataset, every window overlaps every later one, and the
    # statistics converge on the same number for all of them.
    last_index = int(df.index.searchsorted(end, side="right")) - 1

    outcome = simulate(
        sliced,
        signal_fn=signal_fn,
        sl_atr_mult=sl_atr_mult,
        tp_atr_mult=tp_atr_mult,
        atr=atr.reindex(sliced.index) if atr is not None else _fallback_atr(sliced),
        holdout_start_index=first_index - sim_start,
        holdout_end_index=last_index - sim_start,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        risk_per_trade=risk_per_trade,
    )

    result = RegimeResult(window=window)
    result.trades = outcome["trades"]
    result.expectancy = outcome["expectancy"]
    result.win_rate = outcome["win_rate"]
    result.max_drawdown_pct = outcome["max_drawdown_pct"]
    result.net_return_pct = outcome["net_return_pct"]
    pf = outcome["profit_factor"]
    result.profit_factor = None if pf is None or pf == float("inf") else pf

    reasons: list[str] = []
    if result.trades < min_trades:
        reasons.append(
            f"{result.trades} trades in the episode; a verdict needs >= {min_trades}"
        )
    if result.profit_factor is None or result.profit_factor < MIN_PROFIT_FACTOR:
        shown = "undefined" if result.profit_factor is None else f"{result.profit_factor:.3f}"
        reasons.append(f"profit factor {shown} does not clear {MIN_PROFIT_FACTOR:.2f}")

    result.reasons = reasons
    if result.trades < min_trades:
        result.verdict = "INSUFFICIENT_EVIDENCE"
    else:
        result.verdict = "FAILED_DRAWDOWN" if reasons else "SURVIVED_DRAWDOWN"
    return result


def evaluate_regimes(
    df: pd.DataFrame,
    signal_fn: Callable[[pd.DataFrame], str],
    sl_atr_mult: float,
    tp_atr_mult: float,
    atr: pd.Series,
    fee_rate: float,
    slippage_rate: float,
    risk_per_trade: float,
    threshold_pct: float = DRAWDOWN_THRESHOLD_PCT,
    min_bars: int = MIN_DRAWDOWN_BARS,
    min_trades: int = MIN_TRADES_FOR_VERDICT,
) -> dict[str, Any]:
    """Evaluate every drawdown episode found in the data. Nothing is filtered out."""
    windows = find_drawdown_windows(df, threshold_pct=threshold_pct, min_bars=min_bars)
    results = [
        evaluate_window(
            df=df,
            window=window,
            signal_fn=signal_fn,
            sl_atr_mult=sl_atr_mult,
            tp_atr_mult=tp_atr_mult,
            atr=atr,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            risk_per_trade=risk_per_trade,
            min_trades=min_trades,
        )
        for window in windows
    ]

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": None,
        "timeframe": None,
        "bars_total": len(df),
        "range": [str(df.index[0]), str(df.index[-1])],
        "drawdown_threshold_pct": threshold_pct,
        "episodes_found": len(results),
        "episodes_with_verdict": sum(
            1 for r in results if r.verdict in {"SURVIVED_DRAWDOWN", "FAILED_DRAWDOWN"}
        ),
        "episodes_survived": sum(1 for r in results if r.verdict == "SURVIVED_DRAWDOWN"),
        "episodes_failed": sum(1 for r in results if r.verdict == "FAILED_DRAWDOWN"),
        "results": [r.as_dict() for r in results],
    }

    if not results:
        summary["regime_verdict"] = "NO_DRAWDOWNS_IN_DATA"
    elif summary["episodes_with_verdict"] == 0:
        summary["regime_verdict"] = "INSUFFICIENT_EVIDENCE"
    else:
        summary["regime_verdict"] = (
            "SURVIVED_ALL_MEASURED" if summary["episodes_failed"] == 0 else "FAILED_A_DRAWDOWN"
        )
    return summary


def write_report(summary: dict[str, Any], directory: str | Path) -> Path:
    """Persist a dated report so a verdict can be re-read long after the run."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    symbol = summary.get("symbol") or "UNKNOWN"
    timeframe = summary.get("timeframe") or "unknown"
    path = directory / f"regime_report_{symbol}_{timeframe}_{stamp}.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return path
