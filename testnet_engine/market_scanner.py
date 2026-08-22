import datetime
import threading
import time

import pandas as pd
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
        self.last_candle_close = {} # track actual candle closes
        self.data_health_status = {sym: "UNKNOWN" for sym in symbols}
        self.callbacks = []
        
        self._cache_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._health_thread = None
        
    def _fetch_historical_candles(self, symbol, tf):
        """Initializes the cache with 250 historical candles via REST.

        Binance TESTNET caps klines at ~101 per request (production returns
        1000), so a single limit=250 call silently yields a 101-bar cache —
        too short for a correctly warmed EMA200/ADX. Paginate backwards until
        the target depth is reached or the symbol history is exhausted.
        """
        TARGET_BARS = 250
        try:
            klines = []
            end_id = None
            for _ in range(6):  # hard cap: 6 pages ≈ 600 bars max
                params = {"symbol": symbol, "interval": tf, "limit": 250}
                if end_id is not None:
                    params["endTime"] = end_id
                page = self.client.get_klines(**params)
                if not page:
                    break
                klines = page + klines if end_id is not None else page
                if len(page) < 2 or (end_id is None and len(klines) >= TARGET_BARS):
                    break
                end_id = page[0][0] - 1
                if len(klines) >= TARGET_BARS:
                    break
            if not klines:
                logger.error(f"[SCANNER] No historical data for {symbol} ({tf})")
                return
            if len(klines) < TARGET_BARS:
                # Binance TESTNET keeps only ~2-3 weeks of history — too short to
                # warm EMA200/ADX. Seed older bars from PRODUCTION public klines
                # (no credentials required). Only indicator warm-up uses these;
                # the newest bars (entries/SL/TP) remain testnet data.
                try:
                    prod_client = Client("", "", testnet=False)
                    first_ts = klines[0][0]
                    need = TARGET_BARS - len(klines)
                    seed = prod_client.get_klines(
                        symbol=symbol, interval=tf,
                        limit=min(need + 50, 1000), endTime=first_ts - 1
                    )
                    if seed:
                        klines = seed[-need:] + klines
                        logger.info(
                            f"[SCANNER] {symbol} {tf}: warm-seeded {min(len(seed), need)} bars "
                            f"from production history (testnet holds only {len(klines) - min(len(seed), need)})"
                        )
                except Exception as seed_err:
                    logger.warning(f"[SCANNER] {symbol} {tf}: production warm-seed unavailable ({seed_err}) — indicators partially warmed")
            if len(klines) < TARGET_BARS:
                logger.warning(
                    f"[SCANNER] {symbol} {tf}: only {len(klines)} bars available "
                    f"(target {TARGET_BARS}) — indicators will be partially warmed"
                )
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            # Deduplicate in case pages overlap, oldest -> newest
            df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume', 'taker_buy_base_asset_volume']:
                df[col] = df[col].astype(float)
            if df is not None and not df.empty:
                # Drop unclosed candle (Binance always returns the active incomplete candle at the end)
                # A candle is only closed if its close_time is in the past.
                now_utc = datetime.datetime.utcnow()
                if 'close_time' in df.columns:
                    df = df[df['close_time'] <= now_utc]
                
            df['buy_vol'] = df['taker_buy_base_asset_volume']
            df['sell_vol'] = df['volume'] - df['buy_vol']
            df['vol_delta'] = df['buy_vol'] - df['sell_vol']
            
            clean_df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'vol_delta', 'buy_vol', 'sell_vol', 'close_time']].copy()
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
                self.last_candle_close[(sym, tf)] = datetime.datetime.utcnow()
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
                    try:
                        tf_secs = int(pd.to_timedelta(tf).total_seconds())
                    except Exception:
                        if tf.endswith('m'): tf_secs = int(tf[:-1]) * 60
                        elif tf.endswith('h'): tf_secs = int(tf[:-1]) * 3600
                        else: tf_secs = 60
                        
                    last_close = self.last_candle_close.get((sym, tf), now - datetime.timedelta(days=1))
                    elapsed_close = (now - last_close).total_seconds()
                    
                    # Faster stale threshold: cap at 90s so 1m candles are detected quickly
                    stale_threshold = max(15, min(tf_secs * 1.5, 90))
                    if elapsed > stale_threshold or elapsed_close > tf_secs + 15:
                        sym_stale = True
                        if self.data_health_status.get(sym) == "OK":
                            logger.warning(f"[CANDLE_CLOSE_MISSED] {sym} ({tf}) data STALE. Tick elapsed: {elapsed:.1f}s, Close elapsed: {elapsed_close:.1f}s")
                            logger.warning(f"[REST_FALLBACK] Triggering historical sync for {sym} ({tf})")
                        
                        # REST Fallback
                        try:
                            self._fetch_historical_candles(sym, tf)
                            # Dispatch simulated tick from REST
                            for cb in self.callbacks:
                                try:
                                    cb(sym, tf, self.candle_cache[(sym, tf)].copy(), "STALE")
                                except Exception as e:
                                    logger.error(f"[SCANNER] Callback error (REST Fallback) for {sym} ({tf}): {e}")
                            
                            self.last_candle_close[(sym, tf)] = now
                        except Exception as e:
                            logger.error(f"[SCANNER] REST Fallback failed for {sym} ({tf}): {e}")
                
                if sym_stale:
                    self.data_health_status[sym] = "STALE"
                else:
                    self.data_health_status[sym] = "OK"
                    all_stale = False
            
            # If all symbols are completely dead for > 120 seconds, reconnect socket atomically
            if all_stale and len(self.symbols) > 0:
                max_elapsed = max([(now - self.last_market_update.get((s, t), now)).total_seconds() for s in self.symbols for t in self.timeframes])
                if max_elapsed > 120:
                    logger.critical("[SCANNER] 🚨 Entire websocket appears DEAD. Attempting atomic reconnect...")
                    try:
                        old_twm = self.twm
                        # Build new TWM before stopping old one — prevents gap window
                        new_twm = ThreadedWebsocketManager(testnet=self.testnet)
                        new_twm.start()
                        streams = [f"{sym.lower()}@kline_{tf}" for sym in self.symbols for tf in self.timeframes]
                        new_twm.start_multiplex_socket(callback=self._handle_socket_message, streams=streams)
                        # Swap atomically
                        self.twm = new_twm
                        # Now safely stop the old one
                        try:
                            old_twm.stop()
                        except Exception as stop_err:
                            logger.warning(f"[SCANNER] Old TWM stop error (non-fatal): {stop_err}")
                        # Reset staleness timers only after new stream confirmed
                        for sym in self.symbols:
                            for tf in self.timeframes:
                                self.last_market_update[(sym, tf)] = datetime.datetime.utcnow()
                        logger.info("[SCANNER] Atomic reconnect completed successfully.")
                    except Exception as e:
                        logger.error(f"[SCANNER] Atomic reconnect failed: {e}")
                        
            time.sleep(3)  # Reduced from 5s for faster stale detection

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
            
        self.last_candle_close[(symbol, tf)] = datetime.datetime.utcnow()
            
        # Parse new row
        vol = float(kline.get('v', 0))
        taker_vol = float(kline.get('V', vol / 2.0))
        buy_vol = taker_vol
        sell_vol = max(0.0, vol - buy_vol)
        vol_delta = buy_vol - sell_vol

        candle_ts = pd.to_datetime(kline.get('t', int(time.time() * 1000)), unit='ms')
        new_row = {
            'timestamp': candle_ts,
            'close_time': pd.to_datetime(kline.get('T', int(time.time() * 1000)), unit='ms'),
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

        cached_copy = None
        with self._cache_lock:
            if (symbol, tf) in self.candle_cache:
                df = self.candle_cache[(symbol, tf)]
                
                # Duplicate candle prevention: skip if this timestamp already exists
                if 'timestamp' in df.columns and len(df) > 0:
                    existing_ts = df['timestamp'].iloc[-1]
                    if existing_ts == candle_ts:
                        logger.debug(f"[DUPLICATE_CANDLE_SKIPPED] {symbol} {tf} ts={candle_ts} already in cache")
                        return
                
                # Append and sort by timestamp to handle any out-of-order delivery
                df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                df = df.sort_values('timestamp', ascending=True).reset_index(drop=True)
                if len(df) > 250:
                    df = df.iloc[-250:].reset_index(drop=True)
                self.candle_cache[(symbol, tf)] = df
            else:
                self.candle_cache[(symbol, tf)] = pd.DataFrame([new_row])
            cached_copy = self.candle_cache[(symbol, tf)].copy()
            
        # Dispatch event to registered callbacks
        if cached_copy is not None:
            for cb in self.callbacks:
                try:
                    cb(symbol, tf, cached_copy, self.data_health_status.get(symbol, "OK"))
                except Exception as e:
                    logger.error(f"[SCANNER] Callback error for {symbol} ({tf}): {e}")
