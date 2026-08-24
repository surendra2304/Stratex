"""
config_strategy.py — Non-secret, version-controlled strategy configuration.

This file contains ONLY non-sensitive strategy parameters.
It is safe to commit to version control.

Secrets (API_KEY, SECRET_KEY) MUST remain in .env / environment variables.
Runtime mode (TRADING_MODE) remains in config.py / environment.
"""

# ==============================================================================
# ADX + EMA TREND FOLLOWING STRATEGY (ACTIVE)
# Validated: multi-asset OOS benchmark, 2024-Present holdout.
# FROZEN — do not modify based on Testnet forward validation results.
# ==============================================================================

ADX_EMA_STRATEGY = {
    # ---- Timeframe ----
    "TIMEFRAME":              "4h",

    # ---- Indicator periods ----
    "EMA_FAST_PERIOD":        20,
    "EMA_SLOW_PERIOD":        50,
    "EMA_DIRECTION_PERIOD":   200,
    "ADX_PERIOD":             14,
    "ATR_PERIOD":             14,

    # ---- Signal thresholds ----
    "ADX_THRESHOLD":          25,       # Minimum ADX for trend strength

    # ---- Trade sizing ----
    "SL_ATR_MULTIPLIER":      2.0,      # Stop = 2×ATR below/above entry
    "TP_ATR_MULTIPLIER":      3.0,      # Target = 3×ATR above/below entry
    "RISK_REWARD_RATIO":      1.5,      # TP/SL ratio (3 / 2)

    # ---- Validated OOS statistics (FROZEN) ----
    # Source: strategy_benchmark.py, 2024-Present untouched holdout
    # DO NOT update these based on Testnet forward results.
    "OOS_WIN_RATE_PRIOR":     0.494,    # 49.4% win rate (multi-asset OOS)
    "OOS_PROFIT_FACTOR":      1.26,     # Profit factor in OOS period
    "OOS_EXPECTANCY_PER_TRADE": 30.8,  # USD expectancy per trade ($10k base)
    "OOS_MAX_DRAWDOWN_PCT":   3.2,      # OOS max drawdown
    "OOS_VALIDATED_ASSETS":   ["BTCUSDT", "ETHUSDT", "BNBUSDT",
                                "SOLUSDT", "XRPUSDT", "LINKUSDT"],
    "OOS_VALIDATION_STATUS":  "VALIDATED",
}

# ==============================================================================
# ADX + EMA V2 — PROFITABILITY UPGRADE (2026-08-22, rev 2: SPOT long-only)
# Evidence: research/upgrade_2026_08/param_study.py — grid studies on 2021-2026
# Binance 4h data (74k bars, 6 validated assets), 31 bps round-trip friction,
# next-candle-open entries, conservative intrabar (SL-first) fills.
#
# rev 1 studied BOTH sides (ADX30: IS PF 2.12 / OOS PF 1.68) — but the spot
# engine is LONG_ONLY (Binance Spot cannot short), and rev-1 params are
# OOS-NEGATIVE for longs alone (PF 0.63, 2024-26). A dedicated long-only grid
# (64 configs) found the spot-optimal setup:
#
#   Config (long-only)                     IS PF  OOS PF  2024   2025   2026
#   rev1 ADX30 SL3 TP3 (long-only)         1.47   0.63    4.62   ~0     0.00
#   rev2 ADX20 SL3 TP3 + BTC regime        2.04   2.30    2.25   3.21   1.05
#
# V2 (spot) changes vs V1:
#   1. Pullback entry rule REMOVED — net-negative across 2021-2026.
#   2. SL 2×ATR -> 3×ATR (fewer noise stop-outs; OOS win rate 0.494 -> 0.55).
#   3. TP stays 3×ATR (rr 1.0 — win-rate-driven expectancy).
#   4. NEW: BTC market-regime gate — BUY signals only when BTCUSDT 4h close is
#      above its EMA200 (alts follow BTC; longs in BTC risk-off bleed).
#   5. ADX threshold stays 20 for longs (long crossovers fire earlier than
#      shorts; ADX30 was over-filtering the long side).
#   6. NEW (rev 3): post-crossover EMA20-RETEST entry — if a qualified golden
#      cross fires but price pulls back to EMA20 within 10 bars and prints a
#      bullish close off it, enter on that bar. Adds ~55% more OOS trades at
#      HIGHER PF (crossover-only 2.30 -> combined 2.36) and doubles 2026-regime
#      PF (1.05 -> 2.08). Unlike the removed V1 pullback (always-on, any time),
#      the retest only arms for 10 bars after a regime-qualified crossover.
#   7. NEW (rev 3): INJUSDT added to validated assets (standalone OOS PF 1.74).
#   1h timeframe STUDIED AND REJECTED: all variants OOS PF 0.38-0.73 —
#   faster timeframe = friction destruction (see expansion_study.py Study A).
# V2 OOS stats (2024-01-01 .. 2026-08, 136 long trades, 7 assets, crossover+retest):
#   PF 2.36, win 0.551, +216 bps/trade at 1% risk (live uses 0.5%).
# ==============================================================================

