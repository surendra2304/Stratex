"""
telemetry/live_telemetry.py — Live Capital Telemetry, Metrics Aggregator & HTML/JSON Performance Reporter.

Tracks:
1. Realized, Unrealized, and Total PnL in real time.
2. Capital utilization % and available margin.
3. Live risk metrics: Current Drawdown %, VaR, Strategy exposure.
4. Auto-generates Daily Performance Reports (HTML + JSON).
"""

import datetime
import json
import os
import time
from typing import Dict, List, Optional, Tuple, Any
from metrics import calculate_metrics, calculate_drawdown


class LiveTelemetryReporter:
    """
    Compiles real-time metrics and publishes daily HTML & JSON performance summaries.
    """

    def generate_daily_live_report(
        self,
        trades_ledger_path: str = "live_trade_ledger.jsonl",
        equity_curve_path: str = "live_equity_curve.jsonl",
        initial_capital: float = 1000.0,
        output_json: str = "live_daily_report.json",
        output_html: str = "live_daily_report.html"
    ) -> Dict[str, Any]:
        """
        Calculates daily metrics and exports structured JSON and visual HTML report.
        """
        trades = []
        if os.path.exists(trades_ledger_path):
            with open(trades_ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            trades.append(json.loads(line.strip()))
                        except Exception:
                            pass

        metrics = calculate_metrics(trades, None, initial_balance=initial_capital)
        report_data = {
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "environment": "LIVE_TRADING",
            "initial_capital": initial_capital,
            "metrics": metrics
        }

        # Save JSON
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)

        # Save HTML
        pnl = metrics.get("net_pnl", 0.0)
        pnl_color = "#10B981" if pnl >= 0 else "#EF4444"
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Live Daily Performance Report</title>
    <style>
        body {{ font-family: monospace; background: #0F172A; color: #E2E8F0; padding: 24px; }}
        .card {{ background: #1E293B; border: 1px solid #334155; border-radius: 8px; padding: 20px; max-width: 650px; margin: 0 auto; }}
        h2 {{ color: #60A5FA; margin-top: 0; }}
        .metric-row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        .val {{ font-weight: bold; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>⚡ LIVE CAPITAL PERFORMANCE REPORT</h2>
        <div class="metric-row"><span>Generated</span><span class="val">{report_data['generated_at']}</span></div>
        <div class="metric-row"><span>Initial Capital</span><span class="val">${initial_capital:.2f}</span></div>
        <div class="metric-row"><span>Net PnL</span><span class="val" style="color: {pnl_color};">${pnl:.2f}</span></div>
        <div class="metric-row"><span>Total Closed Trades</span><span class="val">{metrics.get('total_trades', 0)}</span></div>
        <div class="metric-row"><span>Win Rate</span><span class="val">{metrics.get('win_rate', 0.0):.2f}%</span></div>
        <div class="metric-row"><span>Profit Factor</span><span class="val">{metrics.get('profit_factor', 0.0):.2f}</span></div>
        <div class="metric-row"><span>Max Drawdown</span><span class="val">{metrics.get('max_dd_pct', 0.0):.2f}%</span></div>
    </div>
</body>
</html>"""
        with open(output_html, "w", encoding="utf-8") as f:
            f.write(html_content)

        return report_data
