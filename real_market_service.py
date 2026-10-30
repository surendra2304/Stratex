"""Jarvis live watchlists: bounded Yahoo chart acquisition, cache and spoken briefing."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import json
import math
import re
from threading import RLock
import time
from typing import Callable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from Stratex.real_market_momentum import MarketMomentumEngine

WATCHLIST = {'^NSEI':'Nifty 50', '^BSESN':'Sensex', 'RELIANCE.NS':'Reliance Industries', 'TCS.NS':'TCS',
             'INFY.NS':'Infosys', 'HDFCBANK.NS':'HDFC Bank', 'NVDA':'Nvidia', 'AAPL':'Apple', 'BTC-USD':'Bitcoin'}


@dataclass
class MarketSnapshot:
    symbol: str
    name: str
    price: float
    previous_close: float
    currency: str
    high: float
    low: float
    closes: list[float | None]
    observed_at: float
    analysis: dict
    stale: bool = False
    error: str = ''


class MarketDataService:
    def __init__(self, fetch: Callable | None = None, ttl: float = 60,
                 clock: Callable = time.time) -> None:
        if ttl < 0 or not math.isfinite(ttl):
            raise ValueError('invalid cache ttl')
        self.fetch = fetch or self._fetch
        self.clock, self.ttl = clock, ttl
        self.cache: dict[str, MarketSnapshot] = {}
        self.lock = RLock()
        self.analysis = MarketMomentumEngine()

    @staticmethod
    def _fetch(symbol: str) -> dict:
        url = 'https://query1.finance.yahoo.com/v8/finance/chart/'+quote(symbol,safe='')+'?'+urlencode({'range':'5d','interval':'15m'})
        request = Request(url, headers={'User-Agent':'FRIDAY-Stratex/1.0','Accept':'application/json'})
        with urlopen(request, timeout=10) as response:
            raw = response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:
                raise ValueError('chart response too large')
            return json.loads(raw)

    def get(self, symbol: str) -> MarketSnapshot:
        symbol = symbol.upper().strip()
        if not re.fullmatch(r'[A-Z0-9^][A-Z0-9.^=-]{0,31}', symbol):
            raise ValueError('invalid ticker')
        with self.lock:
            cached = self.cache.get(symbol)
            if cached and self.clock()-cached.observed_at < self.ttl:
                return MarketSnapshot(**{**asdict(cached), 'stale':False, 'error':''})
        try:
            payload = self.fetch(symbol)
            result = payload.get('chart',{}).get('result')
            if not result or payload['chart'].get('error'):
                raise ValueError('provider returned no chart')
            chart = result[0]
            meta = chart['meta']
            price = float(meta['regularMarketPrice'])
            previous = float(meta.get('chartPreviousClose') or meta.get('previousClose') or price)
            closes = chart.get('indicators',{}).get('quote',[{}])[0].get('close',[])
            report = self.analysis.analyze(symbol, price, previous, closes)
            high, low = float(meta.get('regularMarketDayHigh',price)), float(meta.get('regularMarketDayLow',price))
            if not all(math.isfinite(x) and x>0 for x in (high,low)) or high<low:
                raise ValueError('invalid session range')
            snapshot = MarketSnapshot(symbol,WATCHLIST.get(symbol,meta.get('shortName',symbol)),price,previous,
                meta.get('currency','USD'),high,low,closes,self.clock(),asdict(report))
            with self.lock:
                if len(self.cache)>=256 and symbol not in self.cache:
                    self.cache.pop(min(self.cache,key=lambda k:self.cache[k].observed_at))
                self.cache[symbol] = snapshot
            return MarketSnapshot(**asdict(snapshot))
        except Exception as error:
            if cached:
                return MarketSnapshot(**{**asdict(cached), 'stale':True, 'error':type(error).__name__})
            raise RuntimeError(f'Market data unavailable for {symbol} ({type(error).__name__})') from None

    def watchlist(self, symbols: list[str] | None = None) -> dict:
        symbols = list(dict.fromkeys(symbols if symbols is not None else WATCHLIST))
        if not 1<=len(symbols)<=30:
            raise ValueError('watchlist must contain 1..30 tickers')
        def run(symbol: str) -> dict:
            try:
                return {'symbol':symbol, 'status':'ok', 'snapshot':asdict(self.get(symbol))}
            except (ValueError,RuntimeError) as error:
                return {'symbol':symbol,'status':'unavailable','error':str(error)}
        with ThreadPoolExecutor(max_workers=min(5,len(symbols))) as pool:
            records = list(pool.map(run,symbols))
        return {'observed_at':self.clock(), 'results':records,
                'briefing':' '.join(self.describe(r['snapshot']) for r in records if r['status']=='ok') or 'No current quotes available.'}

    @staticmethod
    def describe(snapshot: dict) -> str:
        percent = snapshot['analysis']['percent_change']
        return (f"{snapshot['name']} at {snapshot['currency']} {snapshot['price']:,.2f}, "
                f"{'up' if percent>=0 else 'down'} {abs(percent):.2f} percent. "
                f"{snapshot['analysis']['signal']}." + (' Cached quote; feed currently unavailable.' if snapshot['stale'] else ''))
