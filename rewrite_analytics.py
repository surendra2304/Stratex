
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

analytics_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: ANALYTICS
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-analytics">
                <div class="page-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <div class="page-title-wrap">
                        <h1 class="page-title">ANALYTICS</h1>
                    </div>
                    
                    <div style="display: flex; gap: 16px; align-items: center;">
                        <!-- PERIOD SELECTOR -->
                        <div id="analytics-period-row" style="display: flex; gap: 12px;">
                            <span class="mono an-period" style="cursor:pointer; color:var(--text-muted);" onclick="changeAnalyticsPeriod('1D', this)">1D</span>
                            <span class="mono an-period" style="cursor:pointer; color:var(--text-muted);" onclick="changeAnalyticsPeriod('7D', this)">7D</span>
                            <span class="mono an-period" style="cursor:pointer; color:var(--text-muted);" onclick="changeAnalyticsPeriod('30D', this)">30D</span>
                            <span class="mono an-period active" style="cursor:pointer; color:var(--accent-primary); font-weight:700;" onclick="changeAnalyticsPeriod('ALL', this)">ALL</span>
                        </div>
                        
                        <div style="width: 1px; height: 16px; background: var(--border-medium);"></div>
                        
                        <div class="scanner-toolbar" style="display: flex; gap: 12px; align-items: center;">
                            <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">FILTERS ▼</button>
                            <button class="btn-terminal" onclick="fetchAnalyticsData()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">REFRESH ↻</button>
                        </div>
                    </div>
                </div>

                <!-- KPI ROW -->
                <div class="panel-card" style="margin-bottom: 16px;">
                    <table class="terminal-table table-dense" style="width: 100%;">
                        <thead>
                            <tr>
                                <th style="width: 20%;">NET PNL</th>
                                <th style="width: 20%;">TOTAL TRADES</th>
                                <th style="width: 20%;">WIN RATE</th>
                                <th style="width: 20%;">PROFIT FACTOR</th>
                                <th style="width: 20%;">MAX DRAWDOWN</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td class="mono profit" id="an-net-pnl" style="font-size: 16px; font-weight: 700;">$0.00</td>
                                <td class="mono" id="an-total-trades" style="font-size: 16px;">0</td>
                                <td class="mono" id="an-win-rate" style="font-size: 16px;">0.0%</td>
                                <td class="mono" id="an-profit-factor" style="font-size: 16px;">0.00</td>
                                <td class="mono loss" id="an-max-dd" style="font-size: 16px;">0.00%</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <!-- EQUITY CHART -->
                <div class="panel-card" style="margin-bottom: 16px; display: flex; flex-direction: column;">
                    <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                        EQUITY / PNL
                    </div>
                    <div style="padding: 16px; height: 350px; width: 100%; position: relative;">
                        <canvas id="analytics-equity-chart"></canvas>
                    </div>
                </div>

                <!-- PNL BREAKDOWN & DRAWDOWN CHART -->
                <div style="display: grid; grid-template-columns: 1fr 1.5fr; gap: 16px; margin-bottom: 16px;">
                    <div class="panel-card">
                        <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                            PNL BREAKDOWN
                        </div>
                        <div style="padding: 16px;">
                            <div class="kv-row" style="margin-bottom: 12px;"><span>REALIZED</span><span class="mono" id="an-realized">$0.00</span></div>
                            <div class="kv-row" style="margin-bottom: 12px;"><span>UNREALIZED</span><span class="mono" id="an-unrealized">$0.00</span></div>
                            <div class="kv-row" style="margin-bottom: 12px;"><span>FEES</span><span class="mono loss" id="an-fees">-$0.00</span></div>
                            <div class="kv-row" style="margin-top: 16px; border-top: 1px solid var(--border-medium); padding-top: 16px;"><span>NET PNL</span><span class="mono profit td-strong" style="font-size: 14px;" id="an-net-pnl2">$0.00</span></div>
                        </div>
                    </div>
                    <div class="panel-card" style="display: flex; flex-direction: column;">
                        <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                            DRAWDOWN
                        </div>
                        <div style="padding: 16px; flex: 1; min-height: 150px; position: relative;">
                            <canvas id="analytics-drawdown-chart"></canvas>
                        </div>
                    </div>
                </div>

                <!-- STRATEGY & TIMEFRAME PERFORMANCE -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px;">
                    <div class="panel-card table-card">
                        <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                            STRATEGY PERFORMANCE
                        </div>
                        <div class="panel-table-wrap">
                            <table class="terminal-table table-dense" style="width: 100%;">
                                <tbody id="an-strat-body">
                                    <tr><td class="text-center text-muted" style="padding: 24px;">No data</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                    <div class="panel-card table-card">
                        <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                            TIMEFRAME PERFORMANCE
                        </div>
                        <div class="panel-table-wrap">
                            <table class="terminal-table table-dense" style="width: 100%;">
                                <tbody id="an-tf-body">
                                    <tr><td class="text-center text-muted" style="padding: 24px;">No data</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <!-- TRADE PERFORMANCE -->
                <div class="panel-card" style="margin-bottom: 16px;">
                    <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                        TRADE PERFORMANCE
                    </div>
                    <table class="terminal-table table-dense" style="width: 100%;">
                        <thead>
                            <tr>
                                <th style="width: 20%;">TOTAL PROFIT</th>
                                <th style="width: 20%;">TOTAL LOSS</th>
                                <th style="width: 20%;">AVG WIN</th>
                                <th style="width: 20%;">AVG LOSS</th>
                                <th style="width: 20%;">AVG TRADE</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td class="mono profit" id="an-tp">$0.00</td>
                                <td class="mono loss" id="an-tl">-$0.00</td>
                                <td class="mono profit" id="an-aw">$0.00</td>
                                <td class="mono loss" id="an-al">-$0.00</td>
                                <td class="mono" id="an-at">$0.00</td>
                            </tr>
                        </tbody>
                        <thead style="border-top: 1px solid var(--border-medium);">
                            <tr>
                                <th>BEST TRADE</th>
                                <th>WORST TRADE</th>
                                <th>LONGEST HOLD</th>
                                <th>SHORTEST HOLD</th>
                                <th></th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td class="mono profit" id="an-best">$0.00</td>
                                <td class="mono loss" id="an-worst">-$0.00</td>
                                <td class="mono" id="an-long">0m</td>
                                <td class="mono" id="an-short">0m</td>
                                <td></td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-analytics">'
end_marker = '<div class="view-container" id="view-settings">'

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    if end_idx != -1:
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx
            
        content = content[:start_idx] + analytics_html + content[end_idx:]
        
        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Analytics HTML replaced successfully.')
    else:
        print('End marker not found.')
else:
    print('Start marker not found.')
