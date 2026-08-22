"""
research/upgrade_2026_08/expansion_study.py
Signal-frequency & profitability expansion studies for V2-spot (long-only).

Studies:
  A: V2-spot on the 1h timeframe (base 6 assets)
  B: Per-asset OOS validation on an expanded 4h universe (14 candidate alts)
  C: Post-crossover EMA20-retest entry (adds signals on the 4h TF)

Same cost model as param_study.py: 31 bps round trip, next-candle-open
entries, SL-first intrabar resolution, BTC-regime gate for longs.
IS = 2021..2024, OOS = 2024..present.
"""
import json, os, itertools
import numpy as np, pandas as pd

DATA = "research/upgrade_2026_08/data"
FEE, SLIP = 0.001, 0.0005
BASE = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"]
EXTRA = ["ADAUSDT", "AVAXUSDT", "DOTUSDT", "DOGEUSDT", "LTCUSDT", "UNIUSDT",
         "ATOMUSDT", "NEARUSDT", "ARBUSDT", "OPUSDT", "FILUSDT", "APTUSDT",
         "INJUSDT", "SUIUSDT"]
IS_END = np.datetime64("2024-01-01")

def load(sym, iv):
    raw = json.load(open(f"{DATA}/{sym}_{iv}.json"))
    ts = np.array([r[0] for r in raw], dtype="datetime64[ms]")
    o = np.array([float(r[1]) for r in raw]); h = np.array([float(r[2]) for r in raw])
    l = np.array([float(r[3]) for r in raw]); c = np.array([float(r[4]) for r in raw])
    ema = lambda span: pd.Series(c).ewm(span=span, adjust=False).mean().values
    tr = np.maximum.reduce([h - l, np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))]); tr[0] = h[0] - l[0]
    atr = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values
    up = np.diff(h, prepend=h[0]); dn = -np.diff(l, prepend=l[0])
    pdm = pd.Series(np.where(up > dn, up, 0.0)).ewm(alpha=1/14, adjust=False).mean().values
    mdm = pd.Series(np.where(dn > up, dn, 0.0)).ewm(alpha=1/14, adjust=False).mean().values
    dx = 100 * np.abs(pdm - mdm) / np.maximum(pdm + mdm, 1e-12)
    adx = pd.Series(dx).ewm(alpha=1/14, adjust=False).mean().values
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "atr": atr, "adx": adx,
            "e20": ema(20), "e50": ema(50), "e200": ema(200)}

def load_regime(iv):
    btc = load("BTCUSDT", iv)
    return btc["ts"], btc["c"] > btc["e200"]

def run(d, adx_th, sl_m, tp_m, regime, mode="crossover", retest_bars=6):
    """mode: 'crossover' (V2) or 'retest' (enter on first EMA20 touch after a
    qualified crossover within retest_bars bars — adds entries without the V1
    pullback's always-on bleed)."""
    o, h, l, c = d["o"], d["h"], d["l"], d["c"]
    e20, e50, e200, atr, adx = d["e20"], d["e50"], d["e200"], d["atr"], d["adx"]
    ts = d["ts"]
    n = len(c); trades = []; pos = None; i = 200
    pending = None  # retest state: bars remaining after qualified cross
    while i < n - 1:
        if pos is None:
            cu = e20[i] > e50[i] and e20[i-1] <= e50[i-1]
            qualified = cu and c[i] > e200[i] and adx[i] > adx_th and atr[i] > 0
            if qualified and regime[i]:
                if mode == "crossover":
                    entry = o[i+1] * (1 + SLIP); sd = sl_m * atr[i]; td = tp_m * atr[i]
                    pos = {"e": entry, "sl": entry - sd, "tp": entry + td}
                    i += 2; continue
                else:
                    pending = {"left": retest_bars, "atr": atr[i], "e": e20[i]}
            if mode == "retest" and pending and not qualified:
                if pending["left"] <= 0:
                    pending = None
                else:
                    pending["left"] -= 1
                    # retest: low touches EMA20 and bar closes bullish above EMA20
                    if l[i] <= e20[i] * 1.002 and c[i] > o[i] and c[i] >= e20[i]:
                        entry = o[i+1] * (1 + SLIP)
                        sd = sl_m * pending["atr"]; td = tp_m * pending["atr"]
                        pos = {"e": entry, "sl": entry - sd, "tp": entry + td}
                        pending = None
                        i += 2; continue
        else:
            ep = None
            if l[i] <= pos["sl"]: ep = pos["sl"] * (1 - SLIP)
            elif h[i] >= pos["tp"]: ep = pos["tp"] * (1 - SLIP)
            if ep is not None:
                trades.append((ts[i], ep - pos["e"] - (pos["e"] + ep) * FEE))
                pos = None
        i += 1
    return trades

