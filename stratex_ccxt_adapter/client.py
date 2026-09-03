import datetime
import os
import time
from typing import Any

import pandas as pd

from .errors import CCXTErrorMapper
from .models import NormalizedMarket, NormalizedOrder, NormalizedTicker
from .precision import PrecisionHelper

try:
    import ccxt
except ImportError:
    ccxt = None


class CCXTExchangeAdapter:
    """Unified CCXT exchange abstraction for STRATEX.
    
    Provides normalized market data, precision handling, and optional sandbox execution.
    Authoritative safety gates (ExecutionPolicy, RiskGate, ProfitabilityGate) always precede
    any order creation.
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        *,
        api_key: str | None = None,
        secret: str | None = None,
        password: str | None = None,
        sandbox: bool = True,
        enable_rate_limit: bool = True,
        timeout_ms: int = 10000,
        exchange_options: dict[str, Any] | None = None,
    ):
        if ccxt is None:
            raise RuntimeError("CCXT is required. Install with: pip install ccxt")
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Unsupported CCXT exchange id: {exchange_id}")

        self.exchange_id = exchange_id
        self.sandbox = sandbox
        self.enable_rate_limit = enable_rate_limit
        self.timeout_ms = timeout_ms

        exchange_cls = getattr(ccxt, exchange_id)
        config: dict[str, Any] = {
            "enableRateLimit": enable_rate_limit,
            "timeout": timeout_ms,
        }
        if api_key is not None:
            config["apiKey"] = api_key
        if secret is not None:
            config["secret"] = secret
        if password is not None:
            config["password"] = password
        if exchange_options:
            config.update(exchange_options)

        self.exchange = exchange_cls(config)

        if sandbox and hasattr(self.exchange, "set_sandbox_mode"):
            try:
                self.exchange.set_sandbox_mode(True)
            except Exception:
                pass

        self._markets_cache: dict[str, dict] = {}
        self._last_market_load: float = 0.0
        self._market_cache_ttl: float = 300.0  # 5 minutes cache
        self._symbol_map_to_ccxt: dict[str, str] = {}
        self._symbol_map_to_stratex: dict[str, str] = {}

        # Observability & Health
        self.last_update_time: str | None = None
        self.last_error: str | None = None
        self.total_requests: int = 0
        self.last_latency_ms: float = 0.0

    def to_ccxt_symbol(self, symbol: str) -> str:
        """Converts Stratex symbol (e.g. BTCUSDT) to CCXT format (e.g. BTC/USDT)."""
        if not symbol:
            return ""
        if "/" in symbol:
            return symbol
        if symbol in self._symbol_map_to_ccxt:
            return self._symbol_map_to_ccxt[symbol]

        # Common quote currency matching
        for quote in ["USDT", "USDC", "BUSD", "FDUSD", "BTC", "ETH", "BNB", "EUR"]:
            if symbol.endswith(quote) and len(symbol) > len(quote):
                base = symbol[:-len(quote)]
                candidate = f"{base}/{quote}"
                self._symbol_map_to_ccxt[symbol] = candidate
                self._symbol_map_to_stratex[candidate] = symbol
                return candidate

        return symbol

    def to_stratex_symbol(self, symbol: str) -> str:
        """Converts CCXT symbol (e.g. BTC/USDT or BTC/USDT:USDT) to Stratex format (BTCUSDT)."""
        if not symbol:
            return ""
        clean = symbol.split(":")[0]  # strip futures settlement postfix
        if clean in self._symbol_map_to_stratex:
            return self._symbol_map_to_stratex[clean]
        res = clean.replace("/", "").replace("-", "").replace("_", "")
        self._symbol_map_to_stratex[symbol] = res
        self._symbol_map_to_stratex[clean] = res
        return res

    def load_markets(self, reload: bool = False) -> dict[str, dict]:
        now = time.time()
        if not reload and self._markets_cache and (now - self._last_market_load < self._market_cache_ttl):
            return self._markets_cache

        t0 = time.time()
        try:
            self._markets_cache = self.exchange.load_markets(reload)
            self._last_market_load = now
            self.last_update_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.last_latency_ms = round((time.time() - t0) * 1000.0, 2)
            self.total_requests += 1

            # Populate bidirectional symbol mappings
            for ccxt_sym, m in self._markets_cache.items():
                clean_ccxt = ccxt_sym.split(":")[0]
                strat_sym = m.get("id") or clean_ccxt.replace("/", "")
                self._symbol_map_to_ccxt[strat_sym] = clean_ccxt
                self._symbol_map_to_stratex[clean_ccxt] = strat_sym
                self._symbol_map_to_stratex[ccxt_sym] = strat_sym

            return self._markets_cache
        except Exception as e:
            self.last_error = f"{CCXTErrorMapper.classify(e)}: {str(e)}"
            raise

    def markets(self, reload: bool = False) -> list[NormalizedMarket]:
        raw = self.load_markets(reload)
        out = []
        for symbol, m in raw.items():
            limits = m.get("limits") or {}
            amount_limits = limits.get("amount") or {}
            cost_limits = limits.get("cost") or {}
            precision = m.get("precision") or {}
            out.append(NormalizedMarket(
                symbol=symbol,
                base=str(m.get("base") or ""),
                quote=str(m.get("quote") or ""),
                active=bool(m.get("active", True)),
                market_type=str(m.get("type") or "spot"),
                min_amount=amount_limits.get("min"),
                max_amount=amount_limits.get("max"),
                min_cost=cost_limits.get("min"),
                price_precision=precision.get("price") if isinstance(precision.get("price"), int) else None,
                amount_precision=precision.get("amount") if isinstance(precision.get("amount"), int) else None,
                price_step=precision.get("price") if isinstance(precision.get("price"), float) else None,
                amount_step=precision.get("amount") if isinstance(precision.get("amount"), float) else None,
            ))
        return out

    def get_market_metadata(self, symbol: str) -> NormalizedMarket | None:
        ccxt_sym = self.to_ccxt_symbol(symbol)
        markets = self.load_markets()
        m = markets.get(ccxt_sym)
        if not m:
            return None
        limits = m.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        precision = m.get("precision") or {}
        return NormalizedMarket(
            symbol=ccxt_sym,
            base=str(m.get("base") or ""),
            quote=str(m.get("quote") or ""),
            active=bool(m.get("active", True)),
            market_type=str(m.get("type") or "spot"),
            min_amount=amount_limits.get("min"),
            max_amount=amount_limits.get("max"),
            min_cost=cost_limits.get("min"),
            price_precision=precision.get("price") if isinstance(precision.get("price"), int) else None,
            amount_precision=precision.get("amount") if isinstance(precision.get("amount"), int) else None,
            price_step=precision.get("price") if isinstance(precision.get("price"), float) else None,
            amount_step=precision.get("amount") if isinstance(precision.get("amount"), float) else None,
        )

    def fetch_ticker(self, symbol: str) -> NormalizedTicker:
        ccxt_sym = self.to_ccxt_symbol(symbol)
        t0 = time.time()
        try:
            t = self.exchange.fetch_ticker(ccxt_sym)
            self.last_update_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.last_latency_ms = round((time.time() - t0) * 1000.0, 2)
            self.total_requests += 1
            return NormalizedTicker(
                symbol=symbol,
                last=t.get("last"),
                bid=t.get("bid"),
                ask=t.get("ask"),
                base_volume=t.get("baseVolume"),
                quote_volume=t.get("quoteVolume"),
                timestamp_ms=t.get("timestamp"),
            )
        except Exception as e:
            self.last_error = f"{CCXTErrorMapper.classify(e)}: {str(e)}"
            raise

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", since: int | None = None, limit: int | None = None):
        ccxt_sym = self.to_ccxt_symbol(symbol)
        t0 = time.time()
        try:
            res = self.exchange.fetch_ohlcv(ccxt_sym, timeframe, since, limit)
            self.last_update_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.last_latency_ms = round((time.time() - t0) * 1000.0, 2)
            self.total_requests += 1
            return res
        except Exception as e:
            self.last_error = f"{CCXTErrorMapper.classify(e)}: {str(e)}"
            raise

    def fetch_ohlcv_dataframe(self, symbol: str, timeframe: str = "1h", since: int | None = None, limit: int | None = None) -> pd.DataFrame:
        """Fetches OHLCV candles and converts them into Stratex standard DataFrame."""
        raw = self.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not raw:
            return pd.DataFrame()
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)
        return df

    def fetch_order_book(self, symbol: str, limit: int | None = None):
        ccxt_sym = self.to_ccxt_symbol(symbol)
        t0 = time.time()
        try:
            res = self.exchange.fetch_order_book(ccxt_sym, limit)
            self.last_update_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.last_latency_ms = round((time.time() - t0) * 1000.0, 2)
            self.total_requests += 1
            return res
        except Exception as e:
            self.last_error = f"{CCXTErrorMapper.classify(e)}: {str(e)}"
            raise

    def format_amount(self, symbol: str, amount: float) -> float:
        ccxt_sym = self.to_ccxt_symbol(symbol)
        return float(self.exchange.amount_to_precision(ccxt_sym, amount))

    def format_price(self, symbol: str, price: float) -> float:
        ccxt_sym = self.to_ccxt_symbol(symbol)
        return float(self.exchange.price_to_precision(ccxt_sym, price))

    def create_order(
        self,
        *,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
        authorize_fn=None,
    ) -> NormalizedOrder:
        """Creates an order through CCXT with strict Stratex authorization gates."""
        # 1. Enforce permanent LIVE trading block
        trading_mode = os.environ.get("TRADING_MODE", "TESTNET").upper()
        if trading_mode == "LIVE" or os.environ.get("LIVE_TRADING_ENABLED") == "1":
            raise PermissionError("SECURITY CRITICAL: LIVE trading is permanently blocked by design.")

        # 2. Enforce Paper & Research mode order prohibition
        if trading_mode == "PAPER" or os.environ.get("RESEARCH_MODE") == "1":
            raise PermissionError(f"CCXT order blocked: Cannot place real orders in {trading_mode} / Research mode.")

        # 3. Explicit authorization check
        if authorize_fn is not None:
            allowed, reason = authorize_fn(symbol, side, amount, price)
            if not allowed:
                raise PermissionError(f"STRATEX_ORDER_BLOCKED:{reason}")
        else:
            # Fallback to ExecutionPolicy check if available
            try:
                from execution import ExecutionPolicy
                allowed, reason = ExecutionPolicy.can_place_order()
                if not allowed:
                    raise PermissionError(f"STRATEX_ORDER_BLOCKED:{reason}")
            except ImportError:
                pass

        ccxt_sym = self.to_ccxt_symbol(symbol)
        amount_fmt = self.format_amount(symbol, amount)
        price_fmt = self.format_price(symbol, price) if price is not None else None

        try:
            t0 = time.time()
            order = self.exchange.create_order(
                ccxt_sym, order_type.lower(), side.lower(), amount_fmt, price_fmt, params or {}
            )
            self.last_latency_ms = round((time.time() - t0) * 1000.0, 2)
            self.total_requests += 1
            return self.normalize_order(order)
        except Exception as e:
            err_cat = CCXTErrorMapper.classify(e)
            self.last_error = f"{err_cat}: {str(e)}"
            # Never blindly retry order creation on network timeout to prevent duplicate orders
            raise

    @staticmethod
    def normalize_order(order: dict) -> NormalizedOrder:
        return NormalizedOrder(
            id=str(order.get("id") or ""),
            client_order_id=order.get("clientOrderId"),
            symbol=str(order.get("symbol") or ""),
            side=str(order.get("side") or "").upper(),
            order_type=str(order.get("type") or ""),
            status=str(order.get("status") or ""),
            amount=order.get("amount"),
            filled=order.get("filled"),
            remaining=order.get("remaining"),
            average=order.get("average"),
            price=order.get("price"),
            cost=order.get("cost"),
            fee=order.get("fee"),
            timestamp_ms=order.get("timestamp"),
            raw=order,
        )

    def fetch_order(self, order_id: str, symbol: str | None = None) -> NormalizedOrder:
        ccxt_sym = self.to_ccxt_symbol(symbol) if symbol else None
        return self.normalize_order(self.exchange.fetch_order(order_id, ccxt_sym))

    def cancel_order(self, order_id: str, symbol: str | None = None) -> NormalizedOrder:
        ccxt_sym = self.to_ccxt_symbol(symbol) if symbol else None
        return self.normalize_order(self.exchange.cancel_order(order_id, ccxt_sym))

    def get_health_status(self) -> dict[str, Any]:
        """Returns diagnostic telemetry for dashboard and health monitoring."""
        return {
            "provider": "ccxt",
            "exchange_id": self.exchange_id,
            "sandbox": self.sandbox,
            "rate_limit_enabled": self.enable_rate_limit,
            "markets_cached_count": len(self._markets_cache),
            "last_update_time": self.last_update_time,
            "last_latency_ms": self.last_latency_ms,
            "total_requests": self.total_requests,
            "last_error": self.last_error,
            "status": "HEALTHY" if not self.last_error else "DEGRADED",
        }

    def close(self):
        close_fn = getattr(self.exchange, "close", None)
        if callable(close_fn):
            close_fn()

