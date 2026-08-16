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
        "timeframe": "4h",
        "execution_model": "RULE_BASED",
        "entry_conditions": "EMA(20) crosses EMA(50), Close > EMA(200) (BUY) / Close < EMA(200) (SELL), ADX(14) > 25",
        "sl_method": "2.0 * ATR(14)",
        "tp_method": "3.0 * ATR(14)",
        "rr_ratio": 1.5,
        "oos_win_rate_prior": 0.494,
        "total_friction_bps": 31.0,
        "expected_net_edge_bps": 39.5,
        "minimum_required_edge": 0.0005,
        "validated_assets": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"],
        "reason": "Empirically validated multi-asset OOS trend strategy with robust positive net edge after 31 bps taker friction."
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
