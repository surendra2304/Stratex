"""
config_strategy.py — Non-secret, version-controlled strategy configuration.

This file contains ONLY non-sensitive strategy parameters.
It is safe to commit to version control.

Secrets (API_KEY, SECRET_KEY) MUST remain in .env / environment variables.
Runtime mode (TRADING_MODE) remains in config.py / environment.
"""

# ==============================================================================
# ADX + EMA TREND FOLLOWING STRATEGY (OBSERVE ONLY)
# Historical result files are present, but the source candle dataset is absent
# from this checkout. Treat the recorded metrics as unverified until the exact
# inputs and provenance are restored and the study is reproduced.
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

    # Unverified historical values are deliberately unavailable to runtime gates.
    "OOS_WIN_RATE_PRIOR":     None,
    "OOS_PROFIT_FACTOR":      None,
    "OOS_EXPECTANCY_PER_TRADE": None,
    "OOS_MAX_DRAWDOWN_PCT":   None,
    "OOS_VALIDATED_ASSETS":   [],
    "OOS_VALIDATION_STATUS":  "UNVERIFIED",
}

# ==============================================================================
# ADX + EMA V2 — OBSERVE-ONLY CANDIDATE (2026-08-22, rev 3)
# The strategy parameters below are retained so the candidate can be inspected.
# Historical comments and reports made OOS performance claims, but the source
# OHLCV inputs are absent. The checked-in walk-forward report has only 34
# frozen-config trades in its 2024–2026 fold and labels selection FRAGILE.
# Treat all old OOS statistics as unverified; they are not runtime priors.
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
    "OOS_WIN_RATE_PRIOR":     None,
    "OOS_PROFIT_FACTOR":      None,
    "OOS_TRADE_COUNT":        None,
    "OOS_EXPECTANCY_PER_TRADE": None,
    "OOS_MAX_DRAWDOWN_PCT":   None,
    "OOS_VALIDATED_ASSETS":   [],
    "OOS_VALIDATION_STATUS":  "UNVERIFIED",
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
    # OOS fields are NOT runtime priors for this strategy.
    # PRODUCTION_STRATEGY_REGISTRY marks adx_ema_mtf DISABLED and the null-out
    # block at the end of this file clears the registry copy, but this raw dict is
    # what strategy_adx_ema_mtf reads directly — so leaving a figure here would
    # smuggle an unverified win rate into every live signal it emits, and would
    # report 16 "validated" assets that no reproducible holdout has cleared.
    # The same reasoning and comments already applied to the V1/V2 dicts above.
    "OOS_WIN_RATE_PRIOR":     None,
    "TRADING_MODE":           "FUTURES", # Gated strictly to Futures
    "OOS_VALIDATED_ASSETS":   [],
    "OOS_VALIDATION_STATUS":  "UNVERIFIED",
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
        "status": "OBSERVE_ONLY",
        "version": "V2-spot rev3 (2026-08-22)",
        "timeframe": "4h",
        "execution_model": "RULE_BASED",
        "entry_conditions": "LONG: (a) EMA(20) crosses above EMA(50), Close > EMA(200), ADX(14) > 20; or (b) qualified retest — first EMA20 touch within 10 bars of a qualified cross, bullish close. AND BTCUSDT 4h close > its EMA(200) (market regime gate). SELL signals blocked by LONG_ONLY spot constraint. V1 pullback entry REMOVED (net-negative 2021-2026).",
        "sl_method": "3.0 * ATR(14)",
        "tp_method": "3.0 * ATR(14)",
        "rr_ratio": 1.0,
        "oos_win_rate_prior": None,
        "total_friction_bps": 31.0,
        "expected_net_edge_bps": None,
        "minimum_required_edge": 0.0005,
        "validated_assets": [],
        "reason": "Observe only: historical OOS claims are not reproducible because the referenced OHLCV inputs are absent; the checked-in walk-forward report records 34 frozen-config holdout trades and a FRAGILE verdict.",
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
        "reason": "Disabled in favor of multi-timeframe hyper-aggressive scalper.",
    },
    "aggressive_scalper": {
        "status": "OBSERVE_ONLY",
        "version": "V1-futures-all-tf (2026-08-24)",
        "timeframe": "1m",
        "timeframes": ["1m", "5m", "15m", "30m", "1h", "4h"],
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "Fast EMA(9) cross Slow EMA(21) on any active timeframe. SL: 0.5x ATR, TP: 1.0x ATR.",
        "sl_method": "0.5 * ATR(14)",
        "tp_method": "1.0 * ATR(14)",
        "rr_ratio": 2.0,
        "oos_win_rate_prior": 0.50,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 120.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Observe only: no reproducible out-of-sample evidence is available in this checkout.",
    },
    "bb_reversion": {
        "status": "OBSERVE_ONLY",
        "version": "V1-bb-reversion (2026-09-09)",
        "timeframe": "5m",
        "timeframes": ["5m", "15m"],
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "Bollinger Band 2.0 StdDev pierce & re-entry with SL 0.5x ATR, TP 1.5x ATR.",
        "sl_method": "0.5 * ATR(14)",
        "tp_method": "1.5 * ATR(14)",
        "rr_ratio": 3.0,
        "oos_win_rate_prior": 0.50,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 120.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Research only: no verified out-of-sample evidence supports the stored win-rate or edge prior.",
    },
    "rsi_burst": {
        "status": "DISABLED",
        "version": "V1-futures-multi-tf (2026-08-24)",
        "timeframe": "1m",
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "reason": "Secondary strategy available.",
    },
    "vwap_trend": {
        "status": "DISABLED",
        "version": "V1-futures-multi-tf (2026-08-24)",
        "timeframe": "1m",
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "reason": "Secondary strategy available.",
    },
    "factory_winner_1": {
        "status": "OBSERVE_ONLY",
        "version": "V1-factory (2026-08-24)",
        "timeframe": "5m",
        "timeframes": ["1m", "5m", "15m", "30m", "1h", "4h"],
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "MACD(12,26,9) crossover with Bollinger Bands(20,2) mean-dip confluence. SL: 1.5x ATR, TP: 3.0x ATR.",
        "sl_method": "1.5 * ATR(14)",
        "tp_method": "3.0 * ATR(14)",
        "rr_ratio": 2.0,
        "oos_win_rate_prior": 0.431,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 120.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Research only: historical report is unverified; source data and provenance are absent.",
    },
    "factory_winner_2": {
        "status": "OBSERVE_ONLY",
        "version": "V1-factory (2026-08-24)",
        "timeframe": "5m",
        "timeframes": ["5m", "15m", "30m", "1h", "4h"],
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "5m MACD(12,26,9) crossover with Bollinger Bands(20,2) mean-dip confluence. SL: 1.5x ATR, TP: 3.0x ATR.",
        "sl_method": "1.5 * ATR(14)",
        "tp_method": "3.0 * ATR(14)",
        "rr_ratio": 2.0,
        "oos_win_rate_prior": 0.413,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 110.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Research only: historical report is unverified; source data and provenance are absent.",
    },
    "factory_winner_3": {
        "status": "OBSERVE_ONLY",
        "version": "V1-factory (2026-08-24)",
        "timeframe": "5m",
        "timeframes": ["5m", "15m", "30m", "1h", "4h"],
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "5m MACD(12,26,9) crossover with Bollinger Bands(20,2) mean-dip confluence. SL: 1.5x ATR, TP: 4.5x ATR.",
        "sl_method": "1.5 * ATR(14)",
        "tp_method": "4.5 * ATR(14)",
        "rr_ratio": 3.0,
        "oos_win_rate_prior": 0.334,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 100.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Research only: historical report is unverified; source data and provenance are absent.",
    },
    "factory_winner_4": {
        "status": "OBSERVE_ONLY",
        "version": "V1-factory (2026-08-24)",
        "timeframe": "5m",
        "timeframes": ["5m", "15m", "30m", "1h", "4h"],
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "5m MACD(12,26,9) crossover with Bollinger Bands(20,2) mean-dip confluence. SL: 2.0x ATR, TP: 4.0x ATR.",
        "sl_method": "2.0 * ATR(14)",
        "tp_method": "4.0 * ATR(14)",
        "rr_ratio": 2.0,
        "oos_win_rate_prior": 0.410,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 95.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Research only: historical report is unverified; source data and provenance are absent.",
    },
    "factory_winner_5": {
        "status": "OBSERVE_ONLY",
        "version": "V1-factory (2026-08-24)",
        "timeframe": "15m",
        "timeframes": ["5m", "15m", "30m", "1h", "4h"],
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "15m MACD(12,26,9) crossover with Bollinger Bands(20,2) mean-dip confluence. SL: 1.5x ATR, TP: 4.5x ATR.",
        "sl_method": "1.5 * ATR(14)",
        "tp_method": "4.5 * ATR(14)",
        "rr_ratio": 3.0,
        "oos_win_rate_prior": 0.305,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 90.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Research only: historical report is unverified; source data and provenance are absent.",
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
        "status": "OBSERVE_ONLY",
        "version": "V2-supertrend-high-winrate (2026-09-12)",
        "timeframe": "15m",
        "timeframes": ["15m", "1h"],
        "trading_mode": "FUTURES",
        "execution_model": "RULE_BASED",
        "entry_conditions": "Supertrend breakout flip above EMA200, or EMA21 pullback bounce continuation with full EMA trend alignment & ADX >= 25. SL: 1.5x ATR, TP: 1.5x ATR.",
        "sl_method": "1.5 * ATR(14)",
        "tp_method": "1.5 * ATR(14)",
        "rr_ratio": 1.0,
        "oos_win_rate_prior": 0.80,
        "total_friction_bps": 8.0,
        "expected_net_edge_bps": 120.0,
        "minimum_required_edge": 0.0001,
        "validated_assets": [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "INJUSDT",
            "AVAXUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT"
        ],
        "reason": "Research only: no verified out-of-sample evidence supports the stored 80% win-rate prior.",
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

# Stored research estimates are not runtime priors unless a strategy has been
# explicitly promoted after a reproducible validation review. This also keeps
# stale metrics from disabled or observe-only candidates out of accidental
# direct callers that inspect the registry without applying governance.
for _strategy_entry in PRODUCTION_STRATEGY_REGISTRY.values():
    if _strategy_entry.get("status") != "VALIDATED":
        _strategy_entry["oos_win_rate_prior"] = None
        _strategy_entry["expected_net_edge_bps"] = None
        _strategy_entry["validated_assets"] = []
