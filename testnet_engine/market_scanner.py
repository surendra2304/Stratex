import pandas as pd
import time
import datetime
import threading
from binance import ThreadedWebsocketManager
from binance.client import Client
from logger import get_logger

logger = get_logger("market_scanner")

class MarketScanner:
    def __init__(self, symbols, timeframes=None, timeframe=None, testnet=True):
        self.symbols = symbols
        if timeframes is not None:
            self.timeframes = timeframes if isinstance(timeframes, list) else [timeframes]
        elif timeframe is not None:
            self.timeframes = [timeframe]
        else:
            self.timeframes = ["1m"]
            
        self.testnet = testnet
        
        self.client = Client("", "", testnet=testnet)
        self.twm = ThreadedWebsocketManager(testnet=testnet)
        
        # In-memory OHLCV cache per (symbol, timeframe) and symbol
        self.candle_cache = {} 
        self.last_market_update = {} # track by (symbol, timeframe)
        self.data_health_status = {sym: "UNKNOWN" for sym in symbols}
        self.callbacks = []
        
        self._stop_event = threading.Event()
        self._health_thread = None
        
    def _fetch_historical_candles(self, symbol, tf):
        """Initializes the cache with 250 historical candles via REST."""
        try:
            klines = self.client.get_klines(symbol=symbol, interval=tf, limit=250)
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume', 'taker_buy_base_asset_volume']:
                df[col] = df[col].astype(float)
                
            df['buy_vol'] = df['taker_buy_base_asset_volume']
            df['sell_vol'] = df['volume'] - df['buy_vol']
            df['vol_delta'] = df['buy_vol'] - df['sell_vol']
            
            clean_df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'vol_delta', 'buy_vol', 'sell_vol']].copy()
            self.candle_cache[(symbol, tf)] = clean_df
        except Exception as e:
            logger.error(f"[SCANNER] Failed to fetch historical data for {symbol} ({tf}): {e}")

    def start(self):
        """Starts the multiplexed websocket stream for all discovered symbols and timeframes."""
        logger.info(f"[SCANNER] Initializing cache for {len(self.symbols)} symbols across {len(self.timeframes)} timeframes...")
        for sym in self.symbols:
            for tf in self.timeframes:
                self._fetch_historical_candles(sym, tf)
                self.last_market_update[(sym, tf)] = datetime.datetime.utcnow()
            self.data_health_status[sym] = "OK"
            time.sleep(0.1) # Small delay to respect REST rate limits during init
            
        logger.info("[SCANNER] Starting multiplex websocket...")
        self.twm.start()
        
        # Subscribe to multiplex kline streams
        streams = [f"{sym.lower()}@kline_{tf}" for sym in self.symbols for tf in self.timeframes]
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
                sym_stale = False
                for tf in self.timeframes:
                    last_update = self.last_market_update.get((sym, tf))
                    if not last_update:
                        continue
                        
                    elapsed = (now - last_update).total_seconds()
                    
                    if elapsed > max(15, int(pd.to_timedelta(tf).total_seconds()) * 1.5): # Scale timeout to TF
                        sym_stale = True
                        if self.data_health_status[sym] == "OK":
                            logger.warning(f"[SCANNER] ⚠️ {sym} ({tf}) data STALE (>15s or 1.5x TF). Falling back to REST.")
                        
                        # REST Fallback
                        try:
                            self._fetch_historical_candles(sym, tf)
                            # Dispatch simulated tick from REST
                            for cb in self.callbacks:
                                try:
                                    cb(sym, tf, self.candle_cache[(sym, tf)].copy(), "STALE")
                                except Exception as e:
                                    logger.error(f"[SCANNER] Callback error (REST Fallback) for {sym} ({tf}): {e}")
                        except Exception as e:
                            logger.error(f"[SCANNER] REST Fallback failed for {sym} ({tf}): {e}")
                
                if sym_stale:
                    self.data_health_status[sym] = "STALE"
                else:
                    self.data_health_status[sym] = "OK"
                    all_stale = False
            
            # If all symbols are completely dead for > 60 seconds, reconnect socket
            if all_stale and len(self.symbols) > 0:
                max_elapsed = max([(now - self.last_market_update.get((s, t), now)).total_seconds() for s in self.symbols for t in self.timeframes])
                if max_elapsed > 120:
                    logger.critical("[SCANNER] 🚨 Entire websocket appears DEAD. Attempting reconnect...")
                    try:
                        self.twm.stop()
                        time.sleep(5)
                        self.twm = ThreadedWebsocketManager(testnet=self.testnet)
                        self.twm.start()
                        streams = [f"{sym.lower()}@kline_{tf}" for sym in self.symbols for tf in self.timeframes]
                        self.twm.start_multiplex_socket(callback=self._handle_socket_message, streams=streams)
                        
                        for sym in self.symbols:
                            for tf in self.timeframes:
                                self.last_market_update[(sym, tf)] = datetime.datetime.utcnow() # Reset timeout
                    except Exception as e:
                        logger.error(f"[SCANNER] Reconnect failed: {e}")
                        
            time.sleep(5)

    def register_callback(self, callback_func):
        """Register a function to be called when a candle closes. Signature: cb(symbol, tf, df, health)"""
        self.callbacks.append(callback_func)

    def _handle_socket_message(self, msg):
        """Process incoming websocket messages."""
        if 'data' not in msg or msg['data'].get('e') != 'kline':
            return
            
        data = msg['data']
        kline = data['k']
        symbol = data['s']
        tf = kline.get('i', self.timeframes[0] if self.timeframes else "1m")
        is_closed = kline.get('x', True)
        
        # Track market update time
        self.last_market_update[(symbol, tf)] = datetime.datetime.utcnow()
        if not hasattr(self, 'tick_counts'):
            self.tick_counts = {}
        self.tick_counts[(symbol, tf)] = self.tick_counts.get((symbol, tf), 0) + 1
        
        # Only process fully closed candles for signals
        if not is_closed:
            return
            
        # Update Cache
        vol = float(kline.get('v', 0))
        taker_vol = float(kline.get('V', vol / 2.0))
        buy_vol = taker_vol
        sell_vol = max(0.0, vol - buy_vol)
        vol_delta = buy_vol - sell_vol

        new_row = {
            'timestamp': pd.to_datetime(kline.get('t', int(time.time() * 1000)), unit='ms'),
            'open': float(kline['o']),
            'high': float(kline['h']),
            'low': float(kline['l']),
            'close': float(kline['c']),
            'volume': vol,
            'vol_delta': vol_delta,
            'buy_vol': buy_vol,
            'sell_vol': sell_vol
        }
        
        logger.info(f"[DATA_RECEIVED] {symbol} {tf} | Candle Closed: {new_row['close']} | Vol: {vol:.2f} | VolDelta: {vol_delta:.2f}")

        if (symbol, tf) in self.candle_cache:
            df = self.candle_cache[(symbol, tf)]
            # Append new row and drop oldest to keep size fixed (e.g. 250)
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            if len(df) > 250:
                df = df.iloc[1:]
            self.candle_cache[(symbol, tf)] = df
        else:
            new_df = pd.DataFrame([new_row])
            self.candle_cache[(symbol, tf)] = new_df
            
        # Dispatch event to registered callbacks
        for cb in self.callbacks:
            try:
                cb(symbol, tf, self.candle_cache[(symbol, tf)].copy(), self.data_health_status.get(symbol, "OK"))
            except Exception as e:
                logger.error(f"[SCANNER] Callback error for {symbol} ({tf}): {e}")
