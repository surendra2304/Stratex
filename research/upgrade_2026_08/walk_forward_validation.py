"""
research/upgrade_2026_08/walk_forward_validation.py — Upgrade 4.
3-step Anchored Walk-Forward validation for the long-only ADX+EMA family.

Folds (anchored: each train window grows):
  Train 2021-01-01..2021-12-31  -> Test 2022
  Train 2021-01-01..2022-12-31  -> Test 2023
  Train 2021-01-01..2023-12-31  -> Test 2024-01-01..2026-08 (untouched holdout)

Read-only research: does NOT touch any live config. Question it answers:
are the V2-spot parameters (ADX20 / SL3 / TP3 / retest) consistently selected
by each training window, or does the "optimum" wander (fragile edge)?

Usage: python research/upgrade_2026_08/walk_forward_validation.py
"""
import itertools
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from expansion_study import BASE, FEE, SLIP, load, run  # noqa: E402

FOLDS = [
    ("2021", "2022-01-01", "2023-01-01"),
    ("2021-2022", "2023-01-01", "2024-01-01"),
    ("2021-2023", "2024-01-01", None),
]
GRID = list(itertools.product([15, 20, 25, 30], [2.0, 2.5, 3.0], [2.0, 3.0, 4.5], [False, True]))


def pf(x):
    if not x:
        return 0.0
    g = sum(p for p in x if p > 0)
    gl = abs(sum(p for p in x if p <= 0))
    return g / gl if gl else 99.0


def main():
    btc = load("BTCUSDT", "4h")
    regime_ts, regime = btc["ts"], btc["c"] > btc["e200"]

    def rmask(ts):
        idx = np.clip(np.searchsorted(regime_ts, ts, side="right") - 1, 0, None)
        return regime[idx]

    data = {s: load(s, "4h") for s in BASE}

    def trades_for(adx_th, sl_m, tp_m, retest, lo=None, hi=None):
        out = []
        for s in BASE:
            d = data[s]
            m = rmask(d["ts"])
            allt = run(d, adx_th, sl_m, tp_m, m, mode="retest" if retest else "crossover",
                       retest_bars=10)
            for t, p in allt:
                if (lo is None or t >= np.datetime64(lo)) and (hi is None or t < np.datetime64(hi)):
                    out.append(p)
        return out

    report = {"folds": [], "live_config": "ADX20 SL3.0 TP3.0 retest=True (V2-spot rev3)"}
    for name, lo, hi in FOLDS:
        best, best_exp = None, -1e18
        for adx_th, sl_m, tp_m, retest in GRID:
            tr = trades_for(adx_th, sl_m, tp_m, retest, "2021-01-01", lo)
            if len(tr) < 15:
                continue
            exp = sum(tr) / len(tr)
            if exp > best_exp:
                best_exp, best = exp, (adx_th, sl_m, tp_m, retest)
        te = trades_for(*best, lo, hi) if best else []
        fold = {
            "train_window": name,
            "test_window": f"{lo[:7]}..{(hi or '2026-08')[:7]}",
            "selected": (f"ADX{best[0]} SL{best[1]} TP{best[2]} retest={best[3]}" if best else None),
            "train_trades": None,
            "test_trades": len(te),
            "test_net": round(sum(te), 1),
            "test_pf": round(pf(te), 3),
        }
        # also record the LIVE config's performance on this test window for comparison
        live_te = trades_for(20, 3.0, 3.0, True, lo, hi)
        fold["live_config_test_pf"] = round(pf(live_te), 3)
        fold["live_config_test_trades"] = len(live_te)
        report["folds"].append(fold)
        print(json.dumps(fold))

    selected = [f["selected"] for f in report["folds"]]
    consistent = len(set(selected)) == 1
    report["verdict"] = (
        "ROBUST: identical parameters selected in every anchored fold"
        if consistent else
        "FRAGILE: selected parameters drift across folds — treat live config with caution"
    )
    print("\nVERDICT:", report["verdict"])
    with open("research/upgrade_2026_08/walk_forward_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("saved walk_forward_report.json")


if __name__ == "__main__":
    main()
