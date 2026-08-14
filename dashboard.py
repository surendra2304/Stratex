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

@app.route('/api/status')
def get_status():
    from config import TRADING_MODE
    import time
    
    bot_health = "UNKNOWN"
    data_health = "UNKNOWN"
    
    if TRADING_MODE == "PAPER":
        bot_health = "OK"  
        data_health = "OK"
        try:
            from paper_engine.heartbeat import HeartbeatState
            hb = HeartbeatState()
            b_health, d_health = hb.get_status()
            if b_health != "UNKNOWN":
                bot_health = b_health
            if d_health != "UNKNOWN":
                data_health = d_health
        except:
            pass
        if os.path.exists("paper_portfolio.json"):
            try:
                import json
                with open("paper_portfolio.json", "r") as f:
                    port = json.load(f)
                
                # Fetch recent market prices to compute true equity
                try:
                    from data import get_candles
                    df = get_candles("BTCUSDT", "1m", 1)
                    current_price = df['close'].iloc[-1] if not df.empty else 0.0
                except:
                    current_price = 0.0
                
                # Compute unrealized
                unrealized = 0.0
                for pos in port.get("positions", {}).values():
                    if pos['status'] == "OPEN" and current_price > 0:
                        if pos['direction'] in ["LONG", "BUY"]:
                            unrealized += (current_price - pos['entry_price']) * pos['quantity']
                        else:
                            unrealized += (pos['entry_price'] - current_price) * pos['quantity']
                            
                cash = port.get("cash", 0)
                equity = cash + unrealized
                
                try:
                    from paper_engine.portfolio import PaperPortfolio
                    temp_port = PaperPortfolio("paper_portfolio.json")
                    mdd = temp_port.get_max_drawdown() * 100
                except Exception as e:
                    mdd = 0.0
                
                return jsonify({
                    "mode": TRADING_MODE,
                    "bot_health": bot_health,
                    "data_health": data_health,
                    "equity": equity,
                    "cash": cash,
                    "realized_pnl": port.get("realized_pnl", 0),
                    "unrealized_pnl": unrealized,
                    "fees": port.get("cumulative_fees", 0),
                    "funding": port.get("cumulative_funding", 0),
                    "used_margin": port.get("used_margin", 0),
                    "open_positions": len([p for p in port.get("positions", {}).values() if p["status"] == "OPEN"]),
                    "max_drawdown": mdd
                })
            except Exception as e:
                pass
    
    # Fallback / Testnet / Error
    return jsonify({
        "mode": TRADING_MODE,
        "bot_health": "UNKNOWN",
        "data_health": "UNKNOWN",
        "equity": 0,
        "cash": 0,
        "realized_pnl": 0,
        "unrealized_pnl": 0,
        "open_positions": 0
    })

@app.route('/api/trades')
def get_trades():
    """Parses logs or paper portfolio to return positions."""
    from config import TRADING_MODE
    
    if TRADING_MODE == "PAPER":
        import json
        net_pnl = 0.0
        wins = 0
        losses = 0
        gross_profit = 0.0
        gross_loss = 0.0
        positions = []
        
        # 1. Parse closed trades from ledger
        if os.path.exists("paper_trade_ledger.jsonl"):
            with open("paper_trade_ledger.jsonl", "r") as f:
                for line in f:
                    try:
                        trade = json.loads(line)
                        pnl = trade.get("net_pnl", 0.0)
                        
                        if pnl > 0:
                            wins += 1
                            gross_profit += pnl
                        else:
                            losses += 1
                            gross_loss += abs(pnl)
                            
                        positions.append({
                            "timestamp": trade.get("entry_time", trade.get("signal_time", 0)),
                            "symbol": trade.get("symbol", ""),
                            "action": trade.get("direction", ""),
                            "entry_price": trade.get("entry_price", 0.0),
                            "quantity": trade.get("quantity", 0.0),
                            "status": "CLOSED",
                            "pnl": pnl,
                            "matched": True
                        })
                    except:
                        pass
        
        # 2. Add open positions from portfolio
        if os.path.exists("paper_portfolio.json"):
            try:
                with open("paper_portfolio.json", "r") as f:
                    port = json.load(f)
                net_pnl = port.get("realized_pnl", 0.0)
                
                for pos_id, p in port.get("positions", {}).items():
                    if p["status"] == "OPEN":
                        positions.append({
                            "timestamp": p.get("open_time", 0),
                            "symbol": p["symbol"],
                            "action": p["direction"],
                            "entry_price": p["entry_price"],
                            "quantity": p["quantity"],
                            "status": p["status"],
                            "pnl": 0.0, # Unrealized
                            "matched": False
                        })
            except:
                pass
                
        positions.sort(key=lambda x: x["timestamp"], reverse=True)
        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else ("Infinity" if gross_profit > 0 else 0)
        
        return jsonify({
            "net_pnl": net_pnl, 
            "win_rate": win_rate,
            "total_trades": total_closed,
            "profit_factor": profit_factor,
            "positions": positions
        })

    # TESTNET logic
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
