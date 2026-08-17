"""
scratch/audit_adx_ema_math.py
Strict mathematical audit of the ADX+EMA strategy, ProfitabilityGate, and CostEngine.
"""

import sys
import os
import pandas as pd
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_adx_ema import (
    get_signal,
    add_features,
    compute_atr,
    compute_adx,
    SignalResult,
    _STRATEGY_TYPE,
    _OOS_WIN_RATE_PRIOR,
    _RR_RATIO,
)
from testnet_engine.profitability_gate import ProfitabilityGate
from research_phase9.cost_engine import CostEngine

def run_math_audit():
    print("==================================================================")
    print("MATHEMATICAL AUDIT: ADX + EMA TREND STRATEGY & PROFITABILITY GATE")
    print("==================================================================\n")
    
    # 1. Cost Engine Verification
    ce = CostEngine.get_binance_taker_config()
    print("1. EXACT FRICTION COMPONENTS:")
    print(f"   - Entry Fee      : {ce.entry_fee:.4f} ({ce.entry_fee*10000:.1f} bps / {ce.entry_fee*100:.2f}%)")
    print(f"   - Exit Fee       : {ce.exit_fee:.4f} ({ce.exit_fee*10000:.1f} bps / {ce.exit_fee*100:.2f}%)")
    print(f"   - Entry Slippage : {ce.entry_slip:.4f} ({ce.entry_slip*10000:.1f} bps / {ce.entry_slip*100:.2f}%)")
    print(f"   - Exit Slippage  : {ce.exit_slip:.4f} ({ce.exit_slip*10000:.1f} bps / {ce.exit_slip*100:.2f}%)")
    print(f"   - Half-Spread    : {ce.spread:.4f} ({ce.spread*10000:.1f} bps / {ce.spread*100:.2f}%)")
    total_friction = ce.get_total_friction()
    print(f"   -> TOTAL ROUND-TRIP FRICTION: {total_friction:.6f} ({total_friction*10000:.1f} bps / {total_friction*100:.2f}%)\n")
    
    # 2. Strategy Structural Parameters
    p_win = _OOS_WIN_RATE_PRIOR # 0.494
    p_loss = 1.0 - p_win       # 0.506
    sl_mult = 2.0
    tp_mult = 3.0
    rr_ratio = tp_mult / sl_mult # 1.5
    
    print("2. STRUCTURAL EXPECTANCY FORMULA:")
    print(f"   - Win Probability Prior (P_win)  : {p_win:.4f} (49.4%)")
    print(f"   - Loss Probability (P_loss)      : {p_loss:.4f} (50.6%)")
    print(f"   - Stop-Loss Distance Multiplier  : {sl_mult:.1f} × ATR")
    print(f"   - Take-Profit Distance Multiplier: {tp_mult:.1f} × ATR")
    print(f"   - Reward / Risk Ratio (R:R)      : {rr_ratio:.2f} (3.0 / 2.0)")
    print(f"   - Let normalized ATR ratio a = ATR / Entry_Price")
    print(f"   - Reward % = {tp_mult:.1f} × a")
    print(f"   - Risk %   = {sl_mult:.1f} × a")
    print(f"   - Expected Gross Return = (P_win × Reward %) - (P_loss × Risk %)")
    print(f"                           = ({p_win:.3f} × {tp_mult:.1f} × a) - ({p_loss:.3f} × {sl_mult:.1f} × a)")
    gross_coef = (p_win * tp_mult) - (p_loss * sl_mult)
    print(f"                           = ({p_win * tp_mult:.3f} × a) - ({p_loss * sl_mult:.3f} × a)")
    print(f"                           = +{gross_coef:.3f} × a")
    print(f"   - Expected Net Return   = (+{gross_coef:.3f} × a) - {total_friction:.4f}\n")
    
    # 3. Worked Example Table Across ATR Levels
    print("3. WORKED EXAMPLES AT DIFFERENT 4H ATR LEVELS (Entry = $60,000):")
    print(f"{'ATR %':<8} | {'ATR ($)':<8} | {'SL ($)':<10} | {'TP ($)':<10} | {'Risk %':<8} | {'Reward %':<8} | {'Gross Exp':<10} | {'Friction':<10} | {'Net Exp':<10} | {'Gate Decision'}")
    print("-" * 110)
    
    entry = 60000.0
    atr_levels = [0.0050, 0.00766, 0.0100, 0.0125, 0.0150, 0.0175, 0.0200, 0.0250, 0.0300]
    gate = ProfitabilityGate(ce)
    
    for a in atr_levels:
        atr_val = entry * a
        sl = entry - (sl_mult * atr_val)
        tp = entry + (tp_mult * atr_val)
        risk_pct = (entry - sl) / entry
        reward_pct = (tp - entry) / entry
        
        gross_exp = (p_win * reward_pct) - (p_loss * risk_pct)
        net_exp = gross_exp - total_friction
        
        sr = SignalResult("BUY", sl, tp, _STRATEGY_TYPE, _OOS_WIN_RATE_PRIOR, _RR_RATIO)
        accepted, metrics = gate.evaluate_signal("BTCUSDT", "BUY", entry, sl, tp, sr)
        
        dec = "ACCEPTED" if accepted else "REJECTED"
        print(f"{a*100:>6.3f}% | ${atr_val:>6.1f} | ${sl:>8.1f} | ${tp:>8.1f} | {risk_pct*100:>6.2f}% | {reward_pct*100:>6.2f}% | {gross_exp*10000:>+8.1f} bps | {total_friction*10000:>8.1f} bps | {net_exp*10000:>+8.1f} bps | {dec}")
        
    print("-" * 110)
    
    # 4. Break-even derivation
    min_edge = 0.0005
    break_even_a = (min_edge + total_friction) / gross_coef
    print(f"\n4. BREAK-EVEN ATR THRESHOLD DERIVATION:")
    print(f"   - Minimum Required Edge (Gate Threshold) : {min_edge:.4f} ({min_edge*10000:.1f} bps)")
    print(f"   - Required Gross Expectancy              : {min_edge + total_friction:.4f} ({(min_edge + total_friction)*10000:.1f} bps)")
    print(f"   - Minimum ATR / Price Ratio (a_min)      : ({min_edge:.4f} + {total_friction:.4f}) / {gross_coef:.3f} = {break_even_a:.5f} ({break_even_a*100:.3f}% = {break_even_a*10000:.1f} bps)")
    print(f"   -> ANY signal where 4h ATR >= {break_even_a*100:.3f}% ({break_even_a*10000:.1f} bps) produces a positive net edge >= 5 bps and is ACCEPTED.")
    print(f"   -> Any signal where 4h ATR < {break_even_a*100:.3f}% ({break_even_a*10000:.1f} bps) is correctly REJECTED.\n")

if __name__ == "__main__":
    run_math_audit()
