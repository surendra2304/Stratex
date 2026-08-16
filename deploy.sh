#!/bin/bash
# ==============================================================================
# DEPLOY.SH - Automated Cloud Deployment Script (Ubuntu/Debian VPS)
# ==============================================================================
# Deploys:
#   1. tradingbot.service       -> Python Testnet Engine (bot.py)
#   2. tradingdashboard.service -> Web Dashboard UI (dashboard.py)
# ==============================================================================

set -e

echo "=========================================================="
echo "🚀 Algorithmic Trading Bot - Automated VPS Deployment"
echo "=========================================================="

# 1. System Updates & Core Dependencies
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip git curl ufw

# 2. Configure Firewall
echo "🛡️  Configuring Firewall (UFW)..."
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 5000/tcp  # Dashboard UI
sudo ufw --force enable

APP_DIR=$(pwd)
echo "📂 Application Directory: $APP_DIR"

# 3. Create Python Virtual Environment
if [ ! -d "venv" ]; then
    echo "🔧 Setting up Python 3.11 virtual environment..."
    python3.11 -m venv venv
fi

source venv/bin/activate

# 4. Install Python Packages
echo "📚 Installing dependencies from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. Check & Validate Environment Configuration (.env)
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found!"
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example."
    fi
    echo ""
    echo "❌ ACTION REQUIRED: Please edit .env with your Binance Testnet keys:"
    echo "   nano .env"
    echo "Then run ./deploy.sh again."
    exit 1
fi

# Ensure mandatory Testnet safety settings in .env
grep -q "TRADING_MODE=" .env || echo 'TRADING_MODE="TESTNET"' >> .env
grep -q "TESTNET_ENABLED=" .env || echo 'TESTNET_ENABLED="True"' >> .env
grep -q "LIVE_TRADING_ENABLED=" .env || echo 'LIVE_TRADING_ENABLED="False"' >> .env

# 6. Run Test Suite before deploying
echo "🧪 Running full test suite..."
pytest

# 7. Create Systemd Services

# Service 1: Trading Bot
echo "⚙️  Configuring tradingbot.service..."
cat <<EOF | sudo tee /etc/systemd/system/tradingbot.service > /dev/null
[Unit]
Description=Binance Testnet Quantitative Trading Engine
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=TRADING_MODE=TESTNET
Environment=TESTNET_ONLY=TRUE
ExecStart=$APP_DIR/venv/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=append:$APP_DIR/bot.log
StandardError=append:$APP_DIR/bot.log

[Install]
WantedBy=multi-user.target
EOF

# Service 2: Dashboard UI
echo "⚙️  Configuring tradingdashboard.service..."
cat <<EOF | sudo tee /etc/systemd/system/tradingdashboard.service > /dev/null
[Unit]
Description=Binance Testnet Trading Terminal Dashboard
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=TRADING_MODE=TESTNET
Environment=TESTNET_ONLY=TRUE
ExecStart=$APP_DIR/venv/bin/python dashboard.py
Restart=always
RestartSec=10
StandardOutput=append:$APP_DIR/dashboard.log
StandardError=append:$APP_DIR/dashboard.log

[Install]
WantedBy=multi-user.target
EOF

# 8. Reload and Enable Services
echo "🔄 Reloading systemd daemon and starting services..."
sudo systemctl daemon-reload

sudo systemctl enable tradingbot
sudo systemctl restart tradingbot

sudo systemctl enable tradingdashboard
sudo systemctl restart tradingdashboard

echo "=========================================================="
echo "✅ DEPLOYMENT COMPLETE & RUNNING 24/7"
echo "=========================================================="
echo ""
echo "📊 Services Status:"
sudo systemctl status tradingbot --no-pager -l
echo ""
sudo systemctl status tradingdashboard --no-pager -l
echo ""
echo "🔗 Dashboard Access: http://$(curl -s ifconfig.me || echo 'YOUR_VPS_IP'):5000"
echo ""
echo "🛠️  Useful Management Commands:"
echo "   View Bot Logs       : sudo journalctl -u tradingbot -f"
echo "   View Dashboard Logs : sudo journalctl -u tradingdashboard -f"
echo "   Restart Bot         : sudo systemctl restart tradingbot"
echo "   Stop Bot            : sudo systemctl stop tradingbot"
echo "   Check Bot Status    : sudo systemctl status tradingbot"
echo "=========================================================="

