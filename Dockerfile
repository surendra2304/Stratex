# ==============================================================================
# Dockerfile - STRATEX (Binance Spot Testnet & Dashboard)
# ==============================================================================

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    TRADING_MODE=TESTNET \
    TESTNET_ENABLED=True \
    LIVE_TRADING_ENABLED=False \
    TESTNET_ONLY=TRUE

WORKDIR /app

# Install system dependencies (build tools if needed for native packages & curl for healthcheck)
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
EXPOSE 8000

# Docker Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -f http://localhost:8000/health || exit 1

# Create dedicated non-root user and ensure ownership of /app
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash -m appuser && \
    chown -R appuser:appuser /app

USER appuser

# Supervised production entrypoint monitoring bot.py and dashboard.py
CMD ["python", "scripts/supervise_services.py"]
