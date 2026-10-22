import config
from binance.client import Client

from logger import get_logger
from stratex_agents.venue.binance_futures_agent import BinanceFuturesAgent

logger = get_logger("discovery_service")


class SymbolDiscoveryService:
    def __init__(self, testnet=True):
        self.testnet = testnet
        self.venue_agent = BinanceFuturesAgent()
        is_futures = getattr(config, "TRADING_MODE", "TESTNET").upper() == "FUTURES"
        try:
            if testnet:
                # Use the same endpoint policy as order execution. Do not let demo
                # trading accidentally use legacy testnet URLs (or vice versa).
                self.client = self.venue_agent.create_public_client(futures=is_futures, client_cls=Client)
            else:
                # Public-only production market data is explicitly requested only
                # when the caller passes testnet=False; this client is never signed.
                self.client = Client("", "", testnet=False, ping=False)
        except Exception as exc:
            logger.warning(f"[DISCOVERY] Unable to initialize market-data client: {exc}")
            self.client = None

        # Old testnet market metadata sometimes needs a public-data fallback. The
        # newer Demo Trading environment must never silently mix in production
        # metadata: broker filters/symbol availability are venue-specific.
        if self.venue_agent.demo_trading or not testnet:
            self.prod_client = None
        else:
            try:
                self.prod_client = Client("", "", testnet=False, ping=False)
            except Exception:
                self.prod_client = None
        
    def discover_eligible_symbols(self, min_quote_volume=1000, top_n=15):
        """
        Dynamically discovers USDT-quoted trading pairs that are currently ACTIVE 
        and meet the liquidity criteria, ranking them by 24hr volume.
        """
        logger.info("[DISCOVERY] Fetching exchange metadata and 24hr tickers...")
        is_futures = getattr(config, "TRADING_MODE", "TESTNET").upper() == "FUTURES"
        try:
            exchange_info = None
            tickers = None
            if self.client:
                try:
                    if is_futures and hasattr(self.client, "futures_exchange_info"):
                        exchange_info = self.client.futures_exchange_info()
                        tickers = self.client.futures_ticker() if hasattr(self.client, "futures_ticker") else None
                    else:
                        exchange_info = self.client.get_exchange_info()
                        tickers = self.client.get_ticker()
                except Exception as ex_err:
                    logger.warning(f"[DISCOVERY] Testnet exchange info error: {ex_err}")
            
            if (not exchange_info or not tickers) and self.prod_client:
                try:
                    if is_futures and hasattr(self.prod_client, "futures_exchange_info"):
                        exchange_info = self.prod_client.futures_exchange_info()
                        tickers = self.prod_client.futures_ticker() if hasattr(self.prod_client, "futures_ticker") else None
                    else:
                        exchange_info = self.prod_client.get_exchange_info()
                        tickers = self.prod_client.get_ticker()
                except Exception as prod_err:
                    logger.warning(f"[DISCOVERY] Prod exchange info fallback error: {prod_err}")

            if not exchange_info or not tickers:
                raise RuntimeError("Failed to fetch exchange metadata from both testnet and production.")
            
            # Create a lookup for 24h volume and spread
            ticker_lookup = {}
            for t in tickers:
                try:
                    bid = float(t.get('bidPrice', 0))
                    ask = float(t.get('askPrice', 0))
                    spread = (ask - bid) / bid if bid > 0 else 0.0
                    ticker_lookup[t['symbol']] = {
                        "volume": float(t.get('quoteVolume', 0.0)),
                        "spread": spread
                    }
                except:
                    pass
            
            candidates = []
            
            for symbol_info in exchange_info.get('symbols', []):
                symbol = symbol_info['symbol']
                status = symbol_info['status']
                quote_asset = symbol_info['quoteAsset']
                
                # Spot USDT pairs only
                if status != "TRADING":
                    continue
                if quote_asset != "USDT":
                    continue
                    
                t_info = ticker_lookup.get(symbol, {})
                vol = t_info.get("volume", 0.0)
                spread = t_info.get("spread", 0.0)
                
                # Check minimum volume on mainnet, or allow lower on testnet
                effective_min_vol = 0.0 if self.testnet else min_quote_volume
                if vol < effective_min_vol:
                    continue
                    
                # Reject if spread is > 1.5%
                if spread > 0.015 or spread < 0:
                    continue
                    
                # Parse Binance Filters
                parsed_filters = {
                    "stepSize": 1.0,
                    "minNotional": 10.0,
                    "tickSize": 0.01
                }
                
                for f in symbol_info.get("filters", []):
                    f_type = f.get("filterType")
                    if f_type == "LOT_SIZE":
                        parsed_filters["stepSize"] = float(f.get("stepSize", 1.0))
                    elif f_type in ["MIN_NOTIONAL", "NOTIONAL"]:
                        parsed_filters["minNotional"] = float(f.get("minNotional", f.get("notional", 10.0)))
                    elif f_type == "PRICE_FILTER":
                        parsed_filters["tickSize"] = float(f.get("tickSize", 0.01))
                        
                candidates.append((symbol, vol, parsed_filters))
                
            # Sort by 24h volume descending
            candidates.sort(key=lambda x: x[1], reverse=True)
            selected = candidates[:top_n] if top_n > 0 else candidates
            
            eligible_symbols = {sym: filters for sym, _, filters in selected}
            
            # Ensure major symbols are always present
            majors = {
                "BTCUSDT": {"stepSize": 0.00001, "minNotional": 5.0, "tickSize": 0.01},
                "ETHUSDT": {"stepSize": 0.0001, "minNotional": 5.0, "tickSize": 0.01},
                "SOLUSDT": {"stepSize": 0.01, "minNotional": 5.0, "tickSize": 0.01},
                "BNBUSDT": {"stepSize": 0.01, "minNotional": 5.0, "tickSize": 0.01},
                "LINKUSDT": {"stepSize": 0.01, "minNotional": 5.0, "tickSize": 0.01},
                "TRXUSDT": {"stepSize": 0.1, "minNotional": 5.0, "tickSize": 0.0001},
                "ADAUSDT": {"stepSize": 0.1, "minNotional": 5.0, "tickSize": 0.0001},
                "DOGEUSDT": {"stepSize": 1.0, "minNotional": 5.0, "tickSize": 0.00001},
            }
            for m_sym, m_filt in majors.items():
                if m_sym not in eligible_symbols:
                    eligible_symbols[m_sym] = m_filt
                    
            logger.info(f"[DISCOVERY] Found {len(eligible_symbols)} eligible symbols.")
            return eligible_symbols

        except Exception as e:
            logger.error(f"[DISCOVERY] Error discovering symbols: {e}")
            if self.venue_agent.demo_trading:
                # Do not trade with stale/hard-coded symbol metadata in Demo Trading.
                return {}
            fallback = {
                "BTCUSDT": {"stepSize": 0.00001, "minNotional": 5.0, "tickSize": 0.01},
                "ETHUSDT": {"stepSize": 0.0001, "minNotional": 5.0, "tickSize": 0.01}
            }
            logger.info(f"[DISCOVERY] Falling back to {len(fallback)} legacy testnet major symbols.")
            return fallback

    def get_symbol_filters(self, symbols):
        """Parse exchange filters for explicitly requested symbols.

        Used by the governance layer to backfill OOS-validated assets that the
        volume-ranked discovery (top-N + majors backstop) may have missed —
        e.g. a validated asset outside the top-N on a low-volume testnet.
        Returns {symbol: {stepSize, minNotional, tickSize}} for TRADING symbols.
        """
        out = {}
        exchange_info = None
        is_futures = getattr(config, "TRADING_MODE", "TESTNET").upper() == "FUTURES"
        if self.client:
            try:
                if is_futures and hasattr(self.client, "futures_exchange_info"):
                    exchange_info = self.client.futures_exchange_info()
                else:
                    exchange_info = self.client.get_exchange_info()
            except Exception as e:
                logger.debug(f"[DISCOVERY] Testnet get_symbol_filters error: {e}")
        if not exchange_info and self.prod_client:
            try:
                if is_futures and hasattr(self.prod_client, "futures_exchange_info"):
                    exchange_info = self.prod_client.futures_exchange_info()
                else:
                    exchange_info = self.prod_client.get_exchange_info()
            except Exception as e:
                logger.error(f"[DISCOVERY] Prod get_symbol_filters error: {e}")
        if not exchange_info:
            return out
        wanted = set(symbols)
        for symbol_info in exchange_info.get("symbols", []):
            symbol = symbol_info["symbol"]
            status = symbol_info.get("status")
            if symbol not in wanted or (status != "TRADING" and symbol_info.get("contractStatus") != "TRADING"):
                continue
            if symbol_info.get("quoteAsset") != "USDT":
                continue
            parsed = {"stepSize": 1.0, "minNotional": 10.0, "tickSize": 0.01}
            for f in symbol_info.get("filters", []):
                f_type = f.get("filterType")
                if f_type == "LOT_SIZE":
                    parsed["stepSize"] = float(f.get("stepSize", 1.0))
                elif f_type in ["MIN_NOTIONAL", "NOTIONAL"]:
                    parsed["minNotional"] = float(f.get("minNotional", f.get("notional", 10.0)))
                elif f_type == "PRICE_FILTER":
                    parsed["tickSize"] = float(f.get("tickSize", 0.01))
            out[symbol] = parsed
        missing = wanted - set(out)
        if missing:
            logger.warning(f"[DISCOVERY] Requested symbols not TRADING/USDT: {sorted(missing)}")
        return out
