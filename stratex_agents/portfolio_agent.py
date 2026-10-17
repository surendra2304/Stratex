"""Venue-aware, read-only portfolio aggregation for Stratex.

This component deliberately does not assume that every venue's quantity×price
means the same notional (for example, MT5 CFD quantity is a lot count). It keeps
native symbols, tickets, currencies, and venue failures explicit. It never sends
orders and never converts currencies without an explicit conversion rate.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from exchanges.base_exchange import BaseExchange


class PortfolioSnapshotError(RuntimeError):
    """Portfolio data was inconsistent or unavailable."""


class _UnavailablePortfolioVenue:
    """Placeholder preserving the venue's failure in portfolio health reporting."""

    def __init__(self, venue: str, reason: str):
        self.venue = venue
        self.reason = reason

    def get_balance(self):
        raise PortfolioSnapshotError(f"{self.venue} account adapter unavailable ({self.reason})")

    def get_positions(self):
        raise PortfolioSnapshotError(f"{self.venue} account adapter unavailable ({self.reason})")


class PortfolioAgent:
    agent_id = "portfolio"

    @classmethod
    def from_environment(
        cls,
        *,
        currency_to_reporting_rate: dict[str, float] | None = None,
        mt5_adapter: Any | None = None,
    ) -> "PortfolioAgent":
        """Construct read-only adapters only for explicitly allowlisted demo venues."""
        allowed = {
            item.strip().lower()
            for item in os.getenv("STRATEX_ALLOWED_EXECUTION_VENUES", "binance").split(",")
            if item.strip()
        }
        if not allowed or not allowed.issubset({"binance", "mt5"}):
            raise ValueError("Portfolio venues must be an explicit subset of binance,mt5.")
        providers: dict[str, Any] = {}
        notional_calculators: dict[str, Callable[[Any], float | None]] = {}

        if "binance" in allowed:
            try:
                from .venue.binance_futures_portfolio_adapter import BinanceFuturesPortfolioAdapter
                providers["binance"] = BinanceFuturesPortfolioAdapter()
                notional_calculators["binance"] = lambda position: (
                    abs(float(position.quantity) * float(position.mark_price)), "USDT"
                )
            except Exception as exc:
                providers["binance"] = _UnavailablePortfolioVenue("binance", type(exc).__name__)

        if "mt5" in allowed:
            mt5_enabled = os.getenv("MT5_BRIDGE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
            if not mt5_enabled:
                providers["mt5"] = _UnavailablePortfolioVenue("mt5", "bridge_disabled")
            elif mt5_adapter is None:
                try:
                    from exchanges.mt5_bridge_adapter import MT5BridgeAdapter
                    mt5_adapter = MT5BridgeAdapter()
                except Exception as exc:
                    mt5_adapter = _UnavailablePortfolioVenue("mt5", type(exc).__name__)
            providers["mt5"] = mt5_adapter

        return cls(
            providers,
            currency_to_reporting_rate=currency_to_reporting_rate,
            notional_calculators=notional_calculators,
        )

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
        balance_total_equity = 0.0
        account_equity_total = 0.0
        account_equity_complete = bool(self.venues)
        account_equity_venues = 0
        reporting_equity = 0.0
        reporting_free_cash = 0.0
        converted_balance_total_complete = True
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

            # Prefer broker-reported account equity (includes unrealized PnL).
            # Never treat summed wallet balances as equivalent to margin equity.
            get_equity = getattr(venue, "get_equity", None)
            if not callable(get_equity):
                account_equity_complete = False
            else:
                try:
                    equity_rows = get_equity()
                    if not isinstance(equity_rows, dict) or not equity_rows:
                        raise PortfolioSnapshotError("venue account equity result is invalid")
                    for currency, raw_equity in equity_rows.items():
                        code = str(currency).upper()
                        value = float(raw_equity)
                        if not code or not value > 0 or value != value or abs(value) == float("inf"):
                            raise PortfolioSnapshotError(f"invalid account equity for {code}")
                        fx_rate = 1.0 if code == reporting_currency else self.fx.get(code)
                        if fx_rate is None:
                            account_equity_complete = False
                        else:
                            account_equity_total += value * fx_rate
                    account_equity_venues += 1
                except Exception:
                    account_equity_complete = False

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
                        converted_balance_total_complete = False
                        converted_free_complete = False
                    else:
                        balance_total_equity += total * fx_rate
                        reporting_free_cash += free * fx_rate
                report["balances"] = balance_rows
                balances_ok = True
            except Exception as exc:
                report["balance_error"] = type(exc).__name__
                converted_balance_total_complete = False
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
                    notional_currency = None
                    if calculator is not None:
                        try:
                            value = calculator(position)
                            if isinstance(value, dict):
                                raw_notional = value.get("value")
                                raw_currency = value.get("currency")
                            elif isinstance(value, (tuple, list)) and len(value) == 2:
                                raw_notional, raw_currency = value
                            else:
                                raw_notional, raw_currency = value, None
                            if raw_notional is not None:
                                parsed_notional = float(raw_notional)
                                parsed_currency = str(raw_currency or "").strip().upper()
                                if parsed_notional >= 0 and abs(parsed_notional) != float("inf") and parsed_currency:
                                    if parsed_currency == reporting_currency:
                                        notional = parsed_notional
                                        notional_currency = parsed_currency
                                    else:
                                        fx_rate = self.fx.get(parsed_currency)
                                        if fx_rate is not None:
                                            notional = parsed_notional * fx_rate
                                            notional_currency = reporting_currency
                                        else:
                                            notional = parsed_notional
                                            notional_currency = parsed_currency
                        except Exception:
                            notional = None
                            notional_currency = None
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
                        "notional_currency": notional_currency,
                        "notional_available": notional is not None and notional_currency is not None,
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
        account_equity_available = bool(self.venues) and account_equity_complete and account_equity_venues == len(self.venues)
        if account_equity_available:
            reporting_equity = account_equity_total
            equity_basis = "account_equity"
            currency_conversion_complete = True
        else:
            reporting_equity = balance_total_equity if self.venues and converted_balance_total_complete else 0.0
            equity_basis = "balance_total_estimate"
            currency_conversion_complete = bool(self.venues) and converted_balance_total_complete
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
            "reporting_equity": round(reporting_equity, 8) if venue_reports and (
                account_equity_available or converted_balance_total_complete
            ) else None,
            "equity_basis": equity_basis if venue_reports else "unavailable",
            "reporting_free_cash": round(reporting_free_cash, 8) if venue_reports and converted_free_complete else None,
            "currency_conversion_complete": currency_conversion_complete,
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
