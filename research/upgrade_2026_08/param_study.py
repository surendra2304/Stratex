"""
research/upgrade_2026_08/param_study.py
Out-of-sample parameter study for the ADX+EMA strategy family. (numpy-fast)

Methodology (matches BACKTEST_ASSUMPTIONS in config_strategy.py):
- Entry on NEXT candle open after signal close (no same-candle entry)
- Conservative intrabar resolution: if SL and TP both inside one bar, SL wins
- Costs: 0.1% taker fee + 0.05% slippage per side (31 bps round trip)
- In-sample: 2021-01-01 .. 2024-01-01 ; OOS holdout: 2024-01-01 .. present
- Risk sizing: fixed 1% of $10k per trade
"""
import itertools
import json

import numpy as np
import pandas as pd

DATA_DIR = "research/upgrade_2026_08/data"
SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"]
FEE = 0.001; SLIP = 0.0005
RISK = 0.01; CAPITAL = 10_000.0
IS_END = np.datetime64("2024-01-01")

def load(sym):
    raw = json.load(open(f"{DATA_DIR}/{sym}_4h.json"))
    df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume","ct","qav","trades","tbb","tbq","ig"])
    ts = df["ts"].values.astype("datetime64[ms]")
    o = df["open"].values.astype(float); h = df["high"].values.astype(float)
    l = df["low"].values.astype(float); c = df["close"].values.astype(float)
    ema = lambda span: pd.Series(c).ewm(span=span, adjust=False).mean().values
    tr = np.maximum.reduce([h-l, np.abs(h-np.roll(c,1)), np.abs(l-np.roll(c,1))])
    tr[0]=h[0]-l[0]
    atr = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values
    up = np.diff(h, prepend=h[0]); dn = -np.diff(l, prepend=l[0])
    plus = np.where(up>dn, up, 0.0); minus = np.where(dn>up, dn, 0.0)
    pdm = pd.Series(plus).ewm(alpha=1/14, adjust=False).mean().values
    mdm = pd.Series(minus).ewm(alpha=1/14, adjust=False).mean().values
    dx = 100*np.abs(pdm-mdm)/np.maximum(pdm+mdm, 1e-12)
    adx = pd.Series(dx).ewm(alpha=1/14, adjust=False).mean().values
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c,
            "ema20": ema(20), "ema50": ema(50), "ema200": ema(200), "atr": atr, "adx": adx}

def run_variant(d, adx_th, sl_m, tp_m, trail, be):
    o,h,l,c = d["o"],d["h"],d["l"],d["c"]
    e20,e50,e200,atr,adx = d["ema20"],d["ema50"],d["ema200"],d["atr"],d["adx"]
    ts = d["ts"]
    trades = []
    n = len(c)
    pos = None
    i = 200
    while i < n-1:
        if pos is None:
            cross_up = e20[i]>e50[i] and e20[i-1]<=e50[i-1]
            cross_dn = e20[i]<e50[i] and e20[i-1]>=e50[i-1]
            if adx[i] > adx_th and atr[i] > 0:
                side = 1 if (cross_up and c[i]>e200[i]) else (-1 if (cross_dn and c[i]<e200[i]) else 0)
                if side:
                    entry = o[i+1]*(1+SLIP) if side==1 else o[i+1]*(1-SLIP)
                    sd = sl_m*atr[i]; td = tp_m*atr[i]
                    pos = {"side":side,"entry":entry,"sl":entry-side*sd,"tp":entry+side*td,
                           "atr0":atr[i],"risk":sd,"ts0":ts[i+1]}
                    i += 2; continue
        else:
            s = pos["side"]
            if be and not pos.get("be"):
                if (s==1 and h[i] >= pos["entry"]+pos["risk"]) or (s==-1 and l[i] <= pos["entry"]-pos["risk"]):
                    pos["sl"] = pos["sl"] if s==-1 else max(pos["sl"], pos["entry"])
                    if s==-1: pos["sl"] = min(pos["sl"], pos["entry"])
                    pos["be"] = True
            if trail:
                pos["sl"] = max(pos["sl"], c[i]-2*pos["atr0"]) if s==1 else min(pos["sl"], c[i]+2*pos["atr0"])
            exit_p = None
            if s==1:
                if l[i] <= pos["sl"]: exit_p = pos["sl"]*(1-SLIP)
                elif h[i] >= pos["tp"]: exit_p = pos["tp"]*(1-SLIP)
            else:
                if h[i] >= pos["sl"]: exit_p = pos["sl"]*(1+SLIP)
                elif l[i] <= pos["tp"]: exit_p = pos["tp"]*(1+SLIP)
            if exit_p is not None:
                gross = s*(exit_p-pos["entry"])
                pnl = gross - (pos["entry"]+exit_p)*FEE
                trades.append((ts[i], pnl))
                pos = None
        i += 1
    return trades

