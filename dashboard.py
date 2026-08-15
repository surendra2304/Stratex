import os
import csv
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace') if __name__ == '__main__' else sys.stdout
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

# Legacy /api/backtest removed to focus purely on live Testnet operations.

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
    
    if TRADING_MODE != "PAPER":
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
        
    try:
        from paper_engine.heartbeat import HeartbeatState
        hb = HeartbeatState()
        components = hb.components
        overall = hb.get_overall_health().value
    except Exception as e:
        from logger import get_logger
        get_logger("dashboard").error(f"Failed to read heartbeat: {e}")
        components = {}
        overall = "UNKNOWN"
        
    try:
        from paper_engine.session import SessionState
        session = SessionState()
        session_info = {
            "id": session.session_id,
            "status": session.status,
            "start": session.start_time
        }
    except Exception as e:
        from logger import get_logger
        get_logger("dashboard").error(f"Failed to read session: {e}")
        session_info = {"status": "UNKNOWN"}
        
    try:
        from paper_engine.alerts import AlertManager
        alert_mgr = AlertManager()
        alerts = [v for k, v in alert_mgr.active_alerts.items()]
    except Exception as e:
        from logger import get_logger
        get_logger("dashboard").error(f"Failed to read alerts: {e}")
        alerts = []
        
    equity = 10000.0
    cash = 10000.0
    realized_pnl = 0.0
    unrealized_pnl = 0.0
    fees = 0.0
    funding = 0.0
    used_margin = 0.0
    open_positions = 0
    mdd = 0.0
    
    from config import TRADING_MODE
    portfolio_file = "testnet_portfolio.json" if TRADING_MODE == "TESTNET" else "paper_portfolio.json"
    if os.path.exists(portfolio_file):
        try:
            import json
            with open(portfolio_file, "r") as f:
                port = json.load(f)
            
            # Fetch recent market prices to compute true equity
            current_price = 0.0
            try:
                from data import get_candles
                df = get_candles("BTCUSDT", "1m", 1)
                if not df.empty:
                    current_price = df['close'].iloc[-1]
            except Exception as e:
                from logger import get_logger
                get_logger("dashboard").warning(f"Failed to fetch live price for equity calc: {e}")
            
            # Compute unrealized
            for pos in port.get("positions", {}).values():
                if pos['status'] == "OPEN" and current_price > 0:
                    if pos['direction'] in ["LONG", "BUY"]:
                        unrealized_pnl += (current_price - pos['entry_price']) * pos['quantity']
                    else:
                        unrealized_pnl += (pos['entry_price'] - current_price) * pos['quantity']
                        
            cash = port.get("cash", 10000.0)
            equity = cash + unrealized_pnl
            realized_pnl = port.get("realized_pnl", 0.0)
            fees = port.get("cumulative_fees", 0.0)
            funding = port.get("cumulative_funding", 0.0)
            used_margin = port.get("used_margin", 0.0)
            open_positions = len([p for p in port.get("positions", {}).values() if p["status"] == "OPEN"])
            
            try:
                if TRADING_MODE in ["PAPER", "TESTNET"]:
                    from paper_engine.portfolio import PaperPortfolio
                    temp_port = PaperPortfolio("paper_portfolio.json")
                    mdd = temp_port.get_max_drawdown() * 100
                else:
                    mdd = port.get("max_drawdown", 0.0) * 100
            except Exception as e:
                from logger import get_logger
                get_logger("dashboard").error(f"Failed to compute drawdown: {e}")
                
        except Exception as e:
            from logger import get_logger
            get_logger("dashboard").error(f"Failed to process portfolio for dashboard: {e}")
            overall = "STATE CORRUPTED" # We can't read the portfolio!

    return jsonify({
        "mode": TRADING_MODE,
        "overall_health": overall,
        "components": components,
        "session": session_info,
        "alerts": alerts,
        "equity": equity,
        "cash": cash,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "fees": fees,
        "funding": funding,
        "used_margin": used_margin,
        "open_positions": open_positions,
        "max_drawdown": mdd
    })

@app.route('/api/trades')
def get_trades():
    """Parses logs or paper portfolio to return positions."""
    from config import TRADING_MODE
    
    if TRADING_MODE in ["PAPER", "TESTNET"]:
        import json
        net_pnl = 0.0
        wins = 0
        losses = 0
        gross_profit = 0.0
        gross_loss = 0.0
        positions = []
        
        # 1. Parse closed trades from ledger
        ledger_file = "testnet_trade_ledger.jsonl" if TRADING_MODE == "TESTNET" else "paper_trade_ledger.jsonl"
        if os.path.exists(ledger_file):
            with open(ledger_file, "r") as f:
                for line in f:
                    try:
                        trade = json.loads(line)
                        pnl = trade.get("net_pnl", 0.0)
                        
                        if pnl > 0:
                            wins += 1
                            gross_profit += pnl
                        elif pnl < 0:
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
                    except Exception as e:
                        from logger import get_logger
                        get_logger("dashboard").error(f"Failed parsing ledger line: {e}")
        
        # 2. Add open positions from portfolio
        port_file = "testnet_portfolio.json" if TRADING_MODE == "TESTNET" else "paper_portfolio.json"
        if os.path.exists(port_file):
            try:
                with open(port_file, "r") as f:
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
            except Exception as e:
                from logger import get_logger
                get_logger("dashboard").error(f"Failed to read portfolio for trades: {e}")
                
        positions.sort(key=lambda x: x["timestamp"], reverse=True)
        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else "N/A"
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else ("Infinity" if gross_profit > 0 else ("N/A" if total_closed == 0 else 0))
        
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

@app.route('/api/scanner')
def get_scanner():
    from config import TRADING_MODE
    if TRADING_MODE != "TESTNET":
        from flask import jsonify
        return jsonify({})
        
    import json
    from flask import jsonify
    stats = {
        "symbols_scanned": 0,
        "signals_detected": 0,
        "signals_rejected": 0,
        "orders_submitted": 0,
        "top_opportunities": []
    }
    
    if os.path.exists("testnet_portfolio.json"):
        try:
            with open("testnet_portfolio.json", "r") as f:
                port = json.load(f)
                stats.update(port.get("scanner_stats", {}))
        except:
            pass
            
    if os.path.exists("testnet_opportunity_log.jsonl"):
        try:
            opps = []
            with open("testnet_opportunity_log.jsonl", "r") as f:
                for line in f:
                    opp = json.loads(line)
                    if opp.get("decision") == "ACCEPTED":
                        opps.append(opp)
            stats["top_opportunities"] = sorted(opps, key=lambda x: x.get("expected_net_return", 0), reverse=True)[:3]
        except:
            pass
            
    return jsonify(stats)
