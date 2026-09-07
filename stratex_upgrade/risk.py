from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from .models import InstrumentRules, Position, RiskCheck, RiskDecision, Side


@dataclass(slots=True)
class RiskLimits:
    max_risk_per_trade: Decimal = Decimal("0.005")
    max_total_exposure: Decimal = Decimal("0.05")
    max_single_asset_exposure: Decimal = Decimal("0.02")
    max_net_directional_exposure: Decimal = Decimal("0.04")
    max_open_positions: int = 5
    max_daily_loss: Decimal = Decimal("0.02")
    max_drawdown: Decimal = Decimal("0.05")
    max_order_notional: Decimal = Decimal(1000)
    max_slippage_bps: Decimal = Decimal(25)
    max_spread_bps: Decimal = Decimal(50)
    max_stale_market_age_seconds: float = 10.0
    max_consecutive_losses: int = 3
    cooldown_after_loss_seconds: float = 900.0
    allow_short: bool = True


@dataclass(slots=True)
class RiskState:
    equity: Decimal
    starting_equity: Decimal
    peak_equity: Decimal
    daily_start_equity: Decimal
    daily_realized_pnl: Decimal = Decimal(0)
    consecutive_losses: int = 0
    last_loss_ns: int | None = None
    trading_halted: bool = False
    halt_reason: str | None = None

    def drawdown(self) -> Decimal:
        if self.peak_equity <= 0:
            return Decimal(1)
        return max(Decimal(0), (self.peak_equity - self.equity) / self.peak_equity)

    def daily_loss(self) -> Decimal:
        if self.daily_start_equity <= 0:
            return Decimal(1)
        loss = max(Decimal(0), self.daily_start_equity - self.equity)
        return loss / self.daily_start_equity


