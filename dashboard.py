import os
import csv
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from binance.client import Client
from config import API_KEY, SECRET_KEY, SYMBOL, TIMEFRAME

app = Flask(__name__, static_folder='static')
CORS(app)

client = Client(API_KEY, SECRET_KEY, testnet=True)
LOG_FILE = "trade_log.csv"

@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')


@app.route('/api/candles')
def get_candles():
    """Fetches live candles for the chart."""
    try:
        raw = client.get_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=500)
        formatted = []
        for c in raw:
            formatted.append({
                "time": int(c[0] / 1000), # Lightweight charts wants integer seconds
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4])
            })
        return jsonify(formatted)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/chart_trades')
def get_chart_trades():
    """Parses trade_log.csv to return markers specifically formatted for the chart."""
    if not os.path.exists(LOG_FILE):
        return jsonify([])
        
    trades = []
    try:
        with open(LOG_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                import datetime
                dt = datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                timestamp = int(dt.timestamp())
                side = row["side"].upper()
                
                if side == "BUY":
                    trades.append({"time": timestamp, "position": "belowBar", "color": "#26a69a", "shape": "arrowUp"})
                elif side == "SELL":
                    trades.append({"time": timestamp, "position": "aboveBar", "color": "#ef5350", "shape": "arrowDown"})
                elif "WIN" in side:
                    trades.append({"time": timestamp, "position": "aboveBar", "color": "#2196F3", "shape": "circle", "text": f"WIN +$"})
                elif "LOSS" in side:
                    trades.append({"time": timestamp, "position": "belowBar", "color": "#FF9800", "shape": "circle", "text": f"LOSS -$"})
                    
        return jsonify(trades)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trades')
def get_trades():
    """Parses trade_log.csv to return paired positions and Net PnL."""
    if not os.path.exists(LOG_FILE):
        return jsonify({"net_pnl": 0, "positions": []})
        
    positions = []
    total_pnl = 0.0
    
    try:
        with open(LOG_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                side = row["side"].upper()
                price = float(row["price"])
                qty = float(row["quantity"])
                
                # Check if it's an entry
                if side in ["BUY", "SELL"]:
                    positions.append({
                        "timestamp": row["timestamp"],
                        "symbol": row["symbol"],
                        "action": side,
                        "entry_price": price,
                        "quantity": qty,
                        "status": "ACTIVE",
                        "pnl": 0.0,
                        "matched": False
                    })
                # Check if it's an exit
                elif "CLOSE" in side:
                    # Find the oldest unmatched position with the same symbol and correct side
                    # If side is BUY_CLOSE_WIN, the entry was BUY. 
                    entry_side = side.split("_")[0] 
                    
                    for p in positions:
                        if not p["matched"] and p["symbol"] == row["symbol"] and p["action"] == entry_side:
                            p["matched"] = True
                            p["status"] = "WIN" if "WIN" in side else "LOSS"
                            # Calculate PnL
                            if p["action"] == "BUY":
                                p["pnl"] = (price - p["entry_price"]) * p["quantity"]
                            else:
                                p["pnl"] = (p["entry_price"] - price) * p["quantity"]
                                
                            total_pnl += p["pnl"]
                            break
                            
        # Reverse to show newest positions first
        positions.reverse()
        return jsonify({"net_pnl": total_pnl, "positions": positions})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

if __name__ == '__main__':
    print("🚀 Starting Live Dashboard...")
    print("👉 Open http://127.0.0.1:5000 in your browser")
    app.run(debug=True, port=5000)
