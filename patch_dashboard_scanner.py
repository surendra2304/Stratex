import os

dashboard_file = 'd:/MT5/python_bot/dashboard.py'
with open(dashboard_file, 'r', encoding='utf-8') as f:
    content = f.read()

new_endpoint = '''
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
'''

if '/api/scanner' not in content:
    content += new_endpoint
    with open(dashboard_file, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Added /api/scanner endpoint')
else:
    print('Endpoint already exists')
