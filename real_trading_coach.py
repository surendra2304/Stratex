"""Cloud9 Academy-derived Stratex coaching primitives: risk first, test, journal.

This is an educational calculation engine, not a price predictor or broker. The
curriculum catalog is based on the publicly visible Trading Mastery lesson titles;
all thresholds are caller-supplied unless explicitly named as defaults.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Iterable


CURRICULUM = (
    ("risk_first", "Risk first", "Predefine invalidation, cash risk, and maximum size before entry."),
    ("position_sizing", "Position sizing", "Size from account risk and stop distance; never infer size from conviction."),
    ("stop_losses", "Stop losses", "Make stop distance explicit; model slippage and gaps rather than promising a fill."),
    ("expectancy", "R-multiples and expectancy", "Compare planned reward/risk and realized outcomes, not win rate alone."),
    ("support_resistance", "Support, resistance and trend", "Report observed swing levels as candidate context, not guaranteed barriers."),
    ("candlesticks", "Candlesticks", "Describe common candle geometry without treating patterns as predictive facts."),
    ("volume", "Volume", "Compare reported volume with its trailing baseline; label missing volume."),
    ("market_structure", "Market structure", "Summarize local swing sequences and disclose the lookback window."),
    ("psychology", "Trading psychology", "Journal plan adherence and loss-triggered behavior; never diagnose a user."),
    ("loss_review", "Why traders lose", "Review risk, fees, process and sampling uncertainty before attributing outcomes."),
    ("repeatable_edge", "Build a repeatable edge", "Require written entry, exit, sizing, and no-trade rules before testing."),
    ("journaling", "Journaling", "Preserve planned and realized R, thesis, emotion and rule adherence for review."),
    ("regime", "Trend versus range", "Separate direction from low-separation or insufficient-history regimes."),
    ("moving_averages", "Moving averages", "Show the actual windows and values; lagging averages are descriptive."),
    ("rsi_momentum", "RSI and momentum", "Calculate Wilder RSI as a bounded oscillator, not a standalone trade instruction."),
    ("breakouts", "Breakouts versus fakeouts", "Report completed closes, relative-volume gates and later failed breaks without forecasting."),
    ("risk_reward", "Risk/reward", "Compute target-to-stop distance before any position sizing."),
    ("revenge_trading", "Revenge-trade spiral", "Flag rapid risk escalation after a loss as a journal review prompt."),
    ("cut_losses", "Sizing winners and losers", "Compare realized R and distributions; avoid sizing up based on one result."),
    ("backtesting", "Backtest your own rules", "Use chronological signals, out-of-sample checks, fees and slippage; past tests do not predict returns."),
    ("correlation", "Correlation and concentration", "Measure pairwise return correlation on aligned observations; correlation is not protection."),
)


@dataclass(frozen=True)
class TechnicalReview:
    symbol: str
    regime: str
    market_structure: str
    observations: list[str]
    sma_short: float | None
    sma_long: float | None
    rsi14: float | None
    atr14: float | None
    support: float | None
    resistance: float | None
    candle: str
    volume_multiple: float | None
    breakout: str
    breakout_history: list[dict]
    lookback_bars: int
    limitations: tuple[str, ...] = (
        "Descriptive technical measurements only; no forecast, order or investment recommendation.",
        "Bar data may be incomplete or delayed; stops may slip or gap.",
    )


@dataclass(frozen=True)
class PositionPlan:
    side: str
    entry: float
    stop: float
    target: float
    account_equity: float
    risk_percent: float
    quantity: float
    planned_cash_risk: float
    reward_risk: float
    notional: float
    estimated_round_trip_cost: float
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class Expectancy:
    win_rate: float
    average_win_r: float
    average_loss_r: float
    expectancy_r: float
    breakeven_win_rate: float | None
    sample_size: int
    warning: str


@dataclass(frozen=True)
class JournalReview:
    total_trades: int
    closed_trades: int
    win_rate: float | None
    average_r: float | None
    expectancy_r: float | None
    consecutive_losses: int
    flags: list[str]


class TradingCoach:
    """Typed, deterministic Cloud9 curriculum calculations attached to Stratex."""

    def __init__(self, journal_path: str | None = None) -> None:
        self.journal_path = journal_path
        if journal_path:
            Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(journal_path) as db:
                db.execute("""CREATE TABLE IF NOT EXISTS journal (
                    id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
                    entry REAL NOT NULL, stop REAL NOT NULL, target REAL,
                    quantity REAL NOT NULL, opened_at REAL NOT NULL, closed_at REAL,
                    exit_price REAL, emotion TEXT NOT NULL, followed_plan INTEGER,
                    notes TEXT NOT NULL, planned_risk REAL NOT NULL, fees REAL NOT NULL DEFAULT 0)""")
                columns = {row[1] for row in db.execute("PRAGMA table_info(journal)")}
                if "fees" not in columns:
                    db.execute("ALTER TABLE journal ADD COLUMN fees REAL NOT NULL DEFAULT 0")

    @staticmethod
    def _finite(value: float, name: str, positive: bool = False) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        result = float(value)
        if positive and result <= 0:
            raise ValueError(f"{name} must be positive")
        return result

    @classmethod
    def review_market(cls, symbol: str, bars: Iterable, lookback: int = 50,
                      volume_threshold: float = 1.5, latest_bar_complete: bool = False) -> TechnicalReview:
        rows = list(bars)
        volume_threshold = cls._finite(volume_threshold, "volume_threshold", True)
        if not symbol.strip() or not 5 <= lookback <= 500 or len(rows) < 5 or len(rows) > 5000:
            raise ValueError("symbol and 5-5000 bars are required; lookback must be 5-500")
        for bar in rows:
            o,h,l,c,v = (cls._finite(getattr(bar,field),field) for field in ("open","high","low","close","volume"))
            if min(o,h,l,c) <= 0 or v < 0 or h < max(o,c,l) or l > min(o,c,h):
                raise ValueError("invalid OHLCV bar")
        rows = rows[-lookback:]
        closes = [float(b.close) for b in rows]
        short = sum(closes[-5:]) / 5
        long = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        if long is None:
            regime = "insufficient_history"
        elif short > long * 1.005:
            regime = "uptrend_context"
        elif short < long * 0.995:
            regime = "downtrend_context"
        else:
            regime = "range_or_mixed"
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        period = min(14, len(deltas))
        gains = [max(0.0, x) for x in deltas[:period]]
        losses = [max(0.0, -x) for x in deltas[:period]]
        avg_gain, avg_loss = sum(gains)/period, sum(losses)/period
        for delta in deltas[period:]:
            avg_gain = (avg_gain * (period - 1) + max(0.0, delta)) / period
            avg_loss = (avg_loss * (period - 1) + max(0.0, -delta)) / period
        rsi = 100.0 if avg_loss == 0 and avg_gain > 0 else 50.0 if avg_gain == avg_loss == 0 else 100 - 100/(1 + avg_gain/avg_loss)
        true_ranges = []
        for i, bar in enumerate(rows):
            previous = float(rows[i-1].close) if i else float(bar.close)
            true_ranges.append(max(float(bar.high)-float(bar.low), abs(float(bar.high)-previous), abs(float(bar.low)-previous)))
        atr = sum(true_ranges[-min(14,len(true_ranges)):]) / min(14,len(true_ranges))
        swing_highs=[(i,float(rows[i].high)) for i in range(1,len(rows)-1)
                     if rows[i].high >= rows[i-1].high and rows[i].high >= rows[i+1].high]
        swing_lows=[(i,float(rows[i].low)) for i in range(1,len(rows)-1)
                    if rows[i].low <= rows[i-1].low and rows[i].low <= rows[i+1].low]
        support=max((x for _,x in swing_lows if x <= closes[-1]),default=None)
        resistance=min((x for _,x in swing_highs if x >= closes[-1]),default=None)
        if len(swing_highs)>=2 and len(swing_lows)>=2:
            higher_highs=swing_highs[-1][1]>swing_highs[-2][1]
            higher_lows=swing_lows[-1][1]>swing_lows[-2][1]
            lower_highs=swing_highs[-1][1]<swing_highs[-2][1]
            lower_lows=swing_lows[-1][1]<swing_lows[-2][1]
            structure="higher_highs_higher_lows" if higher_highs and higher_lows else \
                      "lower_highs_lower_lows" if lower_highs and lower_lows else "mixed_swing_structure"
        else:
            structure="insufficient_swing_points"
        latest, previous = rows[-1], rows[-2]
        candle_range=max(float(latest.high)-float(latest.low),1e-12)
        body=abs(float(latest.close)-float(latest.open))
        if body/candle_range<=0.1: candle="doji_like_small_body"
        elif latest.close>latest.open and previous.close<previous.open and latest.close>=previous.open and latest.open<=previous.close:
            candle="bullish_engulfing_shape"
        elif latest.close<latest.open and previous.close>previous.open and latest.open>=previous.close and latest.close<=previous.open:
            candle="bearish_engulfing_shape"
        else: candle="no_basic_pattern"
        prior_volumes=[float(bar.volume) for bar in rows[-21:-1] if float(bar.volume)>0]
        volume_multiple=float(latest.volume)/(sum(prior_volumes)/len(prior_volumes)) if prior_volumes else None
        prior_high=max(float(bar.high) for bar in rows[-21:-1])
        prior_low=min(float(bar.low) for bar in rows[-21:-1])
        if float(latest.close)>prior_high:
            breakout=("completed_close_above_prior_range_volume_gate_met" if latest_bar_complete and volume_multiple is not None and volume_multiple>=volume_threshold else
                      "completed_close_above_prior_range_volume_gate_not_met" if latest_bar_complete else
                      "potential_close_above_prior_range_bar_may_be_open")
        elif float(latest.close)<prior_low:
            breakout=("completed_close_below_prior_range_volume_gate_met" if latest_bar_complete and volume_multiple is not None and volume_multiple>=volume_threshold else
                      "completed_close_below_prior_range_volume_gate_not_met" if latest_bar_complete else
                      "potential_close_below_prior_range_bar_may_be_open")
        else: breakout="no_close_outside_prior_range"
        # Report observed closes that returned inside the pre-break range within
        # three bars; this is a transparent descriptive rule, not a forecast.
        breakout_history=[]
        for i in range(20,len(rows)-1):
            high=max(float(bar.high) for bar in rows[i-20:i])
            low=min(float(bar.low) for bar in rows[i-20:i])
            direction="up" if rows[i].close>high else "down" if rows[i].close<low else ""
            if not direction: continue
            level=high if direction=="up" else low
            future=rows[i+1:min(i+4,len(rows))]
            failed=any((bar.close<=level if direction=="up" else bar.close>=level) for bar in future)
            breakout_history.append({"direction":direction,"bar_index":i,"range_level":level,
                "status":"failed_close_returned_inside_within_3_bars" if failed else
                         "no_return_inside_observed_within_3_bars" if len(future)>=3 else "followup_window_incomplete"})
        observations=[f"Regime and swing labels use the trailing {len(closes)} supplied bars.",
                      "Candle shapes describe OHLC geometry and do not imply future direction.",
                      "A single close cannot distinguish a durable breakout from a later failed breakout."]
        if long is None: observations.append("Twenty-bar moving average unavailable; history is insufficient.")
        if support is None or resistance is None: observations.append("One or both local swing levels are unavailable in the selected window.")
        if volume_multiple is None: observations.append("Relative volume unavailable because no positive baseline volume was provided.")
        return TechnicalReview(symbol,regime,structure,observations,short,long,rsi,atr,support,resistance,
                               candle,volume_multiple,breakout,breakout_history,len(rows))

    @classmethod
    def size_position(cls, side: str, entry: float, stop: float, target: float,
                      account_equity: float, risk_percent: float = 1.0,
                      max_notional_multiple: float = 1.0, lot_size: float = 1.0,
                      fee_percent: float = 0.0, slippage_percent: float = 0.0) -> PositionPlan:
        if side not in {"long", "short"}: raise ValueError("side must be long or short")
        entry=cls._finite(entry,"entry",True); stop=cls._finite(stop,"stop",True)
        target=cls._finite(target,"target",True); equity=cls._finite(account_equity,"account_equity",True)
        risk_percent=cls._finite(risk_percent,"risk_percent",True); leverage=cls._finite(max_notional_multiple,"max_notional_multiple",True)
        lot_size=cls._finite(lot_size,"lot_size",True); fee_percent=cls._finite(fee_percent,"fee_percent")
        slippage_percent=cls._finite(slippage_percent,"slippage_percent")
        if risk_percent>100 or leverage>100 or lot_size>1e9 or not 0<=fee_percent<=10 or not 0<=slippage_percent<=10:
            raise ValueError("risk, leverage, fee, slippage, or lot-size bounds exceeded")
        if (side=="long" and not stop<entry<target) or (side=="short" and not target<entry<stop):
            raise ValueError("stop and target must be on opposite sides of entry")
        unit_distance=abs(entry-stop)+(entry+stop)*(fee_percent+slippage_percent)/100
        budget=equity*risk_percent/100
        raw_quantity=budget/unit_distance
        max_quantity=equity*leverage/entry
        quantity=math.floor(min(raw_quantity,max_quantity)/lot_size)*lot_size
        actual_risk=quantity*unit_distance
        reward_risk=abs(target-entry)/abs(entry-stop)
        notional=quantity*entry
        estimated_cost=quantity*(entry+stop)*fee_percent/100
        warnings=["Modelled sizing only; actual stop execution may gap or slip beyond this estimate.",
                  "Review instrument contract size, taxes, liquidity, margin and broker rules before use."]
        if quantity==0:warnings.append("No whole lot fits the supplied risk budget and notional cap.")
        if risk_percent>1:warnings.append("Risk budget exceeds the 1% application default; this is not a Cloud9 rule or personal recommendation.")
        return PositionPlan(side,entry,stop,target,equity,risk_percent,quantity,actual_risk,reward_risk,
                            notional,estimated_cost,tuple(warnings))

    @classmethod
    def expectancy(cls, results_r: Iterable[float]) -> Expectancy:
        values=[cls._finite(x,"R result") for x in results_r]
        if not values or len(values)>100000:raise ValueError("between 1 and 100000 trade results required")
        wins=[x for x in values if x>0];losses=[-x for x in values if x<0]
        win_rate=len(wins)/len(values);avg_win=sum(wins)/len(wins) if wins else 0.0
        avg_loss=sum(losses)/len(losses) if losses else 0.0
        breakeven=avg_loss/(avg_win+avg_loss) if avg_win+avg_loss else None
        return Expectancy(win_rate,avg_win,avg_loss,sum(values)/len(values),breakeven,len(values),
                          "Historical sample expectancy is descriptive, sensitive to fees and selection bias, and not a forecast.")

    def record_trade(self, *, trade_id: str, symbol: str, side: str, entry: float, stop: float,
                     quantity: float, target: float | None = None, opened_at: float | None = None,
                     exit_price: float | None = None, closed_at: float | None = None,
                     emotion: str = "", followed_plan: bool | None = None, notes: str = "",
                     fees: float = 0.0) -> dict:
        if not self.journal_path: raise RuntimeError("trade journal is not configured")
        if not trade_id or len(trade_id)>100 or not symbol.strip() or len(symbol)>40 or side not in {"long","short"}:
            raise ValueError("invalid trade identity")
        entry=self._finite(entry,"entry",True);stop=self._finite(stop,"stop",True);quantity=self._finite(quantity,"quantity",True)
        if (side=="long" and stop>=entry) or (side=="short" and stop<=entry):raise ValueError("stop must be on the loss side of entry")
        opened_at=time.time() if opened_at is None else self._finite(opened_at,"opened_at",True)
        if target is not None:target=self._finite(target,"target",True)
        if (exit_price is None)!=(closed_at is None):raise ValueError("exit_price and closed_at must be provided together")
        if exit_price is not None:
            exit_price=self._finite(exit_price,"exit_price",True);closed_at=self._finite(closed_at,"closed_at",True)
            if closed_at<opened_at:raise ValueError("closed_at precedes opened_at")
        fees=self._finite(fees,"fees")
        if fees<0 or len(emotion)>300 or len(notes)>4000:raise ValueError("journal fees or text exceed limits")
        risk=abs(entry-stop)*quantity
        record=(trade_id,symbol.upper(),side,entry,stop,target,quantity,opened_at,closed_at,exit_price,
                emotion,None if followed_plan is None else int(followed_plan),notes,risk,fees)
        try:
            with sqlite3.connect(self.journal_path) as db:
                db.execute("INSERT INTO journal (id,symbol,side,entry,stop,target,quantity,opened_at,closed_at,exit_price,emotion,followed_plan,notes,planned_risk,fees) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",record)
        except sqlite3.IntegrityError as error:
            raise ValueError("trade ID already exists") from error
        return {"trade_id":trade_id,"status":"closed" if exit_price is not None else "open","planned_cash_risk":risk}

    def review_journal(self, limit: int = 500) -> JournalReview:
        if not self.journal_path:raise RuntimeError("trade journal is not configured")
        if not 1<=limit<=10000:raise ValueError("limit must be between 1 and 10000")
        with sqlite3.connect(self.journal_path) as db:
            rows=db.execute("SELECT symbol,side,entry,stop,quantity,opened_at,closed_at,exit_price,emotion,followed_plan,planned_risk,fees FROM journal ORDER BY opened_at DESC LIMIT ?",(limit,)).fetchall()
        closed=[];flags=[];chronological=sorted(rows,key=lambda row:row[5]);loss_streak=peak_streak=0;last_closed=None
        for row in chronological:
            symbol,side,entry,stop,qty,opened,closed_at,exit_price,emotion,followed,risk,fees=row
            if closed_at is None:
                if followed==0:flags.append(f"{symbol}: open trade recorded as not following plan")
                continue
            pnl=(exit_price-entry)*qty if side=="long" else (entry-exit_price)*qty
            result_r=(pnl-fees)/risk if risk else 0.0
            closed.append(result_r);loss_streak=loss_streak+1 if result_r<0 else 0;peak_streak=max(peak_streak,loss_streak)
            if followed==0:flags.append(f"{symbol}: plan adherence marked false")
            if emotion.strip():flags.append(f"{symbol}: emotion note recorded; review without judgment")
            if result_r<0 and last_closed and 0<=opened-last_closed[0]<3600 and risk>last_closed[1]*1.5:
                flags.append(f"{symbol}: risk increased >50% within one hour of a loss; check for revenge-trading pattern")
            last_closed=(closed_at,risk) if result_r<0 else None
        stats=self.expectancy(closed) if closed else None
        return JournalReview(len(rows),len(closed),sum(x>0 for x in closed)/len(closed) if closed else None,
            sum(closed)/len(closed) if closed else None,stats.expectancy_r if stats else None,peak_streak,flags)

    @classmethod
    def correlations(cls, series: dict[str,list[float]], threshold: float=0.8) -> dict:
        threshold=cls._finite(threshold,"threshold")
        if not -1<=threshold<=1 or len(series)<2 or len(series)>100:raise ValueError("two to 100 series and a correlation threshold in [-1,1] required")
        names=list(series);values={name:[cls._finite(x,name,True) for x in series[name]] for name in names}
        matrix={};overlaps={};flags=[]
        for left in names:
            matrix[left]={};overlaps[left]={}
            for right in names:
                n=min(len(values[left]),len(values[right]));a,b=values[left][-n:],values[right][-n:]
                ra=[a[j]/a[j-1]-1 for j in range(1,n) if a[j-1]!=0]
                rb=[b[j]/b[j-1]-1 for j in range(1,n) if b[j-1]!=0]
                if left==right:corr=1.0 if len(ra)>=2 else None
                elif len(ra)<2:corr=None
                else:
                    ma,mb=sum(ra)/len(ra),sum(rb)/len(rb)
                    cov=sum((x-ma)*(y-mb) for x,y in zip(ra,rb));va=sum((x-ma)**2 for x in ra);vb=sum((y-mb)**2 for y in rb)
                    corr=cov/math.sqrt(va*vb) if va and vb else None
                matrix[left][right]=corr;overlaps[left][right]=min(len(ra),len(rb))
                if left<right and corr is not None and corr>=threshold:
                    flags.append({"pair":[left,right],"correlation":corr,"observations":overlaps[left][right],
                                  "note":"High historical positive correlation can indicate overlapping exposure; it does not ensure future co-movement."})
        return {"correlations":matrix,"observations":overlaps,"concentration_flags":flags,
                "warning":"Correlation uses aligned trailing observations and is not a guarantee of diversification."}

    @staticmethod
    def lesson_catalog() -> list[dict[str,str]]:
        return [{"key":key,"title":title,"practice":practice} for key,title,practice in CURRICULUM]
