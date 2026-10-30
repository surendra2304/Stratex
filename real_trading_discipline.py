"""Computational implementations of Cloud9's publicly listed trading topics.

Public topic headings are a scope guide, not evidence for advertised win rates.
No access code, locked lessons or trading-performance claims are reproduced.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import statistics
from threading import RLock
import time


def positive(*values: float) -> None:
    if any(not math.isfinite(v) or v<=0 for v in values):
        raise ValueError('finite positive values required')


@dataclass(frozen=True)
class RiskPlan:
    side: str
    entry: float
    stop: float
    target: float
    quantity: float
    risk_budget: float
    estimated_loss: float
    reward_risk: float
    notional: float
    allowed: bool
    reasons: list[str]


class TradingDisciplineEngine:
    def plan(self, equity: float, entry: float, stop: float, target: float,
             risk_fraction: float = 0.01, side: str = 'buy', lot_size: float = 1,
             fee_fraction: float = 0.001, max_exposure: float = 1,
             min_reward_risk: float = 2, existing_risk: float = 0,
             max_portfolio_risk: float = 0.05) -> RiskPlan:
        positive(equity,entry,stop,target,lot_size,max_exposure,min_reward_risk)
        if not 0<risk_fraction<=0.05 or not 0<=fee_fraction<0.1 or not 0<=existing_risk or not 0<max_portfolio_risk<=1:
            raise ValueError('invalid risk limits')
        if not all(math.isfinite(v) for v in (risk_fraction,fee_fraction,existing_risk,max_portfolio_risk)):
            raise ValueError('nonfinite risk limits')
        if side not in {'buy','sell'} or (not stop<entry<target if side=='buy' else not target<entry<stop):
            raise ValueError('stop and target must bracket entry in the trade direction')
        loss = abs(entry-stop)+(entry+stop)*fee_fraction
        gain = abs(target-entry)-(entry+target)*fee_fraction
        budget = min(equity*risk_fraction,max(0,equity*max_portfolio_risk-existing_risk))
        quantity = math.floor(min(budget/loss, equity*max_exposure/entry)/lot_size)*lot_size
        reasons = []
        rr = gain/loss
        if rr<min_reward_risk:
            reasons.append('Reward/risk after fees below minimum')
        if quantity<=0:
            reasons.append('Risk/exposure budget cannot fund one lot')
        return RiskPlan(side,entry,stop,target,quantity,budget,quantity*loss,rr,quantity*entry,not reasons,reasons)

    @staticmethod
    def expectancy(r_multiples: list[float]) -> dict:
        if not r_multiples or any(not math.isfinite(r) for r in r_multiples):
            raise ValueError('finite R observations required')
        wins = [r for r in r_multiples if r>0]
        losses = [r for r in r_multiples if r<0]
        return {'trades':len(r_multiples),'win_rate':len(wins)/len(r_multiples),
                'expectancy_r':statistics.mean(r_multiples),
                'average_win_r':statistics.mean(wins) if wins else 0,
                'average_loss_r':statistics.mean(losses) if losses else 0,
                'profit_factor':sum(wins)/-sum(losses) if losses else None,
                'breakeven_win_rate':(-statistics.mean(losses)/(statistics.mean(wins)-statistics.mean(losses))) if wins and losses else None}

    @staticmethod
    def market_structure(bars: list[dict], window: int = 3) -> dict:
        if window<1 or len(bars)<2*window+1:
            raise ValueError('insufficient bars for swing window')
        for b in bars:
            positive(b['open'],b['high'],b['low'],b['close'])
            if b['high']<max(b['open'],b['close'],b['low']) or b['low']>min(b['open'],b['close']):
                raise ValueError('inconsistent OHLC')
        highs, lows = [], []
        for i in range(window,len(bars)-window):
            neighbors = bars[i-window:i]+bars[i+1:i+window+1]
            if all(bars[i]['high']>b['high'] for b in neighbors):
                highs.append({'index':i,'price':bars[i]['high']})
            if all(bars[i]['low']<b['low'] for b in neighbors):
                lows.append({'index':i,'price':bars[i]['low']})
        trend = 'range'
        if len(highs)>=2 and len(lows)>=2:
            dh,dl = highs[-1]['price']-highs[-2]['price'],lows[-1]['price']-lows[-2]['price']
            trend = 'uptrend' if dh>0 and dl>0 else 'downtrend' if dh<0 and dl<0 else 'transition'
        ranges = [b['high']-b['low'] if i==0 else max(b['high']-b['low'],abs(b['high']-bars[i-1]['close']),abs(b['low']-bars[i-1]['close'])) for i,b in enumerate(bars)]
        atr = statistics.mean(ranges[-14:])
        close = bars[-1]['close']
        previous = bars[:-1]
        resistance = max(b['high'] for b in previous[-20:])
        support = min(b['low'] for b in previous[-20:])
        volumes = [b.get('volume',0) for b in previous[-20:]]
        baseline = statistics.mean(volumes)
        relative_volume = bars[-1].get('volume',0)/baseline if baseline>0 else None
        breakout = 'above' if close>resistance else 'below' if close<support else 'none'
        fakeout = 'upside_rejection' if bars[-1]['high']>resistance and close<=resistance else 'downside_rejection' if bars[-1]['low']<support and close>=support else 'none'
        last, prior = bars[-1],bars[-2]
        body = abs(last['close']-last['open'])
        span = last['high']-last['low']
        patterns = []
        if span and body/span<=0.1:
            patterns.append('doji')
        if body>0 and min(last['open'],last['close'])-last['low']>=2*body and last['high']-max(last['open'],last['close'])<=body:
            patterns.append('hammer')
        if last['close']>last['open'] and prior['close']<prior['open'] and last['open']<=prior['close'] and last['close']>=prior['open']:
            patterns.append('bullish_engulfing')
        displacement = abs(close-bars[max(0,len(bars)-15)]['close'])
        segment = bars[-15:]
        path = sum(abs(b['close']-a['close']) for a,b in zip(segment,segment[1:]))
        efficiency = displacement/path if path else 0
        return {'swing_highs':highs,'swing_lows':lows,'structure':trend,'atr14':atr,'support':support,'resistance':resistance,
                'breakout':breakout,'fakeout':fakeout,'swings_confirmed_through_index':len(bars)-window-1,'volume_confirmed':breakout!='none' and relative_volume is not None and relative_volume>=1.5,
                'relative_volume':relative_volume,'patterns':patterns,'efficiency_ratio':efficiency,
                'regime':'trend' if efficiency>=0.6 else 'range' if efficiency<=0.3 else 'transition',
                'suggested_long_stop':support-atr*0.5,'suggested_short_stop':resistance+atr*0.5}

    @staticmethod
    def manage_position(side: str, entry: float, initial_stop: float, current_stop: float,
                        price: float, favorable_extreme: float, atr: float,
                        trail_multiple: float = 2, scale_after_r: float = 2) -> dict:
        positive(entry,initial_stop,current_stop,price,favorable_extreme,atr,trail_multiple,scale_after_r)
        if side not in {'buy','sell'} or (initial_stop>=entry if side=='buy' else initial_stop<=entry):
            raise ValueError('invalid position side or initial stop')
        risk = abs(entry-initial_stop)
        open_r = (price-entry)/risk*(1 if side=='buy' else -1)
        if side=='buy':
            if favorable_extreme<max(entry,price):
                raise ValueError('favorable extreme must include entry and current price')
            stop = max(current_stop,favorable_extreme-atr*trail_multiple)
            exit_now = price<=stop
        else:
            if favorable_extreme>min(entry,price):
                raise ValueError('favorable extreme must include entry and current price')
            stop = min(current_stop,favorable_extreme+atr*trail_multiple)
            exit_now = price>=stop
        return {'stop':stop,'open_r':open_r,'exit_now':exit_now,
                'scale_eligible':open_r>=scale_after_r and not exit_now,
                'rule':'Never widen stops or add to a losing position. New size requires a separate risk plan.'}

    @staticmethod
    def correlation_exposure(returns: dict[str,list[float]], weights: dict[str,float], threshold: float = 0.8) -> dict:
        if not 0<threshold<=1 or any(not math.isfinite(w) for w in weights.values()):
            raise ValueError('invalid correlation inputs')
        pairs = []
        names = list(weights)
        for i,left in enumerate(names):
            for right in names[i+1:]:
                a,b = returns.get(left,[]),returns.get(right,[])
                if len(a)!=len(b) or len(a)<3 or any(not math.isfinite(x) for x in a+b):
                    raise ValueError('aligned finite return series required')
                ma,mb = statistics.mean(a),statistics.mean(b)
                denominator = math.sqrt(sum((x-ma)**2 for x in a)*sum((y-mb)**2 for y in b))
                corr = sum((x-ma)*(y-mb) for x,y in zip(a,b))/denominator if denominator else None
                if corr is not None and abs(corr)>=threshold:
                    pairs.append({'left':left,'right':right,'correlation':corr,
                                  'same_direction_risk':corr*weights[left]*weights[right]>0,
                                  'gross_weight':abs(weights[left])+abs(weights[right])})
        return {'correlated_pairs':pairs,'gross_exposure':sum(abs(w) for w in weights.values())}


class TradeJournal:
    """Append-only JSONL journal and deterministic revenge-trade/drawdown gates."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock = RLock()

    def record(self, trade_id: str, symbol: str, pnl: float, initial_risk: float,
               followed_plan: bool, timestamp: float | None = None, notes: str = '') -> dict:
        positive(initial_risk)
        if not trade_id or not symbol or not math.isfinite(pnl):
            raise ValueError('invalid journal entry')
        row = {'trade_id':trade_id,'symbol':symbol,'pnl':pnl,'initial_risk':initial_risk,'r':pnl/initial_risk,
               'followed_plan':followed_plan,'timestamp':time.time() if timestamp is None else timestamp,'notes':notes[:2000]}
        if not math.isfinite(row['timestamp']):
            raise ValueError('invalid journal timestamp')
        with self.lock:
            if any(t['trade_id']==trade_id for t in self.entries()):
                raise ValueError('trade already journaled')
            with self.path.open('a',encoding='utf-8') as handle:
                handle.write(json.dumps(row)+'\n')
        return row

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def review(self, now: float | None = None, cooldown: float = 1800, max_daily_loss_r: float = 3) -> dict:
        now = time.time() if now is None else now
        rows = sorted((r for r in self.entries() if r['timestamp']<=now),key=lambda r:r['timestamp'])
        recent = [r for r in rows if now-r['timestamp']<86400]
        reasons = []
        if rows and rows[-1]['pnl']<0 and now-rows[-1]['timestamp']<cooldown:
            reasons.append('Post-loss cooldown active')
        if sum(r['r'] for r in recent)<=-max_daily_loss_r:
            reasons.append('Rolling daily loss limit reached')
        return {'allowed':not reasons,'reasons':reasons,'rolling_daily_r':sum(r['r'] for r in recent),
                'plan_adherence':sum(r['followed_plan'] for r in rows)/len(rows) if rows else None,
                'statistics':TradingDisciplineEngine.expectancy([r['r'] for r in rows]) if rows else None}