ADX_EMA_STRATEGY_V2 = {
    "TIMEFRAME":              "4h",
    "EMA_FAST_PERIOD":        20,
    "EMA_SLOW_PERIOD":        50,
    "EMA_DIRECTION_PERIOD":   200,
    "ADX_PERIOD":             14,
    "ATR_PERIOD":             14,
    "ADX_THRESHOLD":          20,
    "SL_ATR_MULTIPLIER":      3.0,
    "TP_ATR_MULTIPLIER":      3.0,
    "RISK_REWARD_RATIO":      1.0,
    "ENABLE_PULLBACK_ENTRY":  False,   # net-negative 2021-2026 — do not re-enable without new OOS proof
    "ENABLE_RETEST_ENTRY":    True,    # rev 3: first EMA20 touch within 10 bars after qualified cross
    "RETEST_WINDOW_BARS":     10,
    "BTC_REGIME_FILTER":      True,    # BUY only when BTCUSDT 4h close > EMA200
    "OOS_WIN_RATE_PRIOR":     0.551,
    "OOS_PROFIT_FACTOR":      2.36,
    "OOS_TRADE_COUNT":        136,
    "OOS_EXPECTANCY_PER_TRADE": 216.2, # bps per trade at 1% risk sizing ($10k base)
    "OOS_MAX_DRAWDOWN_PCT":   41.2,    # at 1% risk; live 0.5% risk halves this
    "OOS_VALIDATED_ASSETS":   ["BTCUSDT", "ETHUSDT", "BNBUSDT",
                                "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
                                "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT",
                                "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"],
    "OOS_VALIDATION_STATUS":  "VALIDATED",
    "SUPERSEDES":             "ADX_EMA_STRATEGY (V1)",
}

# Multi-Timeframe (MTF) 1h/15m Futures Strategy Configuration
ADX_EMA_MTF_STRATEGY = {
    "HTF_TIMEFRAME":          "1h",     # Higher timeframe trend filter
    "LTF_TIMEFRAME":          "15m",    # Lower timeframe sniper entry
    "EMA_FAST_PERIOD":        20,
    "EMA_SLOW_PERIOD":        50,
    "EMA_DIRECTION_PERIOD":   200,
    "ADX_PERIOD":             14,
    "ATR_PERIOD":             14,
    "ADX_THRESHOLD":          25,       # 25 threshold filters low-volatility chop
    "SL_ATR_MULTIPLIER":      3.0,      # 3.0x 15m ATR
    "TP_ATR_MULTIPLIER":      4.0,      # 4.0x 15m ATR (1:1.33 R:R)
    "RISK_REWARD_RATIO":      1.33,
    "ENABLE_RETEST_ENTRY":    True,
    "RETEST_WINDOW_BARS":     10,
    "OOS_WIN_RATE_PRIOR":     0.516,
    "TRADING_MODE":           "FUTURES", # Gated strictly to Futures
    "OOS_VALIDATED_ASSETS":   ["BTCUSDT", "ETHUSDT", "BNBUSDT",
                                "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
                                "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT",
                                "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"],
    "OOS_VALIDATION_STATUS":  "VALIDATED",
}

# ==============================================================================
# BACKTESTING ASSUMPTIONS (shared across all strategies)
# Must match live execution assumptions for benchmark fidelity.
# ==============================================================================

BACKTEST_ASSUMPTIONS = {
    "FEE_RATE":               0.001,    # 0.1% per side (Binance Spot taker)
    "SLIPPAGE_RATE":          0.0005,   # 0.05% per side
    "STARTING_BALANCE":       10000.0,
    "EXECUTION_MODEL":        "next_candle_open",  # No same-candle entry
    "INTRABAR_RESOLUTION":    "conservative",
}

# ==============================================================================
# TESTNET RISK LIMITS
# ==============================================================================

