"""
exchanges/oanda_adapter.py — OANDA v20 REST API Adapter for Stratex.

Supports Forex, CFDs on Indices, Commodities, and Crypto.
Pure REST API — no gateway needed. Works on Linux/Oracle Cloud.
"""
from __future__ import annotations

import os
import time
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from exchanges.base_exchange import (
    BaseExchange,
    ExchangeCapabilities,
    UnifiedBalance,
    UnifiedOrderResult,
    UnifiedPosition,
    UnifiedTicker,
)
from logger import get_logger

logger = get_logger("oanda_adapter")


class OandaExchangeAdapter(BaseExchange):
    """
    OANDA v20 REST API Adapter.
    
    Supports: Forex, CFDs on Indices, Commodities, Crypto.
    Features: Real balance/equity/margin, native SL/TP, real candles.
    Cloud-native: Pure REST API, no gateway needed.
    """

    def __init__(
        self,
        api_key: str = "",
        account_id: str = "",
        practice: bool = True,
    ):
        capabilities = ExchangeCapabilities(
            supports_futures=True,
            supports_shorting=True,
            supports_margin=True,
            supports_limit_orders=True,
            supports_stop_orders=True,
            supports_websocket=True,
            max_leverage=50.0,
        )
        super().__init__("oanda", capabilities=capabilities)

        self.api_key = api_key or os.getenv("OANDA_API_KEY", "")
        self.account_id = account_id or os.getenv("OANDA_ACCOUNT_ID", "")
        self.practice = practice or os.getenv("OANDA_PRACTICE", "true").lower() in ("1", "true", "yes")

        # API endpoints
        if self.practice:
            self.base_url = "https://api-fxpractice.oanda.com/v3"
            self.stream_url = "https://stream-fxpractice.oanda.com/v3"
        else:
            self.base_url = "https://api-fxtrade.oanda.com/v3"
            self.stream_url = "https://stream-fxtrade.oanda.com/v3"

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        })

        self._instrument_cache: Optional[Dict] = None
        self._symbol_map: Dict[str, str] = {}  # unified -> oanda

    # ------------------------------------------------------------------------
    # Symbol normalization
    # ------------------------------------------------------------------------
    # OANDA instrument names differ from the unified tickers used elsewhere:
    # indices carry a currency suffix and some CFD names are entirely different.
    _INSTRUMENT_MAP = {
        "US30": "US30_USD",
        "US500": "SPX500_USD",
        "USTEC": "NAS100_USD",
        "US2000": "US2000_USD",
        "DE30": "DE30_EUR",
        "UK100": "UK100_GBP",
        "JP225": "JP225_USD",
        "USOIL": "WTICO_USD",
        "UKOIL": "BCO_USD",
        "NATGAS": "NATGAS_USD",
        "XAUUSD": "XAU_USD",
        "XAGUSD": "XAG_USD",
    }

    def normalize_symbol(self, symbol: str) -> str:
        """Convert OANDA symbol (EUR_USD) to unified (EURUSD)."""
        if not symbol:
            return symbol
        reverse = {v: k for k, v in self._INSTRUMENT_MAP.items()}
        if symbol.upper() in reverse:
            return reverse[symbol.upper()]
        return symbol.replace("_", "").replace("-", "").upper()

    def denormalize_symbol(self, unified_symbol: str) -> str:
        """Convert unified (EURUSD) to OANDA (EUR_USD)."""
        if not unified_symbol:
            return unified_symbol
        upper = unified_symbol.upper()
        if upper in self._INSTRUMENT_MAP:
            return self._INSTRUMENT_MAP[upper]
        # Known mappings for common pairs
        if len(upper) == 6 and upper.isalpha():
            return f"{upper[:3]}_{upper[3:]}"
        return upper

    # ------------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------------
    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[Dict]:
        """Make HTTP request with error handling."""
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.request(method, url, timeout=10, **kwargs)
            if resp.status_code >= 400:
                logger.warning(f"[OANDA] {method} {endpoint} failed: {resp.status_code} {resp.text}")
                return None
            return resp.json()
        except Exception as e:
            logger.warning(f"[OANDA] {method} {endpoint} error: {e}")
            return None

    def _get_account(self) -> Optional[Dict]:
        """Get account details including balance, margin, positions."""
        return self._request("GET", f"/accounts/{self.account_id}")

    # ------------------------------------------------------------------------
    # BaseExchange implementation
    # ------------------------------------------------------------------------
    def get_balance(self) -> Dict[str, UnifiedBalance]:
        """Get real account balance, equity, margin from OANDA."""
        if not self.api_key or not self.account_id:
            logger.warning("[OANDA] No credentials configured")
            return {}

        data = self._get_account()
        if not data or "account" not in data:
            return {}

        acc = data["account"]
        try:
            balance = float(acc.get("balance", "0"))
            nav = float(acc.get("NAV", "0"))  # Net Asset Value = equity
            margin_used = float(acc.get("marginUsed", "0"))
            margin_available = float(acc.get("marginAvailable", "0"))
            unrealized_pl = float(acc.get("unrealizedPL", "0"))

            return {
                "USD": UnifiedBalance(
                    currency="USD",
                    free=margin_available,
                    used=margin_used,
                    total=nav,  # equity = balance + unrealized PnL
                )
            }
        except Exception as e:
            logger.warning(f"[OANDA] Balance parse error: {e}")
            return {}

    def get_positions(self) -> List[UnifiedPosition]:
        """Get open positions from OANDA."""
        if not self.api_key or not self.account_id:
            return []

        data = self._get_account()
        if not data or "account" not in data:
            return []

        positions = []
        for pos in data["account"].get("positions", []):
            try:
                long_units = float(pos.get("long", {}).get("units", "0") or 0)
                short_units = float(pos.get("short", {}).get("units", "0") or 0)
                if long_units == 0 and short_units == 0:
                    continue

                if long_units > 0:
                    side = "LONG"
                    qty = long_units
                    avg_price = float(pos.get("long", {}).get("averagePrice", "0") or 0)
                else:
                    side = "SHORT"
                    qty = abs(short_units)
                    avg_price = float(pos.get("short", {}).get("averagePrice", "0") or 0)

                instrument = pos.get("instrument", "")
                mark_price = float(pos.get("long", {}).get("marginRate", "0") or 0)  # not mark
                # Get current price for mark
                current_price = self.get_current_price(self.normalize_symbol(instrument)) or avg_price

                positions.append(UnifiedPosition(
                    symbol=self.normalize_symbol(instrument),
                    side=side,
                    quantity=qty,
                    entry_price=avg_price,
                    mark_price=current_price,
                    unrealized_pnl=0.0,  # OANDA provides unrealizedPL per position separately
                    leverage=1.0 / float(pos.get("long", {}).get("marginRate", "1") or 1) if side == "LONG" else 1.0 / float(pos.get("short", {}).get("marginRate", "1") or 1),
                    exchange="oanda",
                ))
            except Exception as e:
                logger.warning(f"[OANDA] Position parse error: {e}")
                continue

        return positions

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol."""
        ticker = self.get_ticker(symbol)
        return ticker.last if ticker else None

    def get_ticker(self, symbol: str) -> UnifiedTicker:
        """Get current bid/ask/last for a symbol."""
        u_sym = self.normalize_symbol(symbol)
        oanda_sym = self.denormalize_symbol(u_sym)

        data = self._request("GET", f"/accounts/{self.account_id}/pricing", params={"instruments": oanda_sym})
        if data and "prices" in data and data["prices"]:
            p = data["prices"][0]
            bid = float(p.get("bids", [{}])[0].get("price", 0)) if p.get("bids") else 0
            ask = float(p.get("asks", [{}])[0].get("price", 0)) if p.get("asks") else 0
            last = (bid + ask) / 2 if bid and ask else 0
            return UnifiedTicker(symbol=u_sym, bid=bid, ask=ask, last=last, volume_24h=0.0)

        return UnifiedTicker(symbol=u_sym, bid=0.0, ask=0.0, last=0.0, volume_24h=0.0)

    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, List[List[float]]]:
        u_sym = self.normalize_symbol(symbol)
        oanda_sym = self.denormalize_symbol(u_sym)
        data = self._request("GET", f"/accounts/{self.account_id}/pricing", params={"instruments": oanda_sym})
        if data and "prices" in data and data["prices"]:
            p = data["prices"][0]
            bids = [[float(b["price"]), float(b.get("liquidity", 0))] for b in p.get("bids", [])[:limit]]
            asks = [[float(a["price"]), float(a.get("liquidity", 0))] for a in p.get("asks", [])[:limit]]
            return {"bids": bids, "asks": asks}
        return {"bids": [], "asks": []}

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> UnifiedOrderResult:
        """Place order with native SL/TP."""
        u_sym = self.normalize_symbol(symbol)
        oanda_sym = self.denormalize_symbol(u_sym)

        side = side.upper()
        if side not in ("BUY", "SELL", "LONG", "SHORT"):
            raise ValueError(f"Invalid side: {side}")
        oanda_side = "buy" if side in ("BUY", "LONG") else "sell"

        order_type = order_type.upper()
        if order_type == "MARKET":
            oanda_type = "MARKET"
        elif order_type == "LIMIT":
            oanda_type = "LIMIT"
        elif order_type == "STOP":
            oanda_type = "STOP"
        else:
            oanda_type = "MARKET"

        # Build order payload with native SL/TP
        order_data = {
            "order": {
                "instrument": oanda_sym,
                "units": str(int(quantity)) if oanda_side == "buy" else str(-int(quantity)),
                "type": oanda_type,
                "positionFill": "DEFAULT",
            }
        }

        if price and order_type in ("LIMIT", "STOP"):
            order_data["order"]["price"] = str(price)

        if stop_loss:
            order_data["order"]["stopLossOnFill"] = {"price": str(stop_loss)}
        if take_profit:
            order_data["order"]["takeProfitOnFill"] = {"price": str(take_profit)}

        data = self._request("POST", f"/accounts/{self.account_id}/orders", json=order_data)
        if not data or "orderFillTransaction" not in data:
            raise RuntimeError(f"[OANDA] Order failed: {data}")

        fill = data["orderFillTransaction"]
        fill_price = float(fill.get("price", price or 0))
        fill_qty = abs(float(fill.get("units", 0)))

        return UnifiedOrderResult(
            order_id=fill.get("id", f"OANDA_{int(time.time()*1000)}"),
            symbol=self.normalize_symbol(symbol),
            side=side,
            order_type=order_type,
            price=fill_price,
            quantity=fill_qty,
            status="FILLED" if fill.get("type") == "ORDER_FILL" else "PENDING",
            executed_qty=fill_qty,
            avg_price=fill_price,
            fee_paid=0.0,  # OANDA includes spread in price
            exchange="oanda",
        )

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        data = self._request("PUT", f"/accounts/{self.account_id}/orders/{order_id}/cancel")
        return data is not None

    def get_historical_data(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> List[Dict[str, Any]]:
        """Get real OANDA candles."""
        u_sym = self.normalize_symbol(symbol)
        oanda_sym = self.denormalize_symbol(u_sym)

        granularity_map = {
            "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
            "1h": "H1", "4h": "H4", "1d": "D",
        }
        granularity = granularity_map.get(timeframe, "M15")

        data = self._request("GET", f"/instruments/{oanda_sym}/candles", params={
            "granularity": granularity,
            "count": min(limit, 5000),
            "price": "M",  # Midpoint candles
        })

        if not data or "candles" not in data:
            return []

        candles = []
        for c in data["candles"]:
            if not c.get("complete", True):
                continue
            mid = c.get("mid", {})
            try:
                ts = datetime.fromisoformat(
                    str(c.get("time", "")).replace("Z", "+00:00")
                )
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                timestamp = int(ts.timestamp())
            except Exception:
                timestamp = int(time.time())
            candles.append({
                "timestamp": timestamp,
                "open": float(mid.get("o", 0)),
                "high": float(mid.get("h", 0)),
                "low": float(mid.get("l", 0)),
                "close": float(mid.get("c", 0)),
                "volume": int(c.get("volume", 0)),
            })
        return candles[-limit:]

    def get_fees(self, symbol: str) -> tuple[float, float]:
        # OANDA is spread-only, no commission
        return 0.0, 0.0

    def get_account_summary(self) -> Dict[str, Any]:
        """Get full account summary for risk engine."""
        data = self._get_account()
        if not data or "account" not in data:
            return {}
        acc = data["account"]
        return {
            "balance": float(acc.get("balance", 0)),
            "equity": float(acc.get("NAV", 0)),
            "margin_used": float(acc.get("marginUsed", 0)),
            "margin_available": float(acc.get("marginAvailable", 0)),
            "unrealized_pl": float(acc.get("unrealizedPL", 0)),
            "margin_rate": float(acc.get("marginRate", 0)),
        }


# Alias
OandaExchange = OandaExchangeAdapter