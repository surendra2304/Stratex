"""
exchanges/binance_futures_adapter.py — Binance USD-M Futures Adapter (Testnet/Live).

Features:
- Real balance/equity from futures account
- Real positions with unrealized PnL
- Native SL/TP on orders
- Real candles from Binance API (no Yahoo)
- Testnet/Live switchable
- Cloud-native: Pure REST, no gateway needed
"""
from __future__ import annotations

import os
import time
import hmac
import hashlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, quote

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

logger = get_logger("binance_futures_adapter")


class BinanceFuturesAdapter(BaseExchange):
    """
    Binance USD-M Futures Adapter (Testnet/Live).
    
    Features:
    - Real balance/equity from futures account
    - Real positions with unrealized PnL
    - Native SL/TP on orders (bracket orders)
    - Real candles from Binance API
    - Testnet/Live switchable
    - Cloud-native: Pure REST, no gateway
    """

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        testnet: bool = True,
    ):
        capabilities = ExchangeCapabilities(
            supports_futures=True,
            supports_shorting=True,
            supports_margin=True,
            supports_limit_orders=True,
            supports_stop_orders=True,
            supports_websocket=True,
            max_leverage=125.0,
        )
        super().__init__("binance_futures", capabilities=capabilities)

        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.secret_key = secret_key or os.getenv("BINANCE_API_SECRET", "")

        # Environment: BINANCE_ENV=demo|testnet|live (demo.binance.com is a
        # separate host from the legacy testnet, and keys are not interchangeable)
        env = (os.getenv("BINANCE_ENV", "") or "").strip().lower()
        if env in ("live", "prod", "production", "real"):
            self.environment = "live"
        elif env in ("demo", "demo_trading", "demotrading"):
            self.environment = "demo"
        elif env == "testnet":
            self.environment = "testnet"
        else:
            self.environment = "testnet" if testnet else "live"

        self.testnet = self.environment != "live"

        # API endpoints
        if self.environment == "live":
            self.base_url = "https://fapi.binance.com"
        elif self.environment == "demo":
            self.base_url = "https://demo-fapi.binance.com"
        else:
            self.base_url = "https://testnet.binancefuture.com"

        logger.info(f"[BINANCE_FUTURES] Environment: {self.environment} ({self.base_url})")

        self.session = requests.Session()
        self.session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        })

        # Key type: hmac (system generated) | ed25519 | rsa (self generated)
        self.key_type = (os.getenv("BINANCE_KEY_TYPE", "hmac") or "hmac").strip().lower()
        self.private_key_path = os.getenv("BINANCE_PRIVATE_KEY_PATH", "")
        self._private_key = None
        if self.key_type in ("ed25519", "rsa") and self.private_key_path:
            self._private_key = self._load_private_key(self.private_key_path)

        # Time synchronization offset (milliseconds)
        self._time_offset = 0
        self._sync_time()

        # Position mode: one-way (BOTH) is what this adapter assumes. Accounts in
        # hedge mode reject orders without positionSide (-4061).
        self._hedge_mode = False
        if self._has_credentials():
            self._ensure_position_mode()

        self._symbol_cache: Optional[Dict] = None
        self._exchange_info: Optional[Dict] = None
        self._leverage_set: set = set()
        self.target_leverage = int(os.getenv("BINANCE_LEVERAGE", "3"))

    def _ensure_position_mode(self) -> None:
        """Prefer one-way mode; fall back to tagging orders with positionSide."""
        try:
            data = self._get("/fapi/v1/positionSide/dual", signed=True)
            if not data:
                return
            self._hedge_mode = bool(data.get("dualSidePosition"))
            if not self._hedge_mode:
                logger.info("[BINANCE_FUTURES] Position mode: one-way")
                return

            res = self._post(
                "/fapi/v1/positionSide/dual",
                signed=True,
                params={"dualSidePosition": "false"},
            )
            if res is not None:
                self._hedge_mode = False
                logger.info("[BINANCE_FUTURES] Switched account to one-way position mode")
            else:
                logger.info("[BINANCE_FUTURES] Position mode: hedge (sending positionSide on orders)")
        except Exception as e:
            logger.warning(f"[BINANCE_FUTURES] Position mode check failed: {e}")

    def _sync_time(self) -> None:
        """Synchronize local time with Binance server time."""
        try:
            data = self._get("/fapi/v1/time")
            if data and "serverTime" in data:
                server_time = data["serverTime"]
                local_time = int(time.time() * 1000)
                self._time_offset = server_time - local_time
                logger.info(f"[BINANCE_FUTURES] Time synced: offset={self._time_offset}ms")
            else:
                logger.warning("[BINANCE_FUTURES] Failed to sync time with server")
        except Exception as e:
            logger.warning(f"[BINANCE_FUTURES] Time sync failed: {e}")

    # ------------------------------------------------------------------------
    # Symbol normalization
    # ------------------------------------------------------------------------
    def normalize_symbol(self, symbol: str) -> str:
        """Convert Binance symbol (BTCUSDT) to unified (BTCUSDT)."""
        return symbol.upper().replace("_", "").replace("-", "")

    def denormalize_symbol(self, unified_symbol: str) -> str:
        """Convert unified to Binance format (already same format)."""
        return unified_symbol.upper()

    # ------------------------------------------------------------------------
    # HTTP helpers with signature
    # ------------------------------------------------------------------------
    def _has_credentials(self) -> bool:
        """True when the adapter has everything needed for signed requests."""
        if not self.api_key:
            return False
        if self.key_type in ("ed25519", "rsa"):
            return self._private_key is not None
        return bool(self.secret_key)

    def _load_private_key(self, path: str):
        """Load an Ed25519 or RSA private key from a PEM file."""
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            with open(path, "rb") as fh:
                pem = fh.read()
            key = load_pem_private_key(pem, password=None)
            logger.info(f"[BINANCE_FUTURES] Loaded {self.key_type} private key from {path}")
            return key
        except Exception as e:
            logger.error(f"[BINANCE_FUTURES] Failed to load private key {path}: {e}")
            return None

    def _sign_params(self, params: Dict[str, Any]) -> str:
        """Sign the exact query string that will be sent to Binance.

        HMAC  : hex digest of HMAC-SHA256(secret, query)
        Ed25519: base64 of Ed25519(private_key, query)
        RSA   : base64 of RSA-PKCS1v15-SHA256(private_key, query)
        """
        query_string = urlencode(params, doseq=True)

        if self.key_type in ("ed25519", "rsa") and self._private_key is not None:
            import base64

            if self.key_type == "ed25519":
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

                raw = self._private_key.sign(query_string.encode("utf-8"))
            else:
                from cryptography.hazmat.primitives import hashes
                from cryptography.hazmat.primitives.asymmetric import padding

                raw = self._private_key.sign(
                    query_string.encode("utf-8"),
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )
            return base64.b64encode(raw).decode("ascii")

        return hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        endpoint: str,
        signed: bool = False,
        **kwargs
    ) -> Optional[Dict]:
        """Make HTTP request, signing the exact query string that is sent."""
        url = f"{self.base_url}{endpoint}"
        params = dict(kwargs.get("params") or {})
        data = kwargs.get("data")

        headers = kwargs.get("headers") or {}
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        if signed:
            if not self.api_key:
                logger.warning("[BINANCE_FUTURES] No API key for signed request")
                return None
            if self.key_type in ("ed25519", "rsa"):
                if self._private_key is None:
                    logger.error("[BINANCE_FUTURES] key_type=%s but no private key loaded", self.key_type)
                    return None
            elif not self.secret_key:
                logger.warning("[BINANCE_FUTURES] No API secret for signed request")
                return None

            params["timestamp"] = int(time.time() * 1000) + self._time_offset
            params["recvWindow"] = 5000

            # Build the query string once, sign *that* string, then append the
            # signature. Any re-serialisation (sorting, requests' encoder) would
            # change the byte order and invalidate the signature (-1022).
            query_string = urlencode(params, doseq=True)
            signature = self._sign_params(dict(params))
            # Ed25519/RSA signatures are base64 and may contain '+', '/' and '='
            # which must be percent-encoded or the server decodes them wrongly.
            url = f"{url}?{query_string}&signature={quote(signature, safe='')}"
            params = None

        try:
            resp = self.session.request(
                method,
                url,
                params=params if params else None,
                data=data,
                headers=headers,
                timeout=10,
            )
            if resp.status_code >= 400:
                logger.warning(f"[BINANCE_FUTURES] {method} {endpoint} failed: {resp.status_code} {resp.text}")
                return None
            return resp.json()
        except Exception as e:
            logger.warning(f"[BINANCE_FUTURES] {method} {endpoint} error: {e}")
            return None

    def _get(self, endpoint: str, signed: bool = False, **kwargs) -> Optional[Dict]:
        return self._request("GET", endpoint, signed=signed, **kwargs)

    def _post(self, endpoint: str, signed: bool = False, **kwargs) -> Optional[Dict]:
        return self._request("POST", endpoint, signed=signed, **kwargs)

    def _delete(self, endpoint: str, signed: bool = False, **kwargs) -> Optional[Dict]:
        return self._request("DELETE", endpoint, signed=signed, **kwargs)

    # ------------------------------------------------------------------------
    # Symbol handling
    # ------------------------------------------------------------------------
    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("_", "").replace("-", "")

    def denormalize_symbol(self, unified_symbol: str) -> str:
        return unified_symbol.upper()

    # ------------------------------------------------------------------------
    # BaseExchange implementation
    # ------------------------------------------------------------------------
    def get_balance(self) -> Dict[str, UnifiedBalance]:
        """Get real futures account balance."""
        if not self._has_credentials():
            logger.warning("[BINANCE_FUTURES] No credentials configured")
            return {}

        data = self._get("/fapi/v2/balance", signed=True)
        if not data:
            return {}

        res = {}
        for b in data:
            try:
                asset = b.get("asset")
                total = float(b.get("balance", 0) or 0)
                free = float(
                    b.get("availableBalance")
                    or b.get("withdrawAvailable")
                    or 0
                )
                if total > 0:
                    res[asset] = UnifiedBalance(asset, free=free, used=max(total - free, 0.0), total=total)
            except Exception:
                continue
        return res

    def _parse_positions(self, data: Any) -> List[UnifiedPosition]:
        """Turn a /fapi/v2/positionRisk payload into unified positions."""
        positions = []
        for p in data or []:
            try:
                amt = float(p.get("positionAmt", 0) or 0)
                if amt == 0:
                    continue

                entry = float(p.get("entryPrice", 0) or 0)
                mark = float(p.get("markPrice", 0) or 0)
                u_pnl = float(p.get("unRealizedProfit", 0) or 0)
                lev = float(p.get("leverage", 1) or 1)
                symbol = p.get("symbol", "")

                positions.append(UnifiedPosition(
                    symbol=self.normalize_symbol(symbol),
                    side="LONG" if amt > 0 else "SHORT",
                    quantity=abs(amt),
                    entry_price=entry,
                    mark_price=mark,
                    unrealized_pnl=u_pnl,
                    leverage=lev,
                    exchange="binance_futures",
                ))
            except Exception as e:
                logger.warning(f"[BINANCE_FUTURES] Position parse error: {e}")
                continue

        return positions

    def get_positions(self) -> List[UnifiedPosition]:
        """Get open futures positions with unrealized PnL."""
        if not self._has_credentials():
            return []

        data = self._get("/fapi/v2/positionRisk", signed=True)
        if not data:
            return []

        return self._parse_positions(data)

    def get_positions_or_none(self) -> Optional[List[UnifiedPosition]]:
        """Positions, or None when the state is unknown (no creds / failed request).

        An empty list means the account is genuinely flat.
        """
        if not self._has_credentials():
            logger.warning("[BINANCE_FUTURES] No credentials configured")
            return None

        data = self._get("/fapi/v2/positionRisk", signed=True)
        if data is None:
            logger.warning("[BINANCE_FUTURES] positionRisk request failed; positions unknown")
            return None

        return self._parse_positions(data)

    def get_user_trades(
        self,
        symbol: str,
        start_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Account trade history for a symbol (/fapi/v1/userTrades)."""
        params: Dict[str, Any] = {
            "symbol": self.denormalize_symbol(symbol),
            "limit": max(1, min(int(limit), 1000)),
        }
        if start_ms is not None:
            params["startTime"] = int(start_ms)

        data = self._get("/fapi/v1/userTrades", signed=True, params=params)
        if not isinstance(data, list):
            return []
        return data

    def get_realized_pnl(self, symbol: str, since_ms: Optional[int] = None) -> Dict[str, Any]:
        """Sum realised PnL and commission from the account's own trade history."""
        zeros: Dict[str, Any] = {
            "gross": 0.0,
            "fees": 0.0,
            "net": 0.0,
            "trades": 0,
            "last_price": 0.0,
            "last_time": 0,
        }
        try:
            trades = self.get_user_trades(symbol, start_ms=since_ms)

            gross = 0.0
            fees = 0.0
            for t in trades:
                try:
                    gross += float(t.get("realizedPnl", 0) or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    fees += float(t.get("commission", 0) or 0)
                except (TypeError, ValueError):
                    pass

            last_price = 0.0
            last_time = 0
            if trades:
                last = trades[-1] or {}
                try:
                    last_price = float(last.get("price", 0) or 0)
                except (TypeError, ValueError):
                    last_price = 0.0
                try:
                    last_time = int(last.get("time", 0) or 0)
                except (TypeError, ValueError):
                    last_time = 0

            return {
                "gross": gross,
                "fees": fees,
                "net": gross - fees,
                "trades": len(trades),
                "last_price": last_price,
                "last_time": last_time,
            }
        except Exception as e:
            logger.warning(f"[BINANCE_FUTURES] get_realized_pnl failed: {e}")
            return dict(zeros, error=str(e))

    def get_ticker(self, symbol: str) -> UnifiedTicker:
        u_sym = self.normalize_symbol(symbol)
        data = self._get("/fapi/v1/ticker/24hr", params={"symbol": self.denormalize_symbol(u_sym)})
        if data:
            last = float(data.get("lastPrice", 0) or 0)
            bid = float(data.get("bidPrice", 0) or 0) or last
            ask = float(data.get("askPrice", 0) or 0) or last
            vol = float(data.get("volume", 0) or 0)
            return UnifiedTicker(symbol=symbol, bid=bid, ask=ask, last=last, volume_24h=vol)

        return UnifiedTicker(symbol=symbol, bid=0.0, ask=0.0, last=0.0, volume_24h=0.0)

    def round_quantity(self, symbol: str, qty: float) -> float:
        """Round a quantity to the venue's step size."""
        f = self._symbol_filters(symbol)
        step = f.get("stepSize") or 0.001
        return self._round_to_step(qty, step)

    def min_notional(self, symbol: str) -> float:
        return float(self._symbol_filters(symbol).get("minNotional", 0) or 0)

    def _ensure_leverage(self, symbol: str) -> None:
        """Set a low leverage so a single position can't be liquidated easily."""
        sym = self.denormalize_symbol(symbol)
        if sym in self._leverage_set:
            return
        res = self._post(
            "/fapi/v1/leverage",
            signed=True,
            params={"symbol": sym, "leverage": self.target_leverage},
        )
        if res is not None:
            self._leverage_set.add(sym)

    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, List[List[float]]]:
        data = self._get("/fapi/v1/depth", params={"symbol": self.denormalize_symbol(symbol), "limit": limit})
        if data:
            bids = [[float(p), float(q)] for p, q in data.get("bids", [])[:limit]]
            asks = [[float(p), float(q)] for p, q in data.get("asks", [])[:limit]]
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
        """Place order with native SL/TP (bracket order via OCO or separate)."""
        u_sym = self.denormalize_symbol(symbol)
        side = side.upper()
        if side not in ("BUY", "SELL", "LONG", "SHORT"):
            raise ValueError(f"Invalid side: {side}")

        binance_side = "BUY" if side in ("BUY", "LONG") else "SELL"
        order_type = order_type.upper()

        self._ensure_leverage(symbol)

        if order_type == "MARKET":
            binance_type = "MARKET"
        elif order_type == "LIMIT":
            binance_type = "LIMIT"
        elif order_type == "STOP":
            binance_type = "STOP_MARKET"
        else:
            binance_type = "MARKET"

        params = {
            "symbol": u_sym,
            "side": binance_side,
            "type": binance_type,
            "quantity": self._format_quantity(symbol, quantity),
        }

        if self._hedge_mode:
            params["positionSide"] = "LONG" if binance_side == "BUY" else "SHORT"

        if binance_type == "MARKET":
            params["newOrderRespType"] = "RESULT"

        if price and order_type in ("LIMIT", "STOP"):
            params["price"] = self._format_price(symbol, price)
        if stop_price:
            params["stopPrice"] = self._format_price(symbol, stop_price)

        # Place main order
        data = self._post("/fapi/v1/order", signed=True, params=params)
        if not data or data.get("status") in ("REJECTED", "EXPIRED"):
            raise RuntimeError(f"[BINANCE_FUTURES] Order failed: {data}")

        # Place SL/TP as OCO or separate orders
        if stop_loss or take_profit:
            self._place_sl_tp(
                symbol,
                binance_side,
                abs(float(data.get("executedQty") or quantity)),
                stop_loss,
                take_profit,
            )

        # Binance demo returns avgPrice="0.00" on MARKET orders even with
        # newOrderRespType=RESULT, so resolve the fill price defensively.
        fill_price = float(data.get("avgPrice") or 0)
        if fill_price <= 0:
            try:
                target = self.normalize_symbol(symbol)
                for pos in self.get_positions():
                    entry = float(pos.entry_price or 0)
                    if pos.symbol == target and entry > 0:
                        fill_price = entry
                        break
            except Exception as e:
                logger.warning(f"[BINANCE_FUTURES] Position fill-price lookup failed: {e}")
        if fill_price <= 0:
            fill_price = float(data.get("price") or 0)
        if fill_price <= 0:
            fill_price = float(price or 0)
        if fill_price <= 0:
            try:
                fill_price = float(self.get_ticker(symbol).last or 0)
            except Exception as e:
                logger.warning(f"[BINANCE_FUTURES] Ticker fill-price fallback failed: {e}")
        if fill_price <= 0:
            logger.warning(
                f"[BINANCE_FUTURES] No resolvable fill price for order {data.get('orderId')} on {symbol}"
            )

        fill_qty = float(data.get("executedQty") or quantity)
        executed_qty = float(data.get("executedQty") or 0)

        return UnifiedOrderResult(
            order_id=str(data.get("orderId", f"BN_{int(time.time()*1000)}")),
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=fill_price,
            quantity=fill_qty,
            status="FILLED" if (data.get("status") == "FILLED" or executed_qty > 0) else "PENDING",
            executed_qty=fill_qty,
            avg_price=fill_price,
            fee_paid=round(fill_qty * fill_price * 0.0004, 6),
            exchange="binance_futures",
        )

    def _place_sl_tp(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
    ) -> None:
        """Attach SL/TP via the Algo Order service.

        Since 2025-12-09 USDⓈ-M Futures rejects STOP_MARKET / TAKE_PROFIT_MARKET
        on /fapi/v1/order with -4120; conditional orders must go to
        /fapi/v1/algoOrder with algoType=CONDITIONAL.
        """
        u_sym = self.denormalize_symbol(symbol)
        close_side = "SELL" if side == "BUY" else "BUY"
        position_side = "LONG" if side == "BUY" else "SHORT"

        def submit(order_type: str, trigger: float) -> Optional[Dict]:
            params = {
                "algoType": "CONDITIONAL",
                "symbol": u_sym,
                "side": close_side,
                "type": order_type,
                "triggerPrice": self._format_price(symbol, trigger),
                "workingType": "MARK_PRICE",
                "closePosition": "true",
            }
            if self._hedge_mode:
                params["positionSide"] = position_side

            res = self._post("/fapi/v1/algoOrder", signed=True, params=params)
            if res is None:
                logger.error(
                    f"[BINANCE_FUTURES] {order_type} rejected for {symbol} @ {trigger} — "
                    f"position is UNPROTECTED"
                )
            else:
                logger.info(
                    f"[BINANCE_FUTURES] {order_type} {symbol} @ {trigger} "
                    f"(algoId={res.get('algoId') or res.get('clientAlgoId')})"
                )
            return res

        if stop_loss:
            submit("STOP_MARKET", stop_loss)
        if take_profit:
            submit("TAKE_PROFIT_MARKET", take_profit)

    def get_open_algo_orders(self, symbol: str) -> List[Dict]:
        """List open conditional (algo) orders for a symbol."""
        data = self._get(
            "/fapi/v1/openAlgoOrders",
            signed=True,
            params={"symbol": self.denormalize_symbol(symbol)},
        )
        if isinstance(data, dict):
            return data.get("orders", []) or []
        return data or []

    def cancel_algo_order(self, algo_id: Any) -> bool:
        data = self._delete(
            "/fapi/v1/algoOrder",
            signed=True,
            params={"algoId": algo_id},
        )
        return data is not None

    def cancel_protective_orders(self, symbol: str) -> int:
        """Cancel every open conditional order on a symbol."""
        cancelled = 0
        for order in self.get_open_algo_orders(symbol):
            algo_id = order.get("algoId") or order.get("orderId")
            if algo_id and self.cancel_algo_order(algo_id):
                cancelled += 1
        return cancelled

    def _format_quantity(self, symbol: str, qty: float) -> str:
        """Format quantity to the symbol's LOT_SIZE stepSize."""
        f = self._symbol_filters(symbol)
        step = f.get("stepSize") or 0.001
        precision = f.get("quantityPrecision", 3)
        value = self._round_to_step(qty, step)
        text = f"{value:.{max(precision, 8)}f}".rstrip("0").rstrip(".")
        return text or "0"

    def _format_price(self, symbol: str, price: float) -> str:
        """Format price to the symbol's PRICE_FILTER tickSize."""
        f = self._symbol_filters(symbol)
        tick = f.get("tickSize") or 0.01
        precision = f.get("pricePrecision", 2)
        value = self._round_to_step(price, tick)
        text = f"{value:.{max(precision, 8)}f}".rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _round_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        return round(round(value / step) * step, 10)

    def _get_exchange_info(self) -> Optional[Dict]:
        """Fetch and cache /fapi/v1/exchangeInfo."""
        if self._exchange_info is None:
            self._exchange_info = self._get("/fapi/v1/exchangeInfo")
        return self._exchange_info

    def _symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Return tick/step/min-notional filters for a symbol."""
        if self._symbol_cache is None:
            self._symbol_cache = {}
        sym = self.denormalize_symbol(symbol)
        if sym in self._symbol_cache:
            return self._symbol_cache[sym]

        info = self._get_exchange_info() or {}
        result: Dict[str, Any] = {}
        for s in info.get("symbols", []):
            if s.get("symbol") != sym:
                continue
            filters = {f.get("filterType"): f for f in s.get("filters", [])}
            price_filter = filters.get("PRICE_FILTER", {})
            lot_size = filters.get("LOT_SIZE", {})
            min_notional = filters.get("MIN_NOTIONAL", {})
            result = {
                "tickSize": float(price_filter.get("tickSize", 0) or 0),
                "stepSize": float(lot_size.get("stepSize", 0) or 0),
                "minQty": float(lot_size.get("minQty", 0) or 0),
                "minNotional": float(min_notional.get("notional", 0) or 0),
                "pricePrecision": int(s.get("pricePrecision", 2) or 2),
                "quantityPrecision": int(s.get("quantityPrecision", 3) or 3),
            }
            break

        self._symbol_cache[sym] = result
        return result

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        data = self._delete("/fapi/v1/order", signed=True, params={"symbol": self.denormalize_symbol(symbol), "orderId": order_id})
        return data is not None

    def get_historical_data(self, symbol: str, timeframe: str = "15m", limit: int = 100) -> List[Dict[str, Any]]:
        """Get real candles from Binance Futures API."""
        interval_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "1h", "4h": "4h", "1d": "1d",
        }
        interval = interval_map.get(timeframe, "15m")

        data = self._get("/fapi/v1/klines", params={
            "symbol": self.denormalize_symbol(symbol),
            "interval": interval,
            "limit": min(limit, 1500),
        })

        if not data:
            return []

        candles = []
        for k in data:
            candles.append({
                "timestamp": int(k[0] / 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return candles[-limit:]

    def get_fees(self, symbol: str) -> tuple[float, float]:
        return 0.0002, 0.0004

    def get_account_summary(self) -> Dict[str, Any]:
        """Account equity from the exchange's own valuation (USDT terms)."""
        if not self._has_credentials():
            return {}

        acc_data = self._get("/fapi/v2/account", signed=True)
        if not acc_data:
            return {}

        try:
            wallet = float(acc_data.get("totalWalletBalance", 0) or 0)
            unrealized = float(acc_data.get("totalUnrealizedProfit", 0) or 0)
            margin_balance = float(acc_data.get("totalMarginBalance", 0) or 0)
            available = float(acc_data.get("availableBalance", 0) or 0)

            if margin_balance <= 0:
                margin_balance = wallet + unrealized
            if wallet <= 0:
                # Fall back to summing the per-asset wallet balances
                wallet = sum(
                    float(a.get("walletBalance", 0) or 0)
                    for a in acc_data.get("assets", [])
                )

            return {
                "balance": wallet,
                "equity": margin_balance if margin_balance > 0 else wallet,
                "available": available,
                "unrealized_pnl": unrealized,
                "can_trade": bool(acc_data.get("canTrade", False)),
            }
        except Exception as e:
            logger.warning(f"[BINANCE_FUTURES] get_account_summary failed: {e}")
            return {}


# Alias
BinanceFuturesExchange = BinanceFuturesAdapter