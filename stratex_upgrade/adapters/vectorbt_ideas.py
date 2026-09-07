"""Vectorized research helpers: explicit fees, slippage and stop semantics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SignalCosts:
    fees: Decimal
    slippage: Decimal


def apply_round_trip_cost(entry: Decimal, exit: Decimal, qty: Decimal, fee_bps: Decimal, slippage_bps: Decimal, side: str) -> tuple[Decimal, SignalCosts]:
    slip = slippage_bps / Decimal(10000)
    entry_exec = entry * (Decimal(1) + slip if side.upper() == "BUY" else Decimal(1) - slip)
    exit_side = "SELL" if side.upper() == "BUY" else "BUY"
    exit_exec = exit * (Decimal(1) + slip if exit_side == "BUY" else Decimal(1) - slip)
    fee = (entry_exec * qty + exit_exec * qty) * fee_bps / Decimal(10000)
    slip_cost = abs(entry_exec - entry) * qty + abs(exit_exec - exit) * qty
    return fee, SignalCosts(fee, slip_cost)
