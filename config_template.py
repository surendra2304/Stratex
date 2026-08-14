# ==============================================================================
# CONFIG_TEMPLATE.PY - Copy this file to config.py and fill in your own keys
# ==============================================================================

# --- Binance Testnet API Keys ---
# Get your free testnet keys at: https://testnet.binance.vision
API_KEY = "YOUR_BINANCE_TESTNET_API_KEY_HERE"
SECRET_KEY = "YOUR_BINANCE_TESTNET_SECRET_KEY_HERE"

# --- Testnet Base URLs ---
BASE_URL = "https://testnet.binance.vision"
WS_URL = "wss://ws-api.testnet.binance.vision/ws-api/v3"

# --- Trading Symbol ---
SYMBOL = "BTCUSDT"          # Trading pair
TIMEFRAME = "1m"            # Candle interval (1m, 5m, 15m, 1h)

# --- Risk Management ---
TRADE_QTY = 0.001           # BTC quantity per trade (small for safety)
MAX_OPEN_TRADES = 1         # Only 1 open trade at a time per strategy

# --- Strategy to Run ---
# Options: "scalper", "swing", "ml", "multi"
ACTIVE_STRATEGY = "multi"
