# ==============================================================================
# CONFIG.PY - Central Configuration for the Trading Bot Framework
# ==============================================================================
# SECURITY: No credentials are stored in this file.
# Credentials MUST be set via environment variables or a .env file.
# See .env.example for the required variables.
# ==============================================================================

import os
from dotenv import load_dotenv

# We no longer need utf-16le fallback since the file is now standard utf-8
load_dotenv()

# If a BOM was parsed as part of the first key, it will appear as '\ufeffAPI_KEY'
if '\ufeffAPI_KEY' in os.environ:
    os.environ['API_KEY'] = os.environ['\ufeffAPI_KEY']
    del os.environ['\ufeffAPI_KEY']

# --- Binance API Credentials (from environment / .env file only) ---
API_KEY = os.getenv("API_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")

# --- Testnet Base URLs ---
BASE_URL = "https://testnet.binance.vision"
WS_URL = "wss://ws-api.testnet.binance.vision/ws-api/v3"

# --- Dynamic Market Scanner ---
SYMBOL = "BTCUSDT"

# --- Risk Management ---
TRADE_QTY = 0.001           # BTC quantity per trade (small for safety)
MAX_OPEN_TRADES = 20        # Allow more concurrent trades during testing
TOP_COINS_LIMIT = 20  # Increase number of top trending coins to scan
TARGET_TRADE_COUNT = 100  # Minimum trades to generate within the window
TARGET_TRADE_WINDOW_HOURS = 3  # Hours within which to achieve target
LONG_ONLY = True            # Binance Spot default

# -------------------------------------------------------------------
# OPERATIONAL SAFETY GATES
# -------------------------------------------------------------------
TRADING_MODE = os.getenv("TRADING_MODE", "PAPER") # "PAPER", "TESTNET", or "LIVE"
PAPER_SAFE_MODE = os.getenv("PAPER_SAFE_MODE", "False" if TRADING_MODE in ["TESTNET", "LIVE"] else "True").lower() == "true"
TESTNET_ENABLED = os.getenv("TESTNET_ENABLED", "False").lower() == "true"
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "False").lower() == "true"

# --- Strategies to Run ---
# Multi-strategy configuration mapping validated strategy -> timeframe(s)
ACTIVE_STRATEGIES = {
    "aggressor": ["5m"],  # bumped from 1m, 3m
    "scalper": ["5m"],    # bumped from 1m, 3m
    "supertrend": ["15m", "30m", "1h"],
    "ml": ["15m", "30m"],
    "swing": ["2h", "4h"],
    "adx_ema": ["4h"],
    "fast1m": ["1m"],
    "fast2m": ["2m"],
    "fast5m": ["5m"]
}

# Backward-compatibility aliases for legacy tests/modules
ACTIVE_STRATEGY = list(ACTIVE_STRATEGIES.keys())[0]
_first_tf = list(ACTIVE_STRATEGIES.values())[0]
TIMEFRAME = _first_tf[0] if isinstance(_first_tf, list) else _first_tf

# Trading Config
MAX_POSITION_SIZE = 0.95
RISK_PER_TRADE = 0.02

# --- Testnet Risk Management (RELAXED for 100-trade target) ---
MAX_TESTNET_RISK_PER_TRADE = 0.005 # 0.5% risk
MAX_TESTNET_EXPOSURE = 0.95        # 95% max total exposure (relaxed for testing)
MAX_SINGLE_ASSET_EXPOSURE = 0.50   # 50% max per single asset (relaxed for testing)
MAX_NET_DIRECTIONAL_EXPOSURE = 0.95 # 95% max net directional exposure (relaxed)
MAX_OPEN_POSITIONS = 20            # Allow up to 20 concurrent positions
MAX_DAILY_LOSS_PCT = 0.50          # 50% daily loss limit (relaxed for testing)
MAX_TESTNET_DRAWDOWN_PCT = 0.50    # 50% drawdown tolerance (relaxed for testing)
RECONCILIATION_TOLERANCE = 5.0     # 5 USDT tolerance for slight fee estimation drift

