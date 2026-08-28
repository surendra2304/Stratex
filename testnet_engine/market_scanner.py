import datetime
import threading
import time

import pandas as pd
from binance.client import Client

from logger import get_logger

logger = get_logger("market_scanner")

class MockThreadedWebsocketManager:
    """Mock TWM shim to preserve backwards compatibility for unit tests without launching sockets."""
    def __init__(self, testnet=True):
        self.testnet = testnet
    def start(self):
        pass
    def stop(self):
        pass
    def start_multiplex_socket(self, callback=None, streams=None):
        pass
    def start_futures_multiplex_socket(self, callback=None, streams=None):
        pass

# Preserve ThreadedWebsocketManager symbol in module namespace for test compatibility
ThreadedWebsocketManager = MockThreadedWebsocketManager

class MarketScanner:
    def __init__(self, symbols, timeframes=None, timeframe=None, testnet=True, is_futures=False):
        self.symbols = symbols
        if timeframes is not None:
            self.timeframes = timeframes if isinstance(timeframes, list) else [timeframes]
        elif timeframe is not None:
            self.timeframes = [timeframe]
        else:
            self.timeframes = ["1m"]
            
        self.testnet = testnet
        self.is_futures = is_futures
        
        self.client = Client("", "", testnet=testnet)
        self.twm = MockThreadedWebsocketManager(testnet=testnet)
        
        # In-memory OHLCV cache per (symbol, timeframe) and symbol
        self.candle_cache = {} 
        self.last_market_update = {} # track by (symbol, timeframe)
        self.last_candle_close = {} # track actual candle closes
        self.data_health_status = {sym: "UNKNOWN" for sym in symbols}
        self.callbacks = []
        
        self._cache_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._poll_thread = None
        
    def _fetch_historical_candles(self, symbol, tf):
        """Initializes or refreshes the cache with historical candles via REST (Spot or Futures).

        Binance TESTNET caps klines at ~101-500 per request.
        Paginate backwards until the target depth is reached.
        """
        TARGET_BARS = 250
        try:
            klines = []
            end_id = None
            for _ in range(6):  # hard cap: 6 pages ≈ 600 bars max
                params = {"symbol": symbol, "interval": tf, "limit": 250}
                if end_id is not None:
                    params["endTime"] = end_id
                
                if self.is_futures:
                    page = self.client.futures_klines(**params)
                else:
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
            with self._cache_lock:
                self.candle_cache[(symbol, tf)] = clean_df
        except Exception as e:
            logger.error(f"[SCANNER] Failed to fetch historical data for {symbol} ({tf}): {e}")

    def start(self):
        """Initializes the candle cache and starts the REST polling engine."""
        logger.info(f"[SCANNER] Clearing stale cache and initializing {len(self.symbols)} symbols across {len(self.timeframes)} timeframes via REST on boot...")
        with self._cache_lock:
            self.candle_cache.clear()
            self.last_market_update.clear()
            self.last_candle_close.clear()

        boot_now = datetime.datetime.utcnow()
        for sym in self.symbols:
            for tf in self.timeframes:
                self._fetch_historical_candles(sym, tf)
                self.last_market_update[(sym, tf)] = boot_now
                self.last_candle_close[(sym, tf)] = boot_now
            self.data_health_status[sym] = "OK"
            time.sleep(0.1) # Small delay to respect REST rate limits during init
            
        logger.info(f"[SCANNER] Starting multi-timeframe REST polling engine (60s cycle, 0.5s inter-call sleep for {len(self.symbols)} assets × {len(self.timeframes)} timeframes)...")
        print(f"[SCANNER] 📡 Active REST Polling: {len(self.symbols)} assets × {len(self.timeframes)} timeframes...")
        
        # Start REST polling worker thread
        self._poll_thread = threading.Thread(target=self._rest_polling_loop, daemon=True)
        self._poll_thread.start()
        
    def stop(self):
        self._stop_event.set()

    def _poll_single_symbol_tf(self, symbol, tf):
        """Polls Binance REST API for the latest candles of a single (symbol, tf), updates cache, and triggers callback if new closed candle."""
        try:
            params = {"symbol": symbol, "interval": tf, "limit": 250}
            if self.is_futures:
                klines = self.client.futures_klines(**params)
            else:
                klines = self.client.get_klines(**params)

            if not klines or len(klines) < 2:
                return

            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume', 'taker_buy_base_asset_volume']:
                df[col] = df[col].astype(float)

            # Drop unclosed candle
            now_utc = datetime.datetime.utcnow()
            closed_df = df[df['close_time'] <= now_utc].copy()
            if closed_df.empty:
                return

            closed_df['buy_vol'] = closed_df['taker_buy_base_asset_volume']
            closed_df['sell_vol'] = closed_df['volume'] - closed_df['buy_vol']
            closed_df['vol_delta'] = closed_df['buy_vol'] - closed_df['sell_vol']
            
            clean_df = closed_df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'vol_delta', 'buy_vol', 'sell_vol', 'close_time']].copy()
            clean_df = clean_df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
            if len(clean_df) > 250:
                clean_df = clean_df.iloc[-250:].reset_index(drop=True)

            latest_candle = clean_df.iloc[-1]
            latest_ts = latest_candle['timestamp']
            
            is_new_candle = False
            with self._cache_lock:
                prev_df = self.candle_cache.get((symbol, tf))
                if prev_df is None or prev_df.empty:
                    is_new_candle = True
                else:
                    prev_ts = prev_df['timestamp'].iloc[-1]
                    if latest_ts > prev_ts:
                        is_new_candle = True

                self.candle_cache[(symbol, tf)] = clean_df
                self.last_market_update[(symbol, tf)] = now_utc
                if is_new_candle:
                    self.last_candle_close[(symbol, tf)] = now_utc

                cached_copy = clean_df.copy()

            self.data_health_status[symbol] = "OK"
            if not hasattr(self, 'tick_counts'):
                self.tick_counts = {}
            self.tick_counts[(symbol, tf)] = self.tick_counts.get((symbol, tf), 0) + 1

            if is_new_candle and cached_copy is not None:
                logger.info(f"[REST_CANDLE_CLOSED] {symbol} {tf} | Closed: {latest_candle['close']} | Vol: {latest_candle['volume']:.2f} | TS: {latest_ts}")
                for cb in self.callbacks:
                    try:
                        cb(symbol, tf, cached_copy, "OK")
                    except Exception as cb_err:
                        logger.error(f"[SCANNER] Callback error for {symbol} ({tf}): {cb_err}")

        except Exception as err:
            err_msg = str(err)
            # Catch and suppress read loop closed error if any residual client throws it
            if "Read loop has been closed" in err_msg:
                logger.debug(f"[SCANNER] Suppressed closed loop error on {symbol} ({tf}): {err_msg}")
            else:
                logger.warning(f"[SCANNER_REST_POLL_ERROR] Error fetching {symbol} ({tf}): {err_msg}")

    def _rest_polling_loop(self):
        """Continuous REST polling loop: iterates across all symbols and timeframes every 60 seconds with 0.5s rate-limit pause."""
        logger.info("[SCANNER] REST polling thread started.")
        while not self._stop_event.is_set():
            loop_start = time.time()
            try:
                for sym in self.symbols:
                    if self._stop_event.is_set():
                        break
                    for tf in self.timeframes:
                        if self._stop_event.is_set():
                            break
                        self._poll_single_symbol_tf(sym, tf)
                        # 0.5-second sleep between REST calls for rate limit safety and low CPU usage
                        time.sleep(0.5)

            except Exception as e:
                err_msg = str(e)
                if "Read loop has been closed" in err_msg:
                    logger.debug(f"[SCANNER] Suppressed loop exception: {err_msg}")
                else:
                    logger.error(f"[SCANNER_LOOP_ERROR] Error in REST polling cycle: {e}")

            elapsed = time.time() - loop_start
            remaining_sleep = max(1.0, 60.0 - elapsed)
            # Sleep remaining interval in 1s increments to respond promptly to stop_event
            for _ in range(int(remaining_sleep)):
                if self._stop_event.is_set():
                    break
                time.sleep(1.0)

    def register_callback(self, callback_func):
        """Register a function to be called when a candle closes. Signature: cb(symbol, tf, df, health)"""
        self.callbacks.append(callback_func)

    def _handle_socket_message(self, msg):
        """Process incoming socket/test message with crash-proof error isolation (kept for test compatibility)."""
        try:
            if not isinstance(msg, dict):
                return
            data = msg.get('data', msg)
            if not isinstance(data, dict):
                return
                
            kline = data.get('k')
            symbol = data.get('s', '')
            if not symbol and kline:
                symbol = kline.get('s', '')
                
            if not kline or not isinstance(kline, dict):
                return
                
            if not symbol:
                return
                
            tf = kline.get('i', self.timeframes[0] if self.timeframes else "1m")
            is_closed = kline.get('x', True)
            
            now_utc = datetime.datetime.utcnow()
            self.last_market_update[(symbol, tf)] = now_utc
            self.data_health_status[symbol] = "OK"
            if not hasattr(self, 'tick_counts'):
                self.tick_counts = {}
            self.tick_counts[(symbol, tf)] = self.tick_counts.get((symbol, tf), 0) + 1
            
            if not is_closed:
                return
                
            self.last_candle_close[(symbol, tf)] = now_utc
                
            vol = float(kline.get('v', 0))
            taker_vol = float(kline.get('V', vol / 2.0))
            buy_vol = taker_vol
            sell_vol = max(0.0, vol - buy_vol)
            vol_delta = buy_vol - sell_vol

            candle_ts = pd.to_datetime(kline.get('t', int(time.time() * 1000)), unit='ms')
            new_row = {
                'timestamp': candle_ts,
                'close_time': pd.to_datetime(kline.get('T', int(time.time() * 1000)), unit='ms'),
                'open': float(kline.get('o', 0.0)),
                'high': float(kline.get('h', 0.0)),
                'low': float(kline.get('l', 0.0)),
                'close': float(kline.get('c', 0.0)),
                'volume': vol,
                'vol_delta': vol_delta,
                'buy_vol': buy_vol,
                'sell_vol': sell_vol
            }
            
            cached_copy = None
            with self._cache_lock:
                if (symbol, tf) in self.candle_cache:
                    df = self.candle_cache[(symbol, tf)]
                    
                    if 'timestamp' in df.columns and len(df) > 0:
                        existing_ts = df['timestamp'].iloc[-1]
                        if existing_ts == candle_ts:
                            return
                    
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    df = df.sort_values('timestamp', ascending=True).reset_index(drop=True)
                    if len(df) > 250:
                        df = df.iloc[-250:].reset_index(drop=True)
                    self.candle_cache[(symbol, tf)] = df
                elif symbol in self.candle_cache and isinstance(self.candle_cache[symbol], pd.DataFrame):
                    df = self.candle_cache[symbol]
                    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                    if len(df) > 250:
                        df = df.iloc[-250:].reset_index(drop=True)
                    self.candle_cache[symbol] = df
                else:
                    self.candle_cache[(symbol, tf)] = pd.DataFrame([new_row])
                
                cached_copy = self.candle_cache.get((symbol, tf), self.candle_cache.get(symbol))
                if cached_copy is not None:
                    cached_copy = cached_copy.copy()
                
            if cached_copy is not None:
                for cb in self.callbacks:
                    try:
                        cb(symbol, tf, cached_copy, self.data_health_status.get(symbol, "OK"))
                    except Exception as e:
                        logger.error(f"[SCANNER] Callback error for {symbol} ({tf}): {e}")

        except Exception as ws_err:
            err_str = str(ws_err)
            if "Read loop has been closed" in err_str:
                logger.debug(f"[SCANNER] Suppressed closed loop error: {err_str}")
            else:
                logger.error(f"[WS_MESSAGE_HANDLER_ERROR] Error parsing message: {ws_err}", exc_info=True)
