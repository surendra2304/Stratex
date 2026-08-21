from binance.client import Client

from logger import get_logger

logger = get_logger("discovery_service")

class SymbolDiscoveryService:
    def __init__(self, testnet=True):
        self.testnet = testnet
        self.client = Client("", "", testnet=testnet)
        if testnet:
            self.client.API_URL = "https://testnet.binance.vision/api"
        
    def discover_eligible_symbols(self, min_quote_volume=1000, top_n=15):
        """
        Dynamically discovers USDT-quoted trading pairs that are currently ACTIVE 
        and meet the liquidity criteria, ranking them by 24hr volume.
        """
        logger.info("[DISCOVERY] Fetching exchange metadata and 24hr tickers...")
        try:
            exchange_info = self.client.get_exchange_info()
            tickers = self.client.get_ticker()
            
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
            fallback = {
                "BTCUSDT": {"stepSize": 0.00001, "minNotional": 5.0, "tickSize": 0.01},
                "ETHUSDT": {"stepSize": 0.0001, "minNotional": 5.0, "tickSize": 0.01}
            }
            logger.info(f"[DISCOVERY] Falling back to {len(fallback)} major symbols.")
            return fallback