class RiskManager:
    """Deterministic portfolio risk gate with atomic reserve/release semantics."""

    def __init__(self, state: RiskState, limits: RiskLimits | None = None):
        self.state = state
        self.limits = limits or RiskLimits()
        self._lock = threading.RLock()
        self._reserved_notional = Decimal(0)

    def snapshot(self) -> RiskState:
        with self._lock:
            return RiskState(
                equity=self.state.equity,
                starting_equity=self.state.starting_equity,
                peak_equity=self.state.peak_equity,
                daily_start_equity=self.state.daily_start_equity,
                daily_realized_pnl=self.state.daily_realized_pnl,
                consecutive_losses=self.state.consecutive_losses,
                last_loss_ns=self.state.last_loss_ns,
                trading_halted=self.state.trading_halted,
                halt_reason=self.state.halt_reason,
            )

    def update_equity(self, equity: Decimal) -> None:
        with self._lock:
            if equity <= 0:
                self.state.trading_halted = True
                self.state.halt_reason = "NON_POSITIVE_EQUITY"
                return
            self.state.equity = equity
            self.state.peak_equity = max(self.state.peak_equity, equity)
            if self.state.drawdown() >= self.limits.max_drawdown:
                self.state.trading_halted = True
                self.state.halt_reason = "MAX_DRAWDOWN"

    def record_realized(self, pnl: Decimal) -> None:
        with self._lock:
            self.state.daily_realized_pnl += pnl
            if pnl < 0:
                self.state.consecutive_losses += 1
                self.state.last_loss_ns = time.time_ns()
            elif pnl > 0:
                self.state.consecutive_losses = 0
            self.state.equity += pnl
            self.state.peak_equity = max(self.state.peak_equity, self.state.equity)
            if self.state.daily_loss() >= self.limits.max_daily_loss:
                self.state.trading_halted = True
                self.state.halt_reason = "MAX_DAILY_LOSS"
            if self.state.consecutive_losses >= self.limits.max_consecutive_losses:
                self.state.trading_halted = True
                self.state.halt_reason = "MAX_CONSECUTIVE_LOSSES"

    def reset_daily(self) -> None:
        with self._lock:
            self.state.daily_start_equity = self.state.equity
            self.state.daily_realized_pnl = Decimal(0)
            self.state.consecutive_losses = 0
            if self.state.halt_reason == "MAX_DAILY_LOSS":
                self.state.trading_halted = False
                self.state.halt_reason = None

    def reserve_notional(self, notional: Decimal) -> bool:
        with self._lock:
            if notional <= 0:
                return False
            limit = self.state.equity * self.limits.max_total_exposure
            if self._reserved_notional + notional > limit:
                return False
            self._reserved_notional += notional
            return True

    def release_notional(self, notional: Decimal) -> None:
        with self._lock:
            self._reserved_notional = max(Decimal(0), self._reserved_notional - max(notional, Decimal(0)))

    def reserved_notional(self) -> Decimal:
        with self._lock:
            return self._reserved_notional

    def calculate_qty(
        self,
        entry_price: Decimal,
        stop_price: Decimal,
        instrument: InstrumentRules,
        confidence: float | None = None,
    ) -> Decimal:
        with self._lock:
            if entry_price <= 0 or stop_price <= 0 or entry_price == stop_price:
                return Decimal(0)
            risk_fraction = self.limits.max_risk_per_trade
            if confidence is not None:
                confidence = max(0.0, min(1.0, confidence))
                risk_fraction *= Decimal(str(0.5 + 0.5 * confidence))
            risk_amount = self.state.equity * risk_fraction
            per_unit = abs(entry_price - stop_price)
            qty = risk_amount / per_unit
            exposure_cap = self.state.equity * self.limits.max_single_asset_exposure
            qty = min(qty, exposure_cap / entry_price, self.limits.max_order_notional / entry_price)
            if instrument.step_size > 0:
                qty = (qty // instrument.step_size) * instrument.step_size
            if qty < instrument.min_qty or qty * entry_price < instrument.min_notional:
                return Decimal(0)
            return qty

    def evaluate(
        self,
        *,
        side: Side,
        qty: Decimal,
        price: Decimal,
        instrument: InstrumentRules,
        positions: Iterable[Position],
        market_age_seconds: float,
        spread_bps: Decimal,
        estimated_slippage_bps: Decimal,
    ) -> RiskCheck:
        with self._lock:
            notional = qty * price
            if self.state.trading_halted:
                return RiskCheck(RiskDecision.REJECT, f"HALTED:{self.state.halt_reason}", qty, Decimal(0), notional, Decimal(0))
            if market_age_seconds > self.limits.max_stale_market_age_seconds:
                return RiskCheck(RiskDecision.REJECT, "STALE_MARKET", qty, Decimal(0), notional, Decimal(0))
            if spread_bps > self.limits.max_spread_bps:
                return RiskCheck(RiskDecision.REJECT, "SPREAD_TOO_WIDE", qty, Decimal(0), notional, Decimal(0))
            if estimated_slippage_bps > self.limits.max_slippage_bps:
                return RiskCheck(RiskDecision.REJECT, "SLIPPAGE_TOO_HIGH", qty, Decimal(0), notional, Decimal(0))
            if qty <= 0 or price <= 0:
                return RiskCheck(RiskDecision.REJECT, "INVALID_ORDER", qty, Decimal(0), notional, Decimal(0))
            try:
                instrument.validate_qty(qty)
                instrument.validate_price(price)
            except ValueError as exc:
                return RiskCheck(RiskDecision.REJECT, str(exc), qty, Decimal(0), notional, Decimal(0))
            positions = list(positions)
            if len(positions) >= self.limits.max_open_positions:
                return RiskCheck(RiskDecision.REJECT, "MAX_OPEN_POSITIONS", qty, Decimal(0), notional, Decimal(0))
            total_exposure = sum((p.quantity * p.avg_entry_price for p in positions), Decimal(0))
            max_total = self.state.equity * self.limits.max_total_exposure
            max_asset = self.state.equity * self.limits.max_single_asset_exposure
            asset_existing = sum(
                (p.quantity * p.avg_entry_price for p in positions if p.symbol == instrument.symbol),
                Decimal(0),
            )
            if total_exposure + notional + self._reserved_notional > max_total:
                return RiskCheck(RiskDecision.REJECT, "MAX_TOTAL_EXPOSURE", qty, max_total / price, notional, Decimal(0))
            if asset_existing + notional > max_asset:
                reduced = max(Decimal(0), (max_asset - asset_existing) / price)
                return RiskCheck(RiskDecision.REDUCE, "MAX_SINGLE_ASSET_EXPOSURE", reduced, reduced, reduced * price, Decimal(0))
            if self.state.consecutive_losses >= self.limits.max_consecutive_losses:
                return RiskCheck(RiskDecision.REJECT, "CONSECUTIVE_LOSS_LIMIT", qty, Decimal(0), notional, Decimal(0))
            if side == Side.SELL and not self.limits.allow_short:
                return RiskCheck(RiskDecision.REJECT, "SHORTS_DISABLED", qty, Decimal(0), notional, Decimal(0))
            risk_amount = qty * abs(price - (price * Decimal("0.99")))
            return RiskCheck(RiskDecision.APPROVE, "RISK_OK", qty, qty, notional, risk_amount)