def pf(x):
    if not x: return 0.0, 0
    g = sum(p for p in x if p > 0); gl = abs(sum(p for p in x if p <= 0))
    return (g / gl if gl else 99.0), len(x)

def split(trades):
    return [p for t, p in trades if t < IS_END], [p for t, p in trades if t >= IS_END]

def yearly(trades):
    out = []
    for lo, hi in [("2024-01-01", "2025-01-01"), ("2025-01-01", "2026-01-01"), ("2026-01-01", None)]:
        sel = [p for t, p in trades if t >= np.datetime64(lo) and (hi is None or t < np.datetime64(hi))]
        p, n = pf(sel); out.append(f"{lo[:4]}:n={n},pf={p:.2f}")
    return " ".join(out)

def main():
    # ---- Study A: 1h timeframe, base assets ----
    print("=" * 80); print("STUDY A: V2-spot on 1h (base 6 assets, BTC-regime gate on 1h)")
    rts, reg1h = load_regime("1h")
    def reg_map_1h(ts):
        idx = np.clip(np.searchsorted(rts, ts, side="right") - 1, 0, None)
        return reg1h[idx]
    d1h = {s: load(s, "1h") for s in BASE}
    for adx_th, sl_m, tp_m in itertools.product([20, 25, 30], [2.0, 3.0], [2.0, 3.0]):
        allt = []
        for s in BASE:
            d = d1h[s]
            rmask = reg_map_1h(d["ts"])
            allt.extend(run(d, adx_th, sl_m, tp_m, rmask))
        isp, oos = split(allt)
        ipf, inx = pf(isp); opf, onx = pf(oos)
        if inx >= 30 and onx >= 15:
            print(f"  ADX{adx_th} SL{sl_m} TP{tp_m} | IS n={inx:4} pf={ipf:5.2f} | OOS n={onx:4} pf={opf:5.2f} net={sum(oos):9.0f} | {yearly(allt)}")

    # ---- Study B: expanded 4h universe, per-asset ----
    print("=" * 80); print("STUDY B: per-asset OOS on 4h V2-spot params (ADX20 SL3 TP3 + regime)")
    rts4, reg4 = load_regime("4h")
    def reg_map_4(ts):
        idx = np.clip(np.searchsorted(rts4, ts, side="right") - 1, 0, None)
        return reg4[idx]
    d4 = {s: load(s, "4h") for s in BASE + EXTRA if os.path.exists(f"{DATA}/{s}_4h.json")}
    ok_assets = []
    for s in BASE + EXTRA:
        if s not in d4: continue
        allt = run(d4[s], 20, 3.0, 3.0, reg_map_4(d4[s]["ts"]))
        isp, oos = split(allt)
        ipf, inx = pf(isp); opf, onx = pf(oos)
        verdict = "OK" if (inx >= 8 and onx >= 5 and ipf > 1.1 and opf > 1.1) else "-"
        if verdict == "OK": ok_assets.append(s)
        print(f"  {s:10} IS n={inx:3} pf={ipf:5.2f} | OOS n={onx:3} pf={opf:5.2f} net={sum(oos):8.0f} {verdict}  {yearly(allt)}")
    print("  EXPANSION CANDIDATES:", ok_assets)

    # ---- Study C: retest entry on 4h base assets ----
    print("=" * 80); print("STUDY C: post-crossover EMA20-retest entry, 4h base (adds signals)")
    for rb in [4, 6, 10]:
        allt = []
        for s in BASE:
            allt.extend(run(d4[s], 20, 3.0, 3.0, reg_map_4(d4[s]["ts"]), mode="retest", retest_bars=rb))
        isp, oos = split(allt)
        ipf, inx = pf(isp); opf, onx = pf(oos)
        print(f"  retest_bars={rb:2} | IS n={inx:4} pf={ipf:5.2f} | OOS n={onx:4} pf={opf:5.2f} net={sum(oos):9.0f} | {yearly(allt)}")

if __name__ == "__main__":
    main()
