
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

markets_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: MARKETS
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-markets">
                <div class="page-header" style="margin-bottom: 16px;">
                    <h1 class="page-title">MARKETS</h1>
                </div>

                <!-- SYMBOL SELECTOR -->
                <div id="mkt-symbol-row" style="display: flex; gap: 16px; margin-bottom: 16px; overflow-x: auto; padding-bottom: 8px; border-bottom: 1px solid var(--border-medium);">
                    <span class="mono mkt-sym active" style="cursor:pointer; color:var(--text-primary); font-weight:700;" onclick="changeMarketSymbol('BTCUSDT', this)">BTCUSDT</span>
                    <span class="mono mkt-sym" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketSymbol('ETHUSDT', this)">ETHUSDT</span>
                    <span class="mono mkt-sym" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketSymbol('LINKUSDT', this)">LINKUSDT</span>
                    <span class="mono mkt-sym" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketSymbol('SOLUSDT', this)">SOLUSDT</span>
                    <span class="mono mkt-sym" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketSymbol('DOGEUSDT', this)">DOGEUSDT</span>
                    <span class="mono mkt-sym" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketSymbol('XRPUSDT', this)">XRPUSDT</span>
                    <span class="mono mkt-sym" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketSymbol('BNBUSDT', this)">BNBUSDT</span>
                    <span class="mono mkt-sym" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketSymbol('ADAUSDT', this)">ADAUSDT</span>
                </div>

                <!-- TICKER BAR -->
                <div class="panel-card" style="padding: 16px; display: flex; align-items: center; gap: 24px; margin-bottom: 16px; flex-wrap: wrap;">
                    <div style="font-family: var(--font-heading); font-size: 24px; font-weight: 700; color: var(--text-primary);" id="mkt-ticker">BTCUSDT</div>
                    <div class="mono" style="font-size: 24px; font-weight: 700; color: var(--text-primary);" id="mkt-price">0.00</div>
                    <div class="mono profit" style="font-size: 16px; font-weight: 700;" id="mkt-change">+0.00%</div>
                    <div style="width: 1px; height: 32px; background: var(--border-medium);"></div>
                    <div style="display: flex; flex-direction: column;">
                        <span style="font-size: 10px; color: var(--text-muted); font-weight: 700; letter-spacing: 0.5px;">24H HIGH</span>
                        <span class="mono" style="font-size: 13px;" id="mkt-high">0.00</span>
                    </div>
                    <div style="display: flex; flex-direction: column;">
                        <span style="font-size: 10px; color: var(--text-muted); font-weight: 700; letter-spacing: 0.5px;">24H LOW</span>
                        <span class="mono" style="font-size: 13px;" id="mkt-low">0.00</span>
                    </div>
                    <div style="display: flex; flex-direction: column;">
                        <span style="font-size: 10px; color: var(--text-muted); font-weight: 700; letter-spacing: 0.5px;">VOLUME</span>
                        <span class="mono" style="font-size: 13px;" id="mkt-vol">0.00</span>
                    </div>
                    <div style="margin-left: auto; display: flex; align-items: center; gap: 8px;">
                        <div class="pulse-dot" style="width: 8px; height: 8px; background: var(--profit-green); border-radius: 50%; animation: pulse 2s infinite;"></div>
                        <span class="mono" style="font-size: 12px; font-weight: 700; color: var(--profit-green);">LIVE</span>
                    </div>
                </div>

                <!-- CHART CONTAINER -->
                <div class="panel-card" style="display: flex; flex-direction: column; margin-bottom: 16px; min-height: 500px; height: 60vh;">
                    <!-- CHART TOOLBAR -->
                    <div style="display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; border-bottom: 1px solid var(--border-medium);">
                        <div style="display: flex; gap: 16px; align-items: center;">
                            <!-- TIMEFRAMES -->
                            <div id="mkt-tf-row" style="display: flex; gap: 12px;">
                                <span class="mono mkt-tf" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketTimeframe('5m', this)">5m</span>
                                <span class="mono mkt-tf active" style="cursor:pointer; color:var(--accent-primary); font-weight:700;" onclick="changeMarketTimeframe('15m', this)">15m</span>
                                <span class="mono mkt-tf" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketTimeframe('30m', this)">30m</span>
                                <span class="mono mkt-tf" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketTimeframe('1h', this)">1h</span>
                                <span class="mono mkt-tf" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketTimeframe('2h', this)">2h</span>
                                <span class="mono mkt-tf" style="cursor:pointer; color:var(--text-muted);" onclick="changeMarketTimeframe('4h', this)">4h</span>
                            </div>
                            
                            <div style="width: 1px; height: 16px; background: var(--border-medium);"></div>
                            
                            <!-- DROPDOWNS -->
                            <div class="dropdown">
                                <span class="mono text-muted" style="cursor:pointer; font-size: 12px;">CANDLESTICK ▾</span>
                            </div>
                            <div style="width: 1px; height: 16px; background: var(--border-medium);"></div>
                            <div class="dropdown">
                                <span class="mono text-muted" style="cursor:pointer; font-size: 12px;">INDICATORS ▾</span>
                            </div>
                            <div style="width: 1px; height: 16px; background: var(--border-medium);"></div>
                            <div class="dropdown">
                                <span class="mono text-muted" style="cursor:pointer; font-size: 12px;">DRAW ▾</span>
                            </div>
                        </div>
                        <div>
                            <span class="mono text-muted" style="cursor:pointer; font-size: 14px;" title="Fullscreen">⛶</span>
                        </div>
                    </div>
                    
                    <!-- CHART CANVAS -->
                    <div style="flex: 1; position: relative; width: 100%; padding: 16px;">
                        <canvas id="markets-main-chart"></canvas>
                    </div>
                </div>

                <!-- MARKET INFORMATION -->
                <div class="panel-card table-card">
                    <div style="padding: 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 0.8px;">
                        MARKET INFORMATION
                    </div>
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense">
                            <thead>
                                <tr>
                                    <th>PRICE</th>
                                    <th>OPEN</th>
                                    <th>HIGH</th>
                                    <th>LOW</th>
                                    <th>VOLUME</th>
                                    <th>24H CHANGE</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td class="mono" id="mi-price">0.00</td>
                                    <td class="mono" id="mi-open">0.00</td>
                                    <td class="mono" id="mi-high">0.00</td>
                                    <td class="mono" id="mi-low">0.00</td>
                                    <td class="mono" id="mi-vol">0.00</td>
                                    <td class="mono" id="mi-change">0.00%</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-markets">'
end_marker = '<div class="view-container" id="view-strategies">'

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    if end_idx != -1:
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx
            
        content = content[:start_idx] + markets_html + content[end_idx:]
        
        # Inject pulse animation to CSS if not exists
        if '@keyframes pulse' not in content:
            pulse_css = """
<style>
@keyframes pulse {
    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
    70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
}
</style>
"""
            content = content.replace('</head>', pulse_css + '</head>')
            
        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Markets HTML replaced successfully.')
    else:
        print('End marker not found.')
else:
    print('Start marker not found.')
