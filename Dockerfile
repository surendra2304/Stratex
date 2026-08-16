# ==============================================================================
# Dockerfile - Algorithmic Trading Bot (Binance Spot Testnet & Dashboard)
# ==============================================================================

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TRADING_MODE=TESTNET \
    TESTNET_ENABLED=True \
    LIVE_TRADING_ENABLED=False \
    TESTNET_ONLY=TRUE

WORKDIR /app

# Install system dependencies (build tools if needed for xgboost/numpy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose Dashboard Web UI port
EXPOSE 5000

# Supervised production entrypoint monitoring bot.py and dashboard.py
CMD ["python", "scripts/supervise_services.py"]