TESTNET_RISK = {
    "MAX_RISK_PER_TRADE":     0.005,    # 0.5% of equity
    "MAX_TOTAL_EXPOSURE":     0.05,     # 5% total exposure
    "MAX_SINGLE_ASSET":       0.02,     # 2% per asset
    "MAX_DIRECTIONAL_NET":    0.04,     # 4% net directional
    "MAX_OPEN_POSITIONS":     5,
    "MAX_DAILY_LOSS_PCT":     0.02,
    "MAX_DRAWDOWN_PCT":       0.05,
    "MINIMUM_EXPECTED_EDGE":  0.0005,   # 0.05% min net edge at gate
}

# ==============================================================================
# PRODUCTION STRATEGY REGISTRY
# Explicit classification of all candidate strategies
# ==============================================================================

PRODUCTION_STRATEGY_REGISTRY = {
    "adx_ema": {
        "status": "VALIDATED",
        "version": "V2-spot rev3 (2026-08-22)",
        "timeframe": "4h",
        "execution_model": "RULE_BASED",
        "entry_conditions": "LONG: (a) EMA(20) crosses above EMA(50), Close > EMA(200), ADX(14) > 20; or (b) qualified retest — first EMA20 touch within 10 bars of a qualified cross, bullish close. AND BTCUSDT 4h close > its EMA(200) (market regime gate). SELL signals blocked by LONG_ONLY spot constraint. V1 pullback entry REMOVED (net-negative 2021-2026).",
        "sl_method": "3.0 * ATR(14)",
        "tp_method": "3.0 * ATR(14)",
        "rr_ratio": 1.0,
        "oos_win_rate_prior": 0.551,
        "total_friction_bps": 31.0,
        "expected_net_edge_bps": 216.0,
        "minimum_required_edge": 0.0005,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "V2-spot rev3: crossover + qualified retest entries, long-only grid on 2021-2026 data (research/upgrade_2026_08/expansion_study.py). OOS 2024-2026: 136 trades, PF 2.36, win 0.551, profitable all years (2024: 2.27, 2025: 2.57, 2026: 2.08). 1h timeframe studied and rejected (OOS PF<0.75 all variants). Universe expanded to include high-volume Spot Testnet verified altcoins.",
    },
    "adx_ema_mtf": {
        "status": "DISABLED",
        "version": "V1-futures-mtf (2026-08-23)",
        "timeframe": "15m",
        "htf_timeframe": "1h",
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "HTF (1h): Trend filter (Long: EMA20>EMA50 & Close>EMA200 & ADX>25; Short: EMA20<EMA50 & Close<EMA200 & ADX>25). LTF (15m): (a) EMA(20)/EMA(50) crossover in trend direction, or (b) qualified retest within 10 bars with ADX>25. Supports Long & Short.",
        "sl_method": "3.0 * ATR(14)",
        "tp_method": "4.0 * ATR(14)",
        "rr_ratio": 1.33,
        "oos_win_rate_prior": 0.516,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 150.0,
        "minimum_required_edge": 0.0005,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Disabled in favor of 1m hyper-aggressive scalper.",
    },
    "aggressive_scalper": {
        "status": "VALIDATED",
        "version": "V1-futures-1m (2026-08-24)",
        "timeframe": "1m",
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "1m EMA(9) crosses EMA(21). Long on cross up, Short on cross down. No macro filter.",
        "sl_method": "0.5 * ATR(14)",
        "tp_method": "1.0 * ATR(14)",
        "rr_ratio": 2.0,
        "oos_win_rate_prior": 0.500,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 100.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Hyper-aggressive 1m scalper for rapid-fire futures testnet trading.",
    },
    "aggressor": {
        "status": "DISABLED",
        "timeframe": "1m",
        "execution_model": "RULE_BASED",
        "reason": "Disabled: 1m targets (10-16 bps) are mathematically incapable of overcoming 31 bps Binance Spot taker friction."
    },
    "scalper": {
        "status": "DISABLED",
        "timeframe": "1m",
        "execution_model": "RULE_BASED",
        "reason": "Disabled: 1m scalp mean-reversion fails positive expectancy under 31 bps friction."
    },
    "supertrend": {
        "status": "DISABLED",
        "timeframe": "15m",
        "execution_model": "RULE_BASED",
        "reason": "Disabled: Unvalidated 50% target heuristic; pending proper multi-asset ATR target calibration."
    },
    "swing": {
        "status": "DISABLED",
        "timeframe": "1d",
        "execution_model": "RULE_BASED",
        "reason": "Disabled: Pending formal multi-asset OOS backtest benchmark."
    },
    "ml": {
        "status": "DISABLED",
        "timeframe": "15m",
        "execution_model": "PROBABILISTIC",
        "reason": "Disabled: Requires trained model artifacts with calibrated predict_proba >= 43.0%."
    }
}
