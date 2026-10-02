"""Chronological holdout validation for a single Stratex strategy.

Why this exists
---------------
The repository already has strong *machinery* for validation: a walk-forward
engine, a six-gate quantitative gauntlet, provenance-verified market data. What it
did not have is a single answer to the question that actually matters:

    "For the parameters that ship, what happened on data nobody looked at while
     they were being chosen?"

The factory search (`research/strategy_factory/mass_backtester.py`) ranks many
variations over one continuous dataset and keeps the best. That is in-sample
selection, and the winner of it is a *hypothesis*, not a validated strategy. This
module is the part that turns a hypothesis into a measurement.

What it does differently
------------------------
- **Chronological split, never shuffled.** The holdout is strictly later in time
  than everything used to choose parameters. Shuffling would leak the future.
- **Causal warm-up.** Indicators are computed once on the full series (EMA/ATR/RSI
  are recursive and need history), but no trade may *open* before the holdout
  boundary. A signal formed on the last in-sample bar is allowed to fill at the
  first holdout bar, which is what would really have happened.
- **Costs are charged on entry and exit, and the position is sized by risk**, so a
  drawdown percentage means something.
- **Refuses to render a verdict below a minimum trade count.** Three trades on the
  holdout is not evidence of anything, and a report that prints "expectancy:
  +$4,212.00" off three trades is worse than no report. The result is explicitly
  ``INSUFFICIENT_EVIDENCE`` and carries the numbers only as context.

The gate this is measured against is Gate 1 of ``evolution.validation_gauntlet``:
profit factor >= 1.30 with >= 100 trades. The minimum here is deliberately the
same 100, so "validated" means the same thing everywhere in this repository.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from data_client import MarketDataClient

#: Gate 1 of evolution/validation_gauntlet.py. Reused verbatim so that a verdict
#: from this module and a verdict from the gauntlet cannot disagree.
MIN_TRADES_FOR_VERDICT = 100
MIN_PROFIT_FACTOR = 1.30

DEFAULT_FEE_RATE = 0.0004
DEFAULT_SLIPPAGE_RATE = 0.0002

#: Cache layout, shared with the factory research cache convention.
CACHE_DIR = Path("data_cache/holdout")


@dataclass
class HoldoutResult:
    """The honest answer for one strategy on one symbol."""

    symbol: str
    timeframe: str
    strategy_id: str
    data_source: str
    data_sha256: str
    bars_total: int
    split_at: str
    in_sample_bars: int
    holdout_bars: int

    holdout_trades: int = 0
    expectancy_per_trade: float | None = None
    profit_factor: float | None = None
    win_rate: float | None = None
    max_drawdown_pct: float | None = None
    net_return_pct: float | None = None

    fee_rate: float = DEFAULT_FEE_RATE
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE
    risk_per_trade: float = 0.005

    verdict: str = "INSUFFICIENT_EVIDENCE"
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    generated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [
            f"{self.strategy_id} on {self.symbol} {self.timeframe}",
            f"  data: {self.data_source} sha256={self.data_sha256[:12]} ({self.bars_total} bars)",
            f"  split at {self.split_at}: {self.in_sample_bars} in-sample / {self.holdout_bars} holdout bars",
            f"  costs: fee {self.fee_rate * 10000:.1f} bps + slippage {self.slippage_rate * 10000:.1f} bps per side, "
            f"risk {self.risk_per_trade * 100:.2f}% of equity per trade",
        ]
        if self.holdout_trades:
            lines.append(
                f"  holdout: {self.holdout_trades} trades, "
                f"expectancy {self.expectancy_per_trade:+.4f}, "
                f"PF {self.profit_factor:.3f}, win rate {self.win_rate:.3f}, "
                f"max DD {self.max_drawdown_pct:.2f}%, net {self.net_return_pct:+.2f}%"
            )
        else:
            lines.append("  holdout: 0 trades — the strategy never signalled on unseen data")
        lines.append(f"  VERDICT: {self.verdict}")
        for reason in self.reasons:
            lines.append(f"    - {reason}")
        for note in self.notes:
            lines.append(f"    note: {note}")
        return "\n".join(lines)


# ── data ────────────────────────────────────────────────────────────────────


def load_real_candles(
    symbol: str,
    timeframe: str,
    start_str: str,
    client: MarketDataClient | None = None,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> tuple[pd.DataFrame, str, str]:
    """Return (dataframe, source, sha256) for verified real exchange candles.

    Mirrors the factory loader's discipline: a cache file is only trusted when a
    provenance sidecar with a matching SHA-256 sits beside it, and no synthetic
    data is ever generated as a fallback.
    """
    import hashlib

    cache_dir = cache_dir or CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{symbol}_{timeframe}.csv"
    provenance_file = cache_file.with_suffix(".csv.json")

    if cache_file.exists() and provenance_file.exists() and not refresh:
        metadata = json.loads(provenance_file.read_text(encoding="utf-8"))
        digest = hashlib.sha256(cache_file.read_bytes()).hexdigest()
        if metadata.get("sha256") != digest:
            raise RuntimeError(f"holdout cache hash mismatch for {cache_file}")
        if metadata.get("symbol") != symbol or metadata.get("timeframe") != timeframe:
            raise RuntimeError(f"holdout cache identity mismatch for {cache_file}")
        df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        if metadata.get("rows") != len(df):
            raise RuntimeError(f"holdout cache row-count mismatch for {cache_file}")
        return df, str(metadata.get("source", "UNKNOWN")), digest

    client = client or MarketDataClient()
    raw = client.futures_historical_klines(symbol, timeframe, start_str=start_str)
    if not raw:
        raise RuntimeError(
            f"real exchange candles unavailable for {symbol} {timeframe}; "
            "no substitute data was generated"
        )
    df = pd.DataFrame(
        raw,
        columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
        ],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()

    source = getattr(client, "data_source", None)
    if not source:
        raise RuntimeError("cannot record provenance without an identified exchange data source")
    payload = df.to_csv().encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    cache_file.write_bytes(payload)
    provenance_file.write_text(
        json.dumps(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "requested_start": start_str,
                "source": source,
                "fetched_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                "rows": len(df),
                "sha256": digest,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return df, str(source), digest


# ── simulation ─────────────────────────────────────────────────────────────


def _costs(rate: float, price: float) -> float:
    return price * rate


def simulate(
    df: pd.DataFrame,
    signal_fn: Callable[[pd.DataFrame], str],
    sl_atr_mult: float,
    tp_atr_mult: float,
    atr: pd.Series,
    holdout_start_index: int,
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
    risk_per_trade: float = 0.005,
    starting_equity: float = 10_000.0,
) -> dict[str, Any]:
    """Bar-by-bar simulation restricted to the holdout window.

    ``signal_fn`` receives a dataframe ending at the current bar and returns
    "BUY", "SELL" or "". It must be causal — anything it reads is by construction
    history up to and including the current bar.
    """
    equity = starting_equity
    peak = equity
    max_dd = 0.0
    wins: list[float] = []
    losses: list[float] = []
    returns: list[float] = []

    position: dict[str, Any] | None = None
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    atrs = atr.to_numpy()

    for i in range(holdout_start_index, len(df)):
        # 1. Manage an open position first: exits take priority over new entries.
        if position is not None:
            exited = False
            if position["side"] == "LONG":
                if lows[i] <= position["sl"]:
                    exit_price, reason = position["sl"], "SL"
                    exited = True
                elif highs[i] >= position["tp"]:
                    exit_price, reason = position["tp"], "TP"
                    exited = True
            else:
                if highs[i] >= position["sl"]:
                    exit_price, reason = position["sl"], "SL"
                    exited = True
                elif lows[i] <= position["tp"]:
                    exit_price, reason = position["tp"], "TP"
                    exited = True

            if exited:
                # Favour the stop when a single bar could hit both: assume the
                # worse fill, never the better one.
                if reason == "TP" and position["side"] == "LONG" and lows[i] <= position["sl"]:
                    exit_price, reason = position["sl"], "SL"
                direction = 1 if position["side"] == "LONG" else -1
                gross = (exit_price - position["entry"]) * direction
                entry_cost = _costs(fee_rate + slippage_rate, position["entry"])
                exit_cost = _costs(fee_rate + slippage_rate, exit_price)
                pnl = gross * position["units"] - entry_cost * position["units"] - exit_cost * position["units"]
                equity += pnl
                trade_return = pnl / (equity - pnl) if equity != pnl else 0.0
                returns.append(trade_return)
                (wins if pnl > 0 else losses).append(pnl)
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100.0 if peak else 0.0)
                position = None

        # 2. Otherwise consider a fresh entry, sized by risk against the stop.
        #
        # The signal is read from bars up to and including i, but the fill happens
        # at the OPEN of bar i+1. Entering at closes[i] would be trading on the
        # very print that produced the signal, which is a look-ahead the project's
        # own BACKTEST_ASSUMPTIONS explicitly forbid ("next_candle_open").
        if position is None and i + 1 < len(df) and atrs[i] and atrs[i] > 0:
            side = signal_fn(df.iloc[: i + 1])
            if side in ("BUY", "SELL"):
                entry = opens[i + 1]
                distance = sl_atr_mult * atrs[i]
                if distance > 0:
                    sl = entry - distance if side == "BUY" else entry + distance
                    tp = entry + tp_atr_mult * atrs[i] if side == "BUY" else entry - tp_atr_mult * atrs[i]
                    units = (equity * risk_per_trade) / distance
                    position = {
                        "side": side,
                        "entry": entry,
                        "sl": sl,
                        "tp": tp,
                        "units": units,
                    }

    # Any position still open at the end is closed at the last close, so the
    # reported equity is a realisable number rather than a mark-to-model one.
    if position is not None:
        exit_price = closes[-1]
        direction = 1 if position["side"] == "LONG" else -1
        gross = (exit_price - position["entry"]) * direction
        pnl = gross * position["units"] - (
            _costs(fee_rate + slippage_rate, position["entry"]) + _costs(fee_rate + slippage_rate, exit_price)
        ) * position["units"]
        equity += pnl
        (wins if pnl > 0 else losses).append(pnl)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100.0 if peak else 0.0)

    all_pnls = wins + losses
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(all_pnls),
        "win_rate": (len(wins) / len(all_pnls)) if all_pnls else None,
        "expectancy": (sum(all_pnls) / len(all_pnls)) if all_pnls else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else float("inf")),
        "max_drawdown_pct": max_dd,
        "net_return_pct": (equity - starting_equity) / starting_equity * 100.0,
        "final_equity": equity,
    }


def validate_holdout(
    symbol: str,
    timeframe: str,
    strategy_id: str,
    signal_fn: Callable[[pd.DataFrame], str],
    sl_atr_mult: float,
    tp_atr_mult: float,
    holdout_fraction: float = 0.35,
    start_str: str = "2023-01-01",
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage_rate: float = DEFAULT_SLIPPAGE_RATE,
    risk_per_trade: float = 0.005,
    min_trades: int = MIN_TRADES_FOR_VERDICT,
    refresh: bool = False,
    df: pd.DataFrame | None = None,
    atr: pd.Series | None = None,
    atr_fn: Callable[[pd.DataFrame], pd.Series] | None = None,
) -> HoldoutResult:
    """Run the strategy on real data and report only what the holdout can support.

    Pass ``df`` to reuse candles already loaded, and ``atr_fn`` to supply the
    strategy's own ATR so stop distances are the ones that would actually be
    placed. Defaulting ATR to a generic formula would measure a different strategy.
    """
    from datetime import datetime, timezone

    if df is None:
        df, source, digest = load_real_candles(symbol, timeframe, start_str, refresh=refresh)
    else:
        import hashlib

        source = "PRELOADED_VERIFIED_CANDLES"
        digest = hashlib.sha256(df.to_csv().encode("utf-8")).hexdigest()
    if len(df) < 500:
        raise RuntimeError(f"only {len(df)} bars available; not enough to split honestly")

    split_index = int(len(df) * (1.0 - holdout_fraction))
    if split_index < 200 or (len(df) - split_index) < 200:
        raise RuntimeError("dataset too small for a defensible train/holdout split")

    outcome = simulate(
        df,
        signal_fn=signal_fn,
        sl_atr_mult=sl_atr_mult,
        tp_atr_mult=tp_atr_mult,
        atr=(
            atr.reindex(df.index)
            if atr is not None
            else (atr_fn(df) if atr_fn is not None else _fallback_atr(df))
        ),
        holdout_start_index=split_index,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        risk_per_trade=risk_per_trade,
    )

    result = HoldoutResult(
        symbol=symbol,
        timeframe=timeframe,
        strategy_id=strategy_id,
        data_source=source,
        data_sha256=digest,
        bars_total=len(df),
        split_at=str(df.index[split_index]),
        in_sample_bars=split_index,
        holdout_bars=len(df) - split_index,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        risk_per_trade=risk_per_trade,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    result.holdout_trades = outcome["trades"]
    result.expectancy_per_trade = outcome["expectancy"]
    result.win_rate = outcome["win_rate"]
    result.max_drawdown_pct = outcome["max_drawdown_pct"]
    result.net_return_pct = outcome["net_return_pct"]
    pf = outcome["profit_factor"]
    result.profit_factor = None if pf is None or pf == float("inf") else pf

    reasons: list[str] = []
    if result.holdout_trades < min_trades:
        reasons.append(
            f"only {result.holdout_trades} holdout trades; the project's own Gate 1 "
            f"requires >= {min_trades} before a verdict is meaningful"
        )
    if result.profit_factor is None or result.profit_factor < MIN_PROFIT_FACTOR:
        shown = "undefined" if result.profit_factor is None else f"{result.profit_factor:.3f}"
        reasons.append(f"holdout profit factor {shown} does not clear Gate 1's {MIN_PROFIT_FACTOR:.2f}")

    result.reasons = reasons
    result.verdict = "FAILED_HOLDOUT" if reasons else "PASSED_HOLDOUT"
    if result.holdout_trades < min_trades:
        result.verdict = "INSUFFICIENT_EVIDENCE"
    result.notes.append(
        "Holdout bars were never used to select parameters. Indicator warm-up is "
        "causal: no trade may open before the split boundary."
    )
    result.notes.append(
        "Entries fill at the next candle's open, per BACKTEST_ASSUMPTIONS "
        "EXECUTION_MODEL=next_candle_open; no trade is taken on the print that "
        "produced its own signal."
    )
    return result


def _fallback_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()
