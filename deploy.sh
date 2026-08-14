#!/bin/bash
# ==============================================================================
# DEPLOY.SH - Automated Cloud Deployment Script (Ubuntu VPS)
# ==============================================================================

echo "🚀 Starting Automated Deployment for Python Trading Bot..."

# 1. System Updates
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# 2. Install Python 3.11 and dependencies
echo "🐍 Installing Python 3.11 and Git..."
sudo apt install -y python3.11 python3.11-venv python3-pip git screen

# 3. Clone Repository (if not already cloned)
if [ ! -d "python-trading-bot" ]; then
    echo "📥 Cloning repository..."
    git clone https://github.com/surendra2304/python-trading-bot.git
fi

cd python-trading-bot

# 4. Create Virtual Environment
echo "🔧 Setting up Python virtual environment..."
python3.11 -m venv venv
source venv/bin/activate

# 5. Install Python Packages
echo "📚 Installing required libraries (this may take a minute)..."
pip install --upgrade pip
pip install -r requirements.txt

# 6. Check for Configuration
if [ ! -f "config.py" ]; then
    echo "⚠️  WARNING: config.py not found!"
    echo "Creating from template..."
    cp config_template.py config.py
    echo "❌ DEPLOYMENT PAUSED: Please edit config.py to add your Binance API keys, then run this script again."
    exit 1
fi

# 7. Start the Bot using Systemd or Screen
echo "🔄 Creating background daemon to run bot 24/7..."

cat <<EOF > tradingbot.service
[Unit]
Description=Python Trading Bot
After=network.target

[Service]
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo mv tradingbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tradingbot
sudo systemctl start tradingbot

echo "=========================================================="
echo "✅ DEPLOYMENT SUCCESSFUL!"
echo "Your bot is now running in the background 24/7."
echo ""
echo "To view live logs: sudo journalctl -u tradingbot -f"
echo "To stop the bot  : sudo systemctl stop tradingbot"
echo "To check status  : sudo systemctl status tradingbot"
echo "=========================================================="
