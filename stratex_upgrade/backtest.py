from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Bar:
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal(0)

    def validate(self) -> None:
        values = (self.open, self.high, self.low, self.close)
        if any(v <= 0 or not v.is_finite() for v in values):
            raise ValueError("OHLC must be finite and positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC relationship")


@dataclass(frozen=True, slots=True)
class BacktestOrder:
    timestamp: int
    side: str
    quantity: Decimal
    reference_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    strategy: str
    signal_id: str


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    signal_id: str
    strategy: str
    side: str
    entry_timestamp: int
    exit_timestamp: int
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    gross_pnl: Decimal
    fees: Decimal
    slippage: Decimal
    net_pnl: Decimal
    exit_reason: str


@dataclass(slots=True)
class BacktestConfig:
    initial_equity: Decimal = Decimal(10000)
    risk_per_trade: Decimal = Decimal("0.01")
    fee_bps: Decimal = Decimal(10)
    slippage_bps: Decimal = Decimal(5)
    long_only: bool = True
    conservative_intrabar: bool = True
    max_open_positions: int = 1
    allow_same_bar_reentry: bool = False


class EventDrivenBacktester:
    """No-lookahead simulator with explicit entry/exit sequencing and costs."""

    def __init__(self, bars: Sequence[Bar], config: BacktestConfig | None = None):
        self.bars = list(bars)
        self.config = config or BacktestConfig()
        for b in self.bars:
            b.validate()
        self.equity = self.config.initial_equity
        self.open_positions: list[dict[str, Any]] = []
        self.trades: list[BacktestTrade] = []

    def _position_qty(self, entry: Decimal, stop: Decimal | None) -> Decimal:
        if stop is None or entry == stop:
            return Decimal(0)
        risk = self.equity * self.config.risk_per_trade
        return risk / abs(entry - stop)

    def _fill(self, side: str, reference: Decimal) -> Decimal:
        slip = self.config.slippage_bps / Decimal(10000)
        return reference * (Decimal(1) + slip) if side == "BUY" else reference * (Decimal(1) - slip)

    def run(self, signal_fn: Callable[[list[Bar]], BacktestOrder | None]) -> list[BacktestTrade]:
        pending: BacktestOrder | None = None
        for idx, bar in enumerate(self.bars):
            if pending and len(self.open_positions) < self.config.max_open_positions:
                entry_ref = bar.open
                entry = self._fill(pending.side, entry_ref)
                qty = pending.quantity
                if qty <= 0:
                    qty = self._position_qty(entry, pending.stop_loss)
                if qty > 0:
                    fee = entry * qty * self.config.fee_bps / Decimal(10000)
                    self.equity -= fee
                    self.open_positions.append({
                        "order": pending,
                        "entry": entry,
                        "qty": qty,
                        "fee": fee,
                        "entry_ts": bar.timestamp,
                    })
            pending = None

            remaining = []
            for pos in self.open_positions:
                order = pos["order"]
                exit_ref = None
                reason = None
                sl = order.stop_loss
                tp = order.take_profit
                if order.side == "BUY":
                    sl_hit = sl is not None and bar.low <= sl
                    tp_hit = tp is not None and bar.high >= tp
                else:
                    sl_hit = sl is not None and bar.high >= sl
                    tp_hit = tp is not None and bar.low <= tp
                if sl_hit and tp_hit:
                    if self.config.conservative_intrabar:
                        exit_ref, reason = sl, "SL_HIT"
                    else:
                        exit_ref, reason = tp, "TP_HIT"
                elif sl_hit:
                    exit_ref, reason = sl, "SL_HIT"
                elif tp_hit:
                    exit_ref, reason = tp, "TP_HIT"
                if exit_ref is None:
                    remaining.append(pos)
                    continue
                exit_price = self._fill("SELL" if order.side == "BUY" else "BUY", Decimal(str(exit_ref)))
                qty = pos["qty"]
                exit_fee = exit_price * qty * self.config.fee_bps / Decimal(10000)
                if order.side == "BUY":
                    gross = (exit_price - pos["entry"]) * qty
                else:
                    gross = (pos["entry"] - exit_price) * qty
                slip_cost = abs(exit_price - Decimal(str(exit_ref))) * qty
                net = gross - pos["fee"] - exit_fee
                self.equity += gross - exit_fee
                self.trades.append(BacktestTrade(
                    signal_id=order.signal_id,
                    strategy=order.strategy,
                    side=order.side,
                    entry_timestamp=pos["entry_ts"],
                    exit_timestamp=bar.timestamp,
                    entry_price=pos["entry"],
                    exit_price=exit_price,
                    quantity=qty,
                    gross_pnl=gross,
                    fees=pos["fee"] + exit_fee,
                    slippage=slip_cost,
                    net_pnl=net,
                    exit_reason=reason or "UNKNOWN",
                ))

            self.open_positions = remaining

            if idx + 1 < len(self.bars) and len(self.open_positions) < self.config.max_open_positions:
                window = self.bars[: idx + 1]
                candidate = signal_fn(window)
                if candidate and self.config.long_only and candidate.side != "BUY":
                    candidate = None
                pending = candidate

        last = self.bars[-1] if self.bars else None
        if last:
            for pos in list(self.open_positions):
                order = pos["order"]
                exit_price = self._fill("SELL" if order.side == "BUY" else "BUY", last.close)
                qty = pos["qty"]
                exit_fee = exit_price * qty * self.config.fee_bps / Decimal(10000)
                gross = (
                    (exit_price - pos["entry"]) * qty
                    if order.side == "BUY"
                    else (pos["entry"] - exit_price) * qty
                )
                self.equity += gross - exit_fee
                self.trades.append(BacktestTrade(
                    signal_id=order.signal_id,
                    strategy=order.strategy,
                    side=order.side,
                    entry_timestamp=pos["entry_ts"],
                    exit_timestamp=last.timestamp,
                    entry_price=pos["entry"],
                    exit_price=exit_price,
                    quantity=qty,
                    gross_pnl=gross,
                    fees=pos["fee"] + exit_fee,
                    slippage=abs(exit_price - last.close) * qty,
                    net_pnl=gross - pos["fee"] - exit_fee,
                    exit_reason="TIME_EXIT",
                ))
        return self.trades


def validate_no_lookahead(features: Sequence[dict], signal_timestamps: Sequence[int]) -> None:
    for row, ts in zip(features, signal_timestamps):
        if int(row["timestamp"]) != int(ts):
            raise AssertionError("feature timestamp mismatch")


def bootstrap_trade_pnl(pnls: Sequence[float], iterations: int = 2000, seed: int = 7) -> list[float]:
    if not pnls:
        return []
    rng = random.Random(seed)
    out = []
    n = len(pnls)
    for _ in range(iterations):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        out.append(sum(sample))
    return out
