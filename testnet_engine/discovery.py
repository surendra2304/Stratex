from binance.client import Client
from logger import get_logger

logger = get_logger("discovery_service")

class SymbolDiscoveryService:
    def __init__(self, testnet=True):
        self.client = Client("", "", testnet=testnet)
        
    def discover_eligible_symbols(self, min_quote_volume=1_000_000):
        """
        Dynamically discovers USDT-quoted trading pairs that are currently ACTIVE 
        and meet the minimum liquidity criteria (quoteVolume > min_quote_volume).
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
                    spread = (ask - bid) / bid if bid > 0 else 1.0
                    ticker_lookup[t['symbol']] = {
                        "volume": float(t['quoteVolume']),
                        "spread": spread
                    }
                except:
                    pass
            
            eligible_symbols = {}
            
            for symbol_info in exchange_info['symbols']:
                symbol = symbol_info['symbol']
                status = symbol_info['status']
                quote_asset = symbol_info['quoteAsset']
                
                # Filters
                if status != "TRADING":
                    continue
                if quote_asset != "USDT":
                    continue
                    
                t_info = ticker_lookup.get(symbol, {})
                vol = t_info.get("volume", 0.0)
                spread = t_info.get("spread", 1.0)
                
                if vol < min_quote_volume:
                    continue
                    
                # Reject if spread is > 0.5% (liquidity/price validity check)
                if spread > 0.005 or spread < 0:
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
                        parsed_filters["minNotional"] = float(f.get("minNotional", f.get("minNotional", 10.0)))
                    elif f_type == "PRICE_FILTER":
                        parsed_filters["tickSize"] = float(f.get("tickSize", 0.01))
                        
                eligible_symbols[symbol] = parsed_filters
                
            logger.info(f"[DISCOVERY] Found {len(eligible_symbols)} eligible symbols.")
            return eligible_symbols
            
        except Exception as e:
            logger.error(f"[DISCOVERY] Error discovering symbols: {e}")
            # Fallback
            fallback = {
                "BTCUSDT": {"stepSize": 0.00001, "minNotional": 5.0, "tickSize": 0.01},
                "ETHUSDT": {"stepSize": 0.0001, "minNotional": 5.0, "tickSize": 0.01}
            }
            logger.info(f"[DISCOVERY] Falling back to {len(fallback)} major symbols.")
            return fallback
