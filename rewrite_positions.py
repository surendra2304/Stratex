
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

positions_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: POSITIONS
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-positions">
                <div class="page-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <div class="page-title-wrap">
                        <h1 class="page-title">POSITIONS</h1>
                    </div>
                    <div class="scanner-toolbar" style="display: flex; gap: 12px; align-items: center;">
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">FILTERS ▼</button>
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">SEARCH 🔍</button>
                        <button class="btn-terminal" onclick="fetchPositionsV2()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">REFRESH ↻</button>
                    </div>
                </div>

                <!-- KPI ROW -->
                <section class="dashboard-hero-kpi-grid" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 16px;">
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 12px; letter-spacing: 0.8px;">OPEN POSITIONS</div>
                        <div class="kpi-val mono" id="pos2-open-count" style="font-size: 20px; font-weight: 700; color: var(--text-primary);">0</div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 12px; letter-spacing: 0.8px;">TOTAL POSITION VALUE</div>
                        <div class="kpi-val mono" id="pos2-total-val" style="font-size: 20px; font-weight: 700; color: var(--text-primary);">$0.00</div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 12px; letter-spacing: 0.8px;">UNREALIZED PNL</div>
                        <div class="kpi-val mono" id="pos2-upnl" style="font-size: 20px; font-weight: 700;">$0.00</div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div class="kpi-label" style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 12px; letter-spacing: 0.8px;">ACTIVE POSITIONS</div>
                        <div class="kpi-val mono" id="pos2-active-ratio" style="font-size: 20px; font-weight: 700; color: var(--text-primary);">0 / 5</div>
                    </div>
                </section>

                <!-- MAIN TABLE -->
                <div class="panel-card table-card" style="margin-bottom: 16px; min-height: 400px;">
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense">
                            <thead>
                                <tr>
                                    <th>SYMBOL</th>
                                    <th>TF</th>
                                    <th>SIDE</th>
                                    <th>ENTRY</th>
                                    <th>CURRENT</th>
                                    <th>QTY</th>
                                    <th>VALUE</th>
                                    <th>U.PNL</th>
                                    <th>STATUS</th>
                                </tr>
                            </thead>
                            <tbody id="pos2-body">
                                <tr>
                                    <td colspan="9" class="idle-state-row" style="padding: 48px; text-align: center;">
                                        <div style="font-family: var(--font-heading); font-size: 14px; font-weight: 700; color: var(--text-secondary); margin-bottom: 8px;">NO OPEN POSITIONS</div>
                                        <div style="font-size: 12px; color: var(--text-muted);">The bot currently has no open positions.</div>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-positions">'
end_marker = '<div class="view-container" id="view-trades">'

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    if end_idx != -1:
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx
            
        content = content[:start_idx] + positions_html + content[end_idx:]
        
        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Positions HTML replaced successfully.')
    else:
        print('End marker not found.')
else:
    print('Start marker not found.')
