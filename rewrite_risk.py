import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

risk_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: RISK
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-risk">
                <div class="page-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <div class="page-title-wrap">
                        <h1 class="page-title">RISK CONTROL</h1>
                    </div>
                    <div class="scanner-toolbar" style="display: flex; gap: 12px; align-items: center;">
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">FILTERS ▼</button>
                        <button class="btn-terminal" onclick="fetchRiskData()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">REFRESH ↻</button>
                    </div>
                </div>

                <!-- KPI ROW -->
                <div class="kpi-grid" style="grid-template-columns: repeat(4, 1fr); margin-bottom: 16px; gap: 16px; display: grid;">
                    <div class="kpi-card" style="padding: 16px; border: 1px solid var(--border-medium); background: var(--bg-panel);">
                        <div class="kpi-title" style="font-size: 10px; font-family: var(--font-heading); color: var(--text-muted); margin-bottom: 8px; letter-spacing: 0.5px;">TOTAL EQUITY</div>
                        <div class="kpi-value mono" id="r-eq" style="font-size: 20px; color: var(--text-primary);">$0.00</div>
                    </div>
                    <div class="kpi-card" style="padding: 16px; border: 1px solid var(--border-medium); background: var(--bg-panel);">
                        <div class="kpi-title" style="font-size: 10px; font-family: var(--font-heading); color: var(--text-muted); margin-bottom: 8px; letter-spacing: 0.5px;">CURRENT EXPOSURE</div>
                        <div class="kpi-value mono" id="r-exp" style="font-size: 20px; color: var(--text-primary);">$0.00</div>
                    </div>
                    <div class="kpi-card" style="padding: 16px; border: 1px solid var(--border-medium); background: var(--bg-panel);">
                        <div class="kpi-title" style="font-size: 10px; font-family: var(--font-heading); color: var(--text-muted); margin-bottom: 8px; letter-spacing: 0.5px;">RISK USED</div>
                        <div class="kpi-value mono" id="r-used" style="font-size: 20px; color: var(--text-primary);">0.00%</div>
                    </div>
                    <div class="kpi-card" style="padding: 16px; border: 1px solid var(--border-medium); background: var(--bg-panel);">
                        <div class="kpi-title" style="font-size: 10px; font-family: var(--font-heading); color: var(--text-muted); margin-bottom: 8px; letter-spacing: 0.5px;">OPEN POSITIONS</div>
                        <div class="kpi-value mono" id="r-pos" style="font-size: 20px; color: var(--text-primary);">0 / 5</div>
                    </div>
                </div>

                <!-- RISK LIMITS -->
                <div class="panel-card" style="margin-bottom: 16px;">
                    <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                        RISK LIMITS
                    </div>
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th style="width: 20%;">PORTFOLIO EXPOSURE</th>
                                    <th style="width: 20%;">RISK PER TRADE</th>
                                    <th style="width: 20%;">DAILY LOSS</th>
                                    <th style="width: 20%;">MAX DRAWDOWN</th>
                                    <th style="width: 20%;">MAX OPEN POSITIONS</th>
                                </tr>
                            </thead>
                            <tbody id="risk-limits-body">
                                <tr><td colspan="5" class="idle-state-row text-center" style="padding: 24px;">Loading risk parameters...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- CURRENT EXPOSURE -->
                <div class="panel-card" style="margin-bottom: 16px;">
                    <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                        CURRENT EXPOSURE
                    </div>
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>SYMBOL</th>
                                    <th>POSITION VALUE</th>
                                    <th>% OF EQUITY</th>
                                    <th>RISK</th>
                                    <th>STATUS</th>
                                </tr>
                            </thead>
                            <tbody id="risk-exp-body">
                                <tr><td colspan="5" class="idle-state-row text-center" style="padding: 24px;">Loading active exposures...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- RISK DECISIONS -->
                <div class="panel-card" style="margin-bottom: 16px;">
                    <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                        RISK DECISIONS
                    </div>
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>TIME</th>
                                    <th>SYMBOL</th>
                                    <th>TF</th>
                                    <th>REQUESTED RISK</th>
                                    <th>AVAILABLE RISK</th>
                                    <th>RESULT</th>
                                    <th>REASON</th>
                                </tr>
                            </thead>
                            <tbody id="risk-dec-body">
                                <tr><td colspan="7" class="idle-state-row text-center" style="padding: 24px;">Loading engine decisions...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- PROTECTION STATUS -->
                <div class="panel-card" style="margin-bottom: 16px;">
                    <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                        PROTECTION STATUS
                    </div>
                    <div style="padding: 24px; display: grid; grid-template-columns: 1fr 1fr; gap: 32px; background: var(--bg-panel);">
                        <div style="display: flex; flex-direction: column; gap: 16px;">
                            <div style="display: flex; justify-content: space-between;"><span class="mono text-secondary" style="font-size: 12px;">DAILY LOSS LIMIT</span><span class="mono profit">● HEALTHY</span></div>
                            <div style="display: flex; justify-content: space-between;"><span class="mono text-secondary" style="font-size: 12px;">EXPOSURE LIMIT</span><span class="mono profit">● HEALTHY</span></div>
                            <div style="display: flex; justify-content: space-between;"><span class="mono text-secondary" style="font-size: 12px;">STOP LOSS</span><span class="mono cyan">● ACTIVE</span></div>
                            <div style="display: flex; justify-content: space-between;"><span class="mono text-secondary" style="font-size: 12px;">DUPLICATE PROTECTION</span><span class="mono cyan">● ACTIVE</span></div>
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 16px;">
                            <div style="display: flex; justify-content: space-between;"><span class="mono text-secondary" style="font-size: 12px;">MAX DRAWDOWN</span><span class="mono profit">● HEALTHY</span></div>
                            <div style="display: flex; justify-content: space-between;"><span class="mono text-secondary" style="font-size: 12px;">POSITION LIMIT</span><span class="mono profit">● HEALTHY</span></div>
                            <div style="display: flex; justify-content: space-between;"><span class="mono text-secondary" style="font-size: 12px;">TAKE PROFIT</span><span class="mono cyan">● ACTIVE</span></div>
                            <div style="display: flex; justify-content: space-between;"><span class="mono text-secondary" style="font-size: 12px;">SAFETY HALT</span><span class="mono text-muted">● INACTIVE</span></div>
                        </div>
                    </div>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-risk">'
end_marker = '<div class="view-container" id="view-system">'

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    if end_idx != -1:
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx
            
        content = content[:start_idx] + risk_html + content[end_idx:]
        
        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Risk HTML replaced successfully.')
    else:
        print('End marker not found.')
else:
    print('Start marker not found.')
