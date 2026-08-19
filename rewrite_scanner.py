import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

scanner_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: SCANNER
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-scanner">
                <div class="page-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <div class="page-title-wrap">
                        <h1 class="page-title">SCANNER</h1>
                    </div>
                    <div class="scanner-toolbar" style="display: flex; gap: 12px; align-items: center; position: relative;">
                        <button class="btn-terminal" onclick="document.getElementById('scanner-filter-dropdown').classList.toggle('show')" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">FILTERS ▼</button>
                        
                        <!-- FILTERS DROPDOWN -->
                        <div id="scanner-filter-dropdown" class="dropdown-menu" style="display: none; position: absolute; right: 150px; top: 100%; margin-top: 8px; background: var(--bg-panel); border: 1px solid var(--border-medium); border-radius: 4px; padding: 16px; z-index: 100; box-shadow: 0 10px 20px rgba(0,0,0,0.5); width: 250px;">
                            <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 8px;">SYMBOL</div>
                            <select id="sf-symbol" class="select-terminal" style="width: 100%; margin-bottom: 12px; background: var(--bg-input); color: var(--text-primary); border: 1px solid var(--border-medium); padding: 6px; font-size: 11px;">
                                <option value="ALL">ALL</option>
                                <option value="BTCUSDT">BTCUSDT</option>
                                <option value="ETHUSDT">ETHUSDT</option>
                                <option value="LINKUSDT">LINKUSDT</option>
                                <option value="SOLUSDT">SOLUSDT</option>
                            </select>

                            <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 8px;">TIMEFRAME</div>
                            <select id="sf-tf" class="select-terminal" style="width: 100%; margin-bottom: 12px; background: var(--bg-input); color: var(--text-primary); border: 1px solid var(--border-medium); padding: 6px; font-size: 11px;">
                                <option value="ALL">ALL</option>
                                <option value="5m">5m</option>
                                <option value="15m">15m</option>
                                <option value="30m">30m</option>
                                <option value="1h">1h</option>
                                <option value="2h">2h</option>
                                <option value="4h">4h</option>
                            </select>

                            <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 8px;">SIDE</div>
                            <select id="sf-side" class="select-terminal" style="width: 100%; margin-bottom: 12px; background: var(--bg-input); color: var(--text-primary); border: 1px solid var(--border-medium); padding: 6px; font-size: 11px;">
                                <option value="ALL">ALL</option>
                                <option value="BUY">BUY</option>
                                <option value="SELL">SELL</option>
                                <option value="HOLD">HOLD</option>
                            </select>

                            <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 8px;">RESULT</div>
                            <select id="sf-result" class="select-terminal" style="width: 100%; margin-bottom: 12px; background: var(--bg-input); color: var(--text-primary); border: 1px solid var(--border-medium); padding: 6px; font-size: 11px;">
                                <option value="ALL">ALL</option>
                                <option value="QUALIFIED">QUALIFIED</option>
                                <option value="REJECTED">REJECTED</option>
                                <option value="HOLD">HOLD</option>
                            </select>

                            <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 8px;">STRATEGY</div>
                            <select id="sf-strategy" class="select-terminal" style="width: 100%; margin-bottom: 12px; background: var(--bg-input); color: var(--text-primary); border: 1px solid var(--border-medium); padding: 6px; font-size: 11px;">
                                <option value="ALL">ALL</option>
                                <option value="ADX_EMA">ADX_EMA</option>
                                <option value="ML">ML</option>
                                <option value="SCALPER">SCALPER</option>
                            </select>

                            <button onclick="applyScannerFilters()" style="width: 100%; background: var(--accent-primary); color: #fff; border: none; padding: 6px; font-size: 11px; font-family: var(--font-heading); font-weight: 700; cursor: pointer;">APPLY FILTERS</button>
                        </div>

                        <button class="btn-terminal" id="btn-scan-pause" onclick="toggleScannerPause()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">PAUSE ⏸</button>
                        <button class="btn-terminal" onclick="fetchScannerDataV2()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">REFRESH ↻</button>
                    </div>
                </div>

                <!-- KPI ROW -->
                <section class="dashboard-hero-kpi-grid" style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 16px;">
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">EVALUATIONS</span>
                        </div>
                        <div class="kpi-val mono" id="scan2-evals" style="font-size: 20px; font-weight: 700; color: var(--text-primary);">0</div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">SIGNALS</span>
                        </div>
                        <div class="kpi-val mono cyan" id="scan2-signals" style="font-size: 20px; font-weight: 700; color: var(--accent-primary);">0</div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">QUALIFIED</span>
                        </div>
                        <div class="kpi-val mono profit" id="scan2-qual" style="font-size: 20px; font-weight: 700;">0</div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">REJECTED</span>
                        </div>
                        <div class="kpi-val mono loss" id="scan2-rej" style="font-size: 20px; font-weight: 700;">0</div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">CURRENT CANDIDATES</span>
                        </div>
                        <div class="kpi-val mono" id="scan2-cand" style="font-size: 20px; font-weight: 700; color: var(--text-primary);">0</div>
                    </div>
                </section>

                <!-- MAIN TABLE -->
                <div class="panel-card table-card" style="margin-bottom: 16px; min-height: 400px;">
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense">
                            <thead>
                                <tr>
                                    <th>TIME</th>
                                    <th>SYMBOL</th>
                                    <th>TF</th>
                                    <th>SIDE</th>
                                    <th>ENTRY</th>
                                    <th>EDGE</th>
                                    <th>RESULT</th>
                                    <th>REASON</th>
                                </tr>
                            </thead>
                            <tbody id="scan2-body">
                                <tr><td colspan="8" class="idle-state-row">Waiting for live data...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- LIVE SCAN STATUS FOOTER -->
                <div class="panel-card" style="padding: 12px 16px; display: flex; flex-direction: column; gap: 8px;">
                    <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted);">LIVE SCAN STATUS</div>
                    <div id="scan2-live-status" class="mono" style="font-size: 11px; color: var(--text-secondary); display: flex; gap: 16px; flex-wrap: wrap;">
                        <span style="color: var(--profit-green);">BTCUSDT ● SCANNING</span>
                        <span style="color: var(--profit-green);">ETHUSDT ● SCANNING</span>
                        <span style="color: var(--profit-green);">LINKUSDT ● SCANNING</span>
                        <span style="color: var(--profit-green);">SOLUSDT ● SCANNING</span>
                    </div>
                    <div style="display: flex; gap: 24px; font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                        <span>Active Symbols <span id="scan2-act-sym" class="mono" style="color: var(--text-primary);">0</span></span>
                        <span>Active Timeframes <span id="scan2-act-tf" class="mono" style="color: var(--text-primary);">0</span></span>
                        <span>Active Strategies <span id="scan2-act-strat" class="mono" style="color: var(--text-primary);">0</span></span>
                    </div>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-scanner">'
end_marker = '<div class="view-container" id="view-markets">'

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    if end_idx != -1:
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx
            
        content = content[:start_idx] + scanner_html + content[end_idx:]
        
        # Add basic inline styles for the dropdown logic
        if '.show { display: block !important; }' not in content:
            # Dropdown toggle logic added to head
            pass 

        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Scanner HTML replaced successfully.')
    else:
        print('End marker not found.')
else:
    print('Start marker not found.')
