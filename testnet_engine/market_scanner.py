import pandas as pd
import time
import datetime
import threading
from binance import ThreadedWebsocketManager
from binance.client import Client
from logger import get_logger

logger = get_logger("market_scanner")

class MarketScanner:
    def __init__(self, symbols, timeframe="1m", testnet=True):
        self.symbols = symbols
        self.timeframe = timeframe
        self.testnet = testnet
        
        self.client = Client("", "", testnet=testnet)
        self.twm = ThreadedWebsocketManager(testnet=testnet)
        
        # In-memory OHLCV cache per symbol
        self.candle_cache = {} 
        self.last_market_update = {}
        self.data_health_status = {sym: "UNKNOWN" for sym in symbols}
        self.callbacks = []
        
        self._stop_event = threading.Event()
        self._health_thread = None
        
    def _fetch_historical_candles(self, symbol):
        """Initializes the cache with 100 historical candles via REST."""
        try:
            klines = self.client.get_klines(symbol=symbol, interval=self.timeframe, limit=100)
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            
            self.candle_cache[symbol] = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].copy()
        except Exception as e:
            logger.error(f"[SCANNER] Failed to fetch historical data for {symbol}: {e}")

    def start(self):
        """Starts the multiplexed websocket stream for all discovered symbols."""
        logger.info(f"[SCANNER] Initializing cache for {len(self.symbols)} symbols...")
        for sym in self.symbols:
            self._fetch_historical_candles(sym)
            self.last_market_update[sym] = datetime.datetime.utcnow()
            self.data_health_status[sym] = "OK"
            time.sleep(0.1) # Small delay to respect REST rate limits during init
            
        logger.info("[SCANNER] Starting multiplex websocket...")
        self.twm.start()
        
        # Subscribe to multiplex kline streams
        streams = [f"{sym.lower()}@kline_{self.timeframe}" for sym in self.symbols]
        self.twm.start_multiplex_socket(callback=self._handle_socket_message, streams=streams)
        
        # Start health monitor
        self._health_thread = threading.Thread(target=self._health_monitor_loop, daemon=True)
        self._health_thread.start()
        
    def stop(self):
        self._stop_event.set()
        self.twm.stop()

    def _health_monitor_loop(self):
        """Monitors tick staleness and triggers REST fallback or WS reconnect."""
        while not self._stop_event.is_set():
            now = datetime.datetime.utcnow()
            all_stale = True
            
            for sym in self.symbols:
                last_update = self.last_market_update.get(sym)
                if not last_update:
                    continue
                    
                elapsed = (now - last_update).total_seconds()
                
                if elapsed > 15:
                    if self.data_health_status[sym] == "OK":
                        logger.warning(f"[SCANNER] ⚠️ {sym} data STALE (>15s). Falling back to REST.")
                    self.data_health_status[sym] = "STALE"
                    
                    # REST Fallback
                    try:
                        self._fetch_historical_candles(sym)
                        # Dispatch simulated tick from REST
                        for cb in self.callbacks:
                            try:
                                cb(sym, self.candle_cache[sym].copy(), "STALE")
                            except Exception as e:
                                logger.error(f"[SCANNER] Callback error (REST Fallback) for {sym}: {e}")
                    except Exception as e:
                        logger.error(f"[SCANNER] REST Fallback failed for {sym}: {e}")
                else:
                    self.data_health_status[sym] = "OK"
                    all_stale = False
            
            # If all symbols are completely dead for > 60 seconds, reconnect socket
            if all_stale and len(self.symbols) > 0:
                max_elapsed = max([(now - self.last_market_update.get(s, now)).total_seconds() for s in self.symbols])
                if max_elapsed > 60:
                    logger.critical("[SCANNER] 🚨 Entire websocket appears DEAD. Attempting reconnect...")
                    try:
                        self.twm.stop()
                        time.sleep(5)
                        self.twm = ThreadedWebsocketManager(testnet=self.testnet)
                        self.twm.start()
                        streams = [f"{sym.lower()}@kline_{self.timeframe}" for sym in self.symbols]
                        self.twm.start_multiplex_socket(callback=self._handle_socket_message, streams=streams)
                        
                        for sym in self.symbols:
                            self.last_market_update[sym] = datetime.datetime.utcnow() # Reset timeout
                    except Exception as e:
                        logger.error(f"[SCANNER] Reconnect failed: {e}")
                        
            time.sleep(5)

    def register_callback(self, callback_func):
        """Register a function to be called when a candle closes."""
        self.callbacks.append(callback_func)

    def _handle_socket_message(self, msg):
        """Process incoming websocket messages."""
        if 'data' not in msg or msg['data'].get('e') != 'kline':
            return
            
        data = msg['data']
        kline = data['k']
        symbol = data['s']
        is_closed = kline['x']
        
        # Track market update time
        self.last_market_update[symbol] = datetime.datetime.utcnow()
        
        # Only process fully closed candles for signals
        if not is_closed:
            return
            
        # Update Cache
        new_row = {
            'timestamp': pd.to_datetime(kline['t'], unit='ms'),
            'open': float(kline['o']),
            'high': float(kline['h']),
            'low': float(kline['l']),
            'close': float(kline['c']),
            'volume': float(kline['v'])
        }
        
        if symbol in self.candle_cache:
            df = self.candle_cache[symbol]
            # Append new row and drop oldest to keep size fixed (e.g. 100)
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            if len(df) > 100:
                df = df.iloc[1:]
            self.candle_cache[symbol] = df
        else:
            self.candle_cache[symbol] = pd.DataFrame([new_row])
            
        # Dispatch event to registered callbacks
        for cb in self.callbacks:
            try:
                cb(symbol, self.candle_cache[symbol].copy(), self.data_health_status[symbol])
            except Exception as e:
                logger.error(f"[SCANNER] Callback error for {symbol}: {e}")
