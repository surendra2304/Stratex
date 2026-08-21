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

# --- Gemini AI Configuration (from environment / .env file only) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", "True").lower() == "true"




# --- Testnet Base URLs ---
BASE_URL = "https://testnet.binance.vision"
WS_URL = "wss://ws-api.testnet.binance.vision/ws-api/v3"

# --- Dynamic Market Scanner ---
SYMBOL = "BTCUSDT"

# --- Risk Management ---
TRADE_QTY = 0.001           # BTC quantity per trade (small for safety)
MAX_OPEN_TRADES = 30        # Increased concurrent trades for profitability
TOP_COINS_LIMIT = 20  # Increase number of top trending coins to scan
TARGET_TRADE_COUNT = 100  # Minimum trades to generate within the window
TARGET_TRADE_WINDOW_HOURS = 3  # Hours within which to achieve target
LONG_ONLY = True            # Binance Spot default

# -------------------------------------------------------------------
# OPERATIONAL SAFETY GATES (TESTNET ONLY)
# -------------------------------------------------------------------
TRADING_MODE = os.getenv("TRADING_MODE", "PAPER").upper()
PAPER_SAFE_MODE = os.getenv("PAPER_SAFE_MODE", "False" if TRADING_MODE == "TESTNET" else "True").lower() == "true"
TESTNET_ENABLED = os.getenv("TESTNET_ENABLED", "False").lower() == "true"
LIVE_TRADING_ENABLED = False  # PERMANENT SECURITY INVARIANT: Live trading is impossible by design

# --- Strategies to Run ---
# Multi-strategy configuration mapping validated strategy -> timeframe(s)
ACTIVE_STRATEGIES = {
    "aggressor": ["1m", "3m", "5m"],
    "scalper": ["1m", "3m", "5m"],
    "supertrend": ["5m", "15m", "30m", "1h"],
    "ml": ["5m", "15m", "30m"],
    "swing": ["15m", "30m", "1h", "2h", "4h"],
    "adx_ema": ["15m", "30m", "1h", "4h"],
    "hybrid": ["5m", "15m", "30m", "1h"],
    "bollinger": ["15m", "30m", "1h"],
    "breakout_vol": ["1m", "5m", "15m"]
}

ACTIVE_STRATEGY = next(iter(ACTIVE_STRATEGIES.keys()))
_first_tf = next(iter(ACTIVE_STRATEGIES.values()))
TIMEFRAME = _first_tf[0] if isinstance(_first_tf, list) else _first_tf

# Trading Config
MAX_POSITION_SIZE = 0.95

# --- Testnet Risk Management ---
MAX_TESTNET_RISK_PER_TRADE = float(os.getenv("MAX_TESTNET_RISK_PER_TRADE", "0.005")) # 0.5% risk
MAX_TESTNET_EXPOSURE = float(os.getenv("MAX_TESTNET_EXPOSURE", "0.05"))        # 5% max total exposure
MAX_SINGLE_ASSET_EXPOSURE = float(os.getenv("MAX_SINGLE_ASSET_EXPOSURE", "0.02"))   # 2% max per single asset
MAX_NET_DIRECTIONAL_EXPOSURE = float(os.getenv("MAX_NET_DIRECTIONAL_EXPOSURE", "0.04")) # 4% max net directional exposure
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))            # Allow up to 5 concurrent positions
VOLATILITY_BUFFER = 0.2  # Scale down positions during high volatility
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))          # 2% daily loss limit
MAX_TESTNET_DRAWDOWN_PCT = float(os.getenv("MAX_TESTNET_DRAWDOWN_PCT", "0.05"))    # 5% drawdown tolerance
RECONCILIATION_TOLERANCE = float(os.getenv("RECONCILIATION_TOLERANCE", "5.0"))     # 5 USDT tolerance

# --- Strategy Quality Control (Phase 5) ---
MINIMUM_EXPECTED_EDGE = float(os.getenv("MINIMUM_EXPECTED_EDGE", "0.0001"))     # Standard positive expected edge threshold
DEGRADATION_WINDOW = 20            # Evaluate last 20 trades for degradation
MIN_WIN_RATE_THRESHOLD = 0.35      # Automatically switch to OBSERVE-ONLY if < 35% win rate
MAX_PREDICTION_ERROR = 0.02        # Automatically switch to OBSERVE-ONLY if actual differs from expected by > 2%

# --- Backtesting Engine ---
BACKTEST_FEE_RATE = 0.001          # 0.1% fee per trade
BACKTEST_SLIPPAGE_RATE = 0.0005    # 0.05% slippage
BACKTEST_RISK_PER_TRADE = 0.01     # 1% risk of equity per trade in backtests
RISK_PER_TRADE = BACKTEST_RISK_PER_TRADE  # Canonical alias for backtest runners
STARTING_BALANCE = 10000.0         # Initial balance
OOS_TRAIN_PCT = 0.60               # Walk-forward train %
OOS_VAL_PCT = 0.20                 # Walk-forward validation %
INTRABAR_RESOLUTION = "conservative" # "conservative" or "optimistic"

SUPPORTED_STRATEGIES = ["scalper", "swing", "ml", "aggressor", "supertrend", "multi", "adx_ema", "fast1m", "fast5m", "hybrid", "bollinger", "breakout_vol"]
SUPPORTED_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"]
VALID_MODES = ["PAPER", "TESTNET"]

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
        raise ValueError(f"Configuration Error: Invalid TRADING_MODE '{TRADING_MODE}'. Only {VALID_MODES} are supported.")

    if not isinstance(TRADE_QTY, (int, float)) or TRADE_QTY <= 0:
        raise ValueError("Configuration Error: TRADE_QTY must be a positive number.")

    if not isinstance(TOP_COINS_LIMIT, int) or TOP_COINS_LIMIT <= 0:
        raise ValueError("Configuration Error: TOP_COINS_LIMIT must be a positive integer.")

    # For non-PAPER modes, credentials must be set (but not hardcoded here)
    if TRADING_MODE in ["TESTNET", "LIVE"] and (not API_KEY or not SECRET_KEY):
        raise ValueError(
            "Configuration Error: API_KEY and SECRET_KEY must be set via "
            "environment variables or .env file for TESTNET/LIVE mode."
        )

# Validate immediately upon import
validate_config()
