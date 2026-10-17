"""Venue-aware, read-only portfolio aggregation for Stratex.

This component deliberately does not assume that every venue's quantity×price
means the same notional (for example, MT5 CFD quantity is a lot count). It keeps
native symbols, tickets, currencies, and venue failures explicit. It never sends
orders and never converts currencies without an explicit conversion rate.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from exchanges.base_exchange import BaseExchange


class PortfolioSnapshotError(RuntimeError):
    """Portfolio data was inconsistent or unavailable."""


class PortfolioAgent:
    agent_id = "portfolio"

    def __init__(
        self,
        venues: dict[str, BaseExchange],
        *,
        currency_to_reporting_rate: dict[str, float] | None = None,
        notional_calculators: dict[str, Callable[[Any], float | None]] | None = None,
    ):
        self.venues = {key.strip().lower(): value for key, value in venues.items()}
        unsupported = set(self.venues) - {"binance", "mt5"}
        if unsupported:
            raise ValueError(f"Unsupported portfolio venues: {sorted(unsupported)}")
        self.fx = {
            str(currency).upper(): float(rate)
            for currency, rate in (currency_to_reporting_rate or {}).items()
            if rate is not None and float(rate) > 0
        }
        self.notional_calculators = {
            key.strip().lower(): value
            for key, value in (notional_calculators or {}).items()
        }

    def snapshot(self, reporting_currency: str = "USD") -> dict[str, Any]:
        """Return account balances/positions by venue without fabricating conversions."""
        reporting_currency = reporting_currency.strip().upper()
        if not reporting_currency:
            raise ValueError("reporting_currency must be a non-empty currency code")

        captured_at = time.time()
        venue_reports: dict[str, Any] = {}
        all_positions: list[dict[str, Any]] = []
        native_balance_totals: dict[str, float] = {}
        reporting_equity = 0.0
        reporting_free_cash = 0.0
        converted_equity_complete = True
        converted_free_complete = True

        for venue_name, venue in sorted(self.venues.items()):
            report: dict[str, Any] = {
                "venue": venue_name,
                "status": "degraded",
                "balances": [],
                "positions": [],
                "balance_error": None,
                "position_error": None,
            }
            balances_ok = False
            positions_ok = False
            try:
                balances = venue.get_balance()
                if not isinstance(balances, dict):
                    raise PortfolioSnapshotError("venue balance result is not a dictionary")
                balance_rows = []
                for currency, balance in balances.items():
                    code = str(currency).upper()
                    total = float(balance.total)
                    free = float(balance.free)
                    used = float(balance.used)
                    if any(value != value or abs(value) == float("inf") for value in (total, free, used)):
                        raise PortfolioSnapshotError(f"non-finite balance value for {code}")
                    balance_rows.append({
                        "currency": code,
                        "free": free,
                        "used": used,
                        "total": total,
                    })
                    native_balance_totals[code] = native_balance_totals.get(code, 0.0) + total

                    fx_rate = 1.0 if code == reporting_currency else self.fx.get(code)
                    if fx_rate is None:
                        converted_equity_complete = False
                        converted_free_complete = False
                    else:
                        reporting_equity += total * fx_rate
                        reporting_free_cash += free * fx_rate
                report["balances"] = balance_rows
                balances_ok = True
            except Exception as exc:
                report["balance_error"] = type(exc).__name__
                converted_equity_complete = False
                converted_free_complete = False

            try:
                positions = venue.get_positions()
                if not isinstance(positions, list):
                    raise PortfolioSnapshotError("venue position result is not a list")
                position_rows = []
                calculator = self.notional_calculators.get(venue_name)
                for position in positions:
                    quantity = float(position.quantity)
                    entry_price = float(position.entry_price)
                    mark_price = float(position.mark_price)
                    pnl = float(position.unrealized_pnl)
                    native_symbol = str(position.symbol)
                    side = str(position.side).upper()
                    if side not in {"LONG", "SHORT"}:
                        raise PortfolioSnapshotError(f"unknown position side {side!r}")
                    if any(
                        value != value or abs(value) == float("inf")
                        for value in (quantity, entry_price, mark_price, pnl)
                    ):
                        raise PortfolioSnapshotError(f"non-finite position values for {native_symbol}")
                    notional = None
                    if calculator is not None:
                        try:
                            value = calculator(position)
                            if value is not None and float(value) >= 0 and abs(float(value)) != float("inf"):
                                notional = float(value)
                        except Exception:
                            notional = None
                    row = {
                        "venue": venue_name,
                        "native_symbol": native_symbol,
                        "position_id": getattr(position, "position_id", None),
                        "side": side,
                        "quantity": quantity,
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "unrealized_pnl": pnl,
                        "notional": notional,
                        "notional_available": notional is not None,
                    }
                    position_rows.append(row)
                    all_positions.append(row)
                report["positions"] = position_rows
                positions_ok = True
            except Exception as exc:
                report["position_error"] = type(exc).__name__

            report["status"] = "healthy" if balances_ok and positions_ok else "degraded"
            venue_reports[venue_name] = report

        total_native_open_positions = len(all_positions)
        return {
            "agent_id": self.agent_id,
            "captured_at": captured_at,
            "reporting_currency": reporting_currency,
            "execution_authority": False,
            "status": (
                "healthy" if venue_reports and all(v["status"] == "healthy" for v in venue_reports.values())
                else "degraded" if venue_reports
                else "no_venues_configured"
            ),
            "reporting_equity": round(reporting_equity, 8) if converted_equity_complete else None,
            "reporting_free_cash": round(reporting_free_cash, 8) if converted_free_complete else None,
            "currency_conversion_complete": converted_equity_complete and converted_free_complete,
            "native_balance_totals": {
                key: round(value, 8) for key, value in sorted(native_balance_totals.items())
            },
            "open_position_count": total_native_open_positions,
            "positions": all_positions,
            "venues": venue_reports,
        }

    def health(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": "ready" if self.venues else "no_venues_configured",
            "venues": sorted(self.venues),
            "execution_authority": False,
        }
