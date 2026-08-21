
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

new_dashboard_html = """            <div class="view-container active" id="view-dashboard">
                <div class="page-header" style="margin-bottom: 16px;">
                    <div class="page-title-wrap">
                        <h1 class="page-title">Dashboard</h1>
                    </div>
                </div>

                <!-- 4 KPI CARDS -->
                <section class="dashboard-hero-kpi-grid" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 16px;">
                    <div class="panel-card kpi-card" onclick="showView('analytics')" style="cursor: pointer; padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">TOTAL ACCOUNT VALUE</span>
                        </div>
                        <div class="kpi-val mono" id="db2-total-account" style="font-size: 20px; font-weight: 700; color: var(--text-primary); margin-bottom: 8px;">$0.00</div>
                        <div class="kpi-sub" style="display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-secondary);">
                            <div style="display: flex; justify-content: space-between;"><span>Cash</span><span id="db2-cash" class="mono">$0.00</span></div>
                            <div style="display: flex; justify-content: space-between;"><span>Managed</span><span id="db2-managed" class="mono">$0.00</span></div>
                        </div>
                    </div>

                    <div class="panel-card kpi-card" onclick="showView('analytics')" style="cursor: pointer; padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">TODAY'S PNL</span>
                        </div>
                        <div class="kpi-val mono" id="db2-today-net" style="font-size: 20px; font-weight: 700; margin-bottom: 8px;">$0.00</div>
                        <div class="kpi-sub" style="display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-secondary);">
                            <div style="display: flex; justify-content: space-between;"><span>Trades <span id="db2-today-trades">0</span></span><span>Wins <span id="db2-today-wins" class="profit">0</span> / Losses <span id="db2-today-losses" class="loss">0</span></span></div>
                            <div style="display: flex; justify-content: space-between;"><span>Profit <span id="db2-today-profit" class="profit">+$0.00</span></span><span>Loss <span id="db2-today-loss" class="loss">-$0.00</span></span></div>
                        </div>
                    </div>

                    <div class="panel-card kpi-card" onclick="showView('trades')" style="cursor: pointer; padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">REALIZED PNL</span>
                        </div>
                        <div class="kpi-val mono" id="db2-realized-net" style="font-size: 20px; font-weight: 700; margin-bottom: 8px;">$0.00</div>
                        <div class="kpi-sub" style="display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-secondary);">
                            <div style="display: flex; justify-content: space-between;"><span>Closed Trades <span id="db2-realized-trades">0</span></span><span>Wins <span id="db2-realized-wins" class="profit">0</span> / Losses <span id="db2-realized-losses" class="loss">0</span></span></div>
                            <div style="display: flex; justify-content: space-between;"><span>Profit <span id="db2-realized-profit" class="profit">+$0.00</span></span><span>Loss <span id="db2-realized-loss" class="loss">-$0.00</span></span></div>
                        </div>
                    </div>

                    <div class="panel-card kpi-card" onclick="showView('positions')" style="cursor: pointer; padding: 16px;">
                        <div class="kpi-head" style="margin-bottom: 12px;">
                            <span class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); letter-spacing: 0.8px;">UNREALIZED PNL</span>
                        </div>
                        <div class="kpi-val mono" id="db2-unrealized-net" style="font-size: 20px; font-weight: 700; margin-bottom: 8px;">$0.00</div>
                        <div class="kpi-sub" style="display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-secondary);">
                            <div style="display: flex; justify-content: space-between;"><span>Open Positions <span id="db2-unrealized-pos">0</span></span><span>Floating PnL <span id="db2-unrealized-floating" class="mono">+$0.00</span></span></div>
                            <div style="display: flex; justify-content: space-between;"><span>Wins <span id="db2-unrealized-wins" class="profit">0</span></span><span>Losses <span id="db2-unrealized-losses" class="loss">0</span></span></div>
                        </div>
                    </div>
                </section>

                <!-- MARKET SCANNER SUMMARY -->
                <section class="dashboard-scanner-summary" style="margin-bottom: 16px;">
                    <div class="panel-card" onclick="showView('scanner')" style="cursor: pointer;">
                        <div class="panel-card-header" style="height: 36px; min-height: 36px; padding: 0 16px;">
                            <div class="panel-card-title">MARKET SCANNER</div>
                        </div>
                        <div class="panel-card-body" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; padding: 16px;">
                            <div style="display: flex; flex-direction: column; gap: 4px;">
                                <span style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted);">EVALUATIONS</span>
                                <span id="db2-scan-evals" class="mono" style="font-size: 16px; font-weight: 700; color: var(--text-primary);">0</span>
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 4px;">
                                <span style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted);">SIGNALS</span>
                                <span id="db2-scan-signals" class="mono" style="font-size: 16px; font-weight: 700; color: var(--accent-primary);">0</span>
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 4px;">
                                <span style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted);">QUALIFIED</span>
                                <span id="db2-scan-qual" class="mono" style="font-size: 16px; font-weight: 700; color: var(--profit-green);">0</span>
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 4px;">
                                <span style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted);">REJECTED</span>
                                <span id="db2-scan-rej" class="mono" style="font-size: 16px; font-weight: 700; color: var(--loss-red);">0</span>
                            </div>
                        </div>
                    </div>
                </section>

                <!-- LATEST OPEN TRADES -->
                <section class="dashboard-latest-trades">
                    <div class="panel-card table-card" style="height: auto; max-height: 300px;">
                        <div class="panel-card-header" style="height: 36px; min-height: 36px; padding: 0 16px;">
                            <div class="panel-card-title">LATEST OPEN TRADES</div>
                        </div>
                        <div class="panel-table-wrap">
                            <table class="terminal-table table-dense">
                                <thead>
                                    <tr>
                                        <th>SYMBOL</th>
                                        <th>TF</th>
                                        <th>SIDE</th>
                                        <th>ENTRY</th>
                                        <th>CURRENT</th>
                                        <th>U.PNL</th>
                                        <th>STATUS</th>
                                    </tr>
                                </thead>
                                <tbody id="db2-open-trades-body">
                                    <tr>
                                        <td colspan="7" class="idle-state-row">No open trades</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </section>
            </div>
"""

# Replace the existing view-dashboard
start_marker = '<div class="view-container active" id="view-dashboard">'
end_marker = '<div class="view-container"'

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    
    # We found the next view container
    if end_idx != -1:
        # Also need to grab any trailing comments before the next view container
        # We'll just replace everything between start_idx and the exact start of the next view
        # Actually, let's find the separator comment before the next view container
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx

        content = content[:start_idx] + new_dashboard_html + content[end_idx:]

        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Dashboard HTML replaced successfully.')
    else:
        print('Could not find next view container.')
else:
    print('Could not find start marker.')