def stats(trades):
    if not trades: return {"n":0,"net":0,"pf":0,"win":0,"ret":0,"dd":0}
    pnls = np.array([p for _,p in trades])
    gw = pnls[pnls>0].sum(); gl = abs(pnls[pnls<=0].sum())
    eq = CAPITAL + np.cumsum(pnls)
    dd = ((np.maximum.accumulate(eq)-eq)/np.maximum.accumulate(eq)).max()
    return {"n":len(pnls),"net":round(pnls.sum(),1),"pf":round(gw/gl,3) if gl>0 else 99.0,
            "win":round((pnls>0).mean(),3),"ret":round(pnls.sum()/CAPITAL,4),"dd":round(dd,3)}

def main():
    data = {s: load(s) for s in SYMS}
    grid = list(itertools.product([20,25,30,35],[1.5,2.0,2.5,3.0],[2.0,3.0,4.5,6.0],[False,True],[False,True]))
    rows=[]
    for adx_th, sl_m, tp_m, trail, be in grid:
        trades=[]
        for s in SYMS: trades.extend(run_variant(data[s], adx_th, sl_m, tp_m, trail, be))
        is_=[(t,p) for t,p in trades if t<IS_END]; oos=[(t,p) for t,p in trades if t>=IS_END]
        si, so = stats(is_), stats(oos)
        if si["n"]>=30:
            rows.append({"adx":adx_th,"sl":sl_m,"tp":tp_m,"trail":trail,"be":be,"IS":si,"OOS":so})
    df = pd.DataFrame(rows)
    print("=== BASELINE (production frozen params: ADX25 SL2.0 TP3.0) ===")
    base=df[(df.adx==25)&(df.sl==2.0)&(df.tp==3.0)&(~df.trail)&(~df.be)]
    for _,r in base.iterrows(): print(f"  IS: {r.IS}  OOS: {r.OOS}")
    df["is_exp"]=df.IS.apply(lambda x:x["net"]/max(x["n"],1))
    df["oos_pf"]=df.OOS.apply(lambda x:x["pf"] if x["n"]>=10 else 0)
    df["is_pf"]=df.IS.apply(lambda x:x["pf"])
    top=df.sort_values("is_exp",ascending=False).head(20)
    print("\n=== TOP 20 BY IS NET EXPECTANCY/TRADE ===")
    for _,r in top.iterrows():
        print(f"ADX{r.adx} SL{r.sl} TP{r.tp} trail={int(r.trail)} be={int(r.be)} | IS n={r.IS['n']:3} net={r.IS['net']:8} pf={r.is_pf:5} win={r.IS['win']} | OOS n={r.OOS['n']:3} net={r.OOS['net']:8} pf={r.oos_pf:5} win={r.OOS['win']} dd={r.OOS['dd']}")
    df.to_json("research/upgrade_2026_08/results.jsonl", orient="records", lines=True)
    print("\nsaved results.jsonl")

if __name__=="__main__":
    main()
