"""Read-only Binance USDⓈ-M Futures account view for Stratex portfolio reporting.

This adapter exposes balances and positions only. It obtains its client through the
dedicated Binance venue agent, which pins the endpoint to current Demo Trading or
legacy Futures Testnet and refuses a live-money flag.
"""
from __future__ import annotations

import os
from typing import Any

from exchanges.base_exchange import UnifiedBalance, UnifiedPosition
from stratex_agents.venue.binance_futures_agent import BinanceFuturesAgent


class BinancePortfolioReadOnlyError(RuntimeError):
    """Binance account data could not be read in the configured demo environment."""


class BinanceFuturesPortfolioAdapter:
    venue = "binance"

    def __init__(self, client: Any | None = None, venue_agent: BinanceFuturesAgent | None = None):
        self.venue_agent = venue_agent or BinanceFuturesAgent()
        self.environment = "binance_demo" if self.venue_agent.demo_trading else "binance_testnet"
        self._client = client or self.venue_agent.create_client(
            mode="FUTURES",
            legacy_api_key=os.getenv("API_KEY", ""),
            legacy_secret_key=os.getenv("SECRET_KEY", ""),
        )
        expected = "demo_trading" if self.venue_agent.demo_trading else "legacy_futures_testnet"
        actual = getattr(self._client, "stratex_environment", None)
        if actual != expected:
            raise BinancePortfolioReadOnlyError(
                f"Binance account client environment is unverified or mismatched: expected {expected!r}, got {actual!r}"
            )

    def get_equity(self) -> dict[str, float]:
        """Return exchange-reported USDⓈ-M Futures account equity, including unrealized PnL."""
        try:
            account = self._client.futures_account()
        except Exception as exc:
            raise BinancePortfolioReadOnlyError("Binance demo Futures equity is unavailable.") from exc
        if not isinstance(account, dict):
            raise BinancePortfolioReadOnlyError("Binance returned an invalid Futures account payload.")
        raw = account.get("totalMarginBalance")
        if raw is None:
            raise BinancePortfolioReadOnlyError("Binance account payload omitted totalMarginBalance.")
        try:
            equity = float(raw)
        except (TypeError, ValueError) as exc:
            raise BinancePortfolioReadOnlyError("Binance account equity is invalid.") from exc
        if equity <= 0 or equity != equity or abs(equity) == float("inf"):
            raise BinancePortfolioReadOnlyError("Binance account equity must be finite and positive.")
        # Binance USDⓈ-M account equity is denominated in USDⓈ-M margin units (USDT).
        return {"USDT": equity}

    def get_balance(self) -> dict[str, UnifiedBalance]:
        """Read Futures wallet/available balances; an API error is not converted to zero."""
        try:
            rows = self._client.futures_account_balance()
        except Exception as exc:
            raise BinancePortfolioReadOnlyError("Binance demo Futures balances are unavailable.") from exc
        if not isinstance(rows, list):
            raise BinancePortfolioReadOnlyError("Binance returned an invalid Futures balance payload.")
        result: dict[str, UnifiedBalance] = {}
        for row in rows:
            code = str(row.get("asset", "")).upper()
            if not code:
                continue
            try:
                total = float(row.get("balance", 0) or 0)
                free = float(row.get("availableBalance", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise BinancePortfolioReadOnlyError(f"Invalid balance values for {code}.") from exc
            if any(value != value or abs(value) == float("inf") for value in (total, free)):
                raise BinancePortfolioReadOnlyError(f"Non-finite balance values for {code}.")
            if total != 0 or free != 0:
                used = max(0.0, total - free)
                result[code] = UnifiedBalance(currency=code, free=free, used=used, total=total)
        return result

    def get_positions(self) -> list[UnifiedPosition]:
        """Read USD-M Futures positions, preserving native symbol and Hedge Mode side."""
        try:
            rows = self._client.futures_position_information()
        except Exception as exc:
            raise BinancePortfolioReadOnlyError("Binance demo Futures positions are unavailable.") from exc
        if not isinstance(rows, list):
            raise BinancePortfolioReadOnlyError("Binance returned an invalid Futures position payload.")
        positions: list[UnifiedPosition] = []
        for row in rows:
            try:
                amount = float(row.get("positionAmt", 0) or 0)
                if amount == 0:
                    continue
                position_side = str(row.get("positionSide", "BOTH")).upper()
                side = position_side if position_side in {"LONG", "SHORT"} else ("LONG" if amount > 0 else "SHORT")
                quantity = abs(amount)
                entry = float(row.get("entryPrice", 0) or 0)
                mark = float(row.get("markPrice", 0) or 0)
                pnl = float(row.get("unRealizedProfit", 0) or 0)
                leverage = float(row.get("leverage", 1) or 1)
            except (TypeError, ValueError) as exc:
                raise BinancePortfolioReadOnlyError("Invalid position values in Binance Futures response.") from exc
            if any(
                value != value or abs(value) == float("inf")
                for value in (quantity, entry, mark, pnl, leverage)
            ):
                raise BinancePortfolioReadOnlyError("Non-finite position values in Binance Futures response.")
            positions.append(UnifiedPosition(
                symbol=str(row.get("symbol", "")),
                side=side,
                quantity=quantity,
                entry_price=entry,
                mark_price=mark,
                unrealized_pnl=pnl,
                leverage=leverage,
                exchange="binance",
                position_id=f"{row.get('symbol', '')}:{side}",
            ))
        return positions

    def health(self) -> dict[str, Any]:
        """Explicitly query account state and return sanitized environment health."""
        balances = self.get_balance()
        positions = self.get_positions()
        return {
            "venue": self.venue,
            "environment": self.environment,
            "status": "healthy",
            "balance_currency_count": len(balances),
            "open_position_count": len(positions),
            "execution_authority": False,
        }
