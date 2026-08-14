import os
import csv
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from data import get_candles as fetch_candles
from config import TIMEFRAME

app = Flask(__name__, static_folder='static')
CORS(app)

LOG_FILE = "trade_log.csv"

@app.route('/')
def serve_index():
    return send_from_directory('static', 'index.html')


@app.route('/api/candles')
def get_candles():
    """Fetches live candles for the chart via the centralized data module."""
    try:
        symbol = request.args.get('symbol', 'BTCUSDT')
        df = fetch_candles(symbol, TIMEFRAME, 500)
        
        if df.empty:
            return jsonify({"error": "No data returned"}), 500
            
        formatted = []
        for _, row in df.iterrows():
            formatted.append({
                "time": int(row["timestamp"].timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"])
            })
        return jsonify(formatted)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/backtest')
def get_backtest():
    """Runs a rapid backtest on the last 7 days and returns the equity curve and metrics."""
    try:
        from backtester import fetch_historical_data, get_strategy_by_name
        from data import add_indicators
        from backtest_engine import BacktestEngine
        from metrics import calculate_metrics
        from config import BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, STARTING_BALANCE, RISK_PER_TRADE, ACTIVE_STRATEGY
        
        symbol = request.args.get('symbol', 'BTCUSDT')
        
        # We fetch less data for the dashboard to keep it snappy
        raw = fetch_historical_data(days=7)
        if raw.empty:
            return jsonify({"error": "No data"}), 500
            
        df = add_indicators(raw)
        strats = get_strategy_by_name(ACTIVE_STRATEGY)
        
        engine = BacktestEngine(df, strats, BACKTEST_FEE_RATE, BACKTEST_SLIPPAGE_RATE, STARTING_BALANCE, RISK_PER_TRADE)
        trades, equity_df = engine.run()
        metrics = calculate_metrics(trades, equity_df, STARTING_BALANCE)
        
        eq_curve = []
        if not equity_df.empty:
            for _, row in equity_df.iterrows():
                eq_curve.append({
                    "time": int(row['timestamp'].timestamp()),
                    "value": float(row['equity'])
                })
                
        return jsonify({
            "metrics": metrics,
            "equity_curve": eq_curve,
            "recent_trades": trades[-10:] if trades else [] # Last 10 trades
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
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
    """Parses trade_log.csv to return paired positions and advanced stats."""
    if not os.path.exists(LOG_FILE):
        return jsonify({"net_pnl": 0, "win_rate": 0, "total_trades": 0, "profit_factor": 0, "positions": []})
        
    positions = []
    total_pnl = 0.0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    
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
                    entry_side = side.split("_")[0] 
                    for p in positions:
                        if not p["matched"] and p["symbol"] == row["symbol"] and p["action"] == entry_side:
                            p["matched"] = True
                            
                            is_win = "WIN" in side
                            p["status"] = "WIN" if is_win else "LOSS"
                            
                            if is_win:
                                wins += 1
                            else:
                                losses += 1
                                
                            # Calculate PnL
                            if p["action"] == "BUY":
                                p["pnl"] = (price - p["entry_price"]) * p["quantity"]
                            else:
                                p["pnl"] = (p["entry_price"] - price) * p["quantity"]
                                
                            total_pnl += p["pnl"]
                            if p["pnl"] > 0:
                                gross_profit += p["pnl"]
                            else:
                                gross_loss += abs(p["pnl"])
                                
                            break
                            
        positions.reverse()
        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0)
        
        return jsonify({
            "net_pnl": total_pnl, 
            "win_rate": win_rate,
            "total_trades": total_closed,
            "profit_factor": profit_factor,
            "positions": positions
        })
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
    is_debug = os.environ.get('FLASK_DEBUG') == '1'
    app.run(debug=is_debug, port=5000)