# --- Strategy Quality Control (Phase 5) ---
MINIMUM_EXPECTED_EDGE = -0.001     # Very low threshold to accept all signals during testing
DEGRADATION_WINDOW = 20            # Evaluate last 20 trades for degradation
MIN_WIN_RATE_THRESHOLD = 0.35      # Automatically switch to OBSERVE-ONLY if < 35% win rate
MAX_PREDICTION_ERROR = 0.02        # Automatically switch to OBSERVE-ONLY if actual differs from expected by > 2%

# --- Backtesting Engine ---
BACKTEST_FEE_RATE = 0.001          # 0.1% fee per trade
BACKTEST_SLIPPAGE_RATE = 0.0005    # 0.05% slippage
RISK_PER_TRADE = 0.01              # 1% risk of equity per trade
STARTING_BALANCE = 10000.0         # Initial balance
OOS_TRAIN_PCT = 0.60               # Walk-forward train %
OOS_VAL_PCT = 0.20                 # Walk-forward validation %
INTRABAR_RESOLUTION = "conservative" # "conservative" or "optimistic"

SUPPORTED_STRATEGIES = ["scalper", "swing", "ml", "aggressor", "supertrend", "multi", "adx_ema", "fast1m", "fast2m", "fast5m"]
SUPPORTED_TIMEFRAMES = ["1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"]
VALID_MODES = ["PAPER", "TESTNET", "LIVE"]

def validate_config():
    # If legacy ACTIVE_STRATEGY or TIMEFRAME attributes were set dynamically (e.g. in tests)
    global ACTIVE_STRATEGY, TIMEFRAME
    if "ACTIVE_STRATEGY" in globals():
        strat = globals()["ACTIVE_STRATEGY"]
        if strat not in SUPPORTED_STRATEGIES:
            raise ValueError(f"Configuration Error: Invalid ACTIVE_STRATEGY '{strat}'. Supported: {SUPPORTED_STRATEGIES}")
    if "TIMEFRAME" in globals():
        tf = globals()["TIMEFRAME"]
        if tf not in SUPPORTED_TIMEFRAMES:
            raise ValueError(f"Configuration Error: Invalid TIMEFRAME '{tf}'. Supported: {SUPPORTED_TIMEFRAMES}")

    for strategy, timeframes in ACTIVE_STRATEGIES.items():
        if strategy not in SUPPORTED_STRATEGIES:
            raise ValueError(f"Configuration Error: Invalid ACTIVE_STRATEGY '{strategy}'. Supported: {SUPPORTED_STRATEGIES}")
        
        tfs = timeframes if isinstance(timeframes, list) else [timeframes]
        for tf in tfs:
            if tf not in SUPPORTED_TIMEFRAMES:
                raise ValueError(f"Configuration Error: Invalid TIMEFRAME '{tf}' for strategy '{strategy}'. Supported: {SUPPORTED_TIMEFRAMES}")

    if TRADING_MODE not in VALID_MODES:
        raise ValueError(f"Configuration Error: Invalid TRADING_MODE '{TRADING_MODE}'.")

    if TRADING_MODE == "LIVE":
        print("WARNING: TRADING_MODE IS SET TO LIVE. REAL MONEY AT RISK.")

    if not isinstance(TRADE_QTY, (int, float)) or TRADE_QTY <= 0:
        raise ValueError(f"Configuration Error: TRADE_QTY must be a positive number.")

    if not isinstance(TOP_COINS_LIMIT, int) or TOP_COINS_LIMIT <= 0:
        raise ValueError(f"Configuration Error: TOP_COINS_LIMIT must be a positive integer.")

    # For non-PAPER modes, credentials must be set (but not hardcoded here)
    if TRADING_MODE in ["TESTNET", "LIVE"]:
        if not API_KEY or not SECRET_KEY:
            raise ValueError(
                "Configuration Error: API_KEY and SECRET_KEY must be set via "
                "environment variables or .env file for TESTNET/LIVE mode."
            )

# Validate immediately upon import
validate_config()
