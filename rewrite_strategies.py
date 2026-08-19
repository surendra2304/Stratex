import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

strategies_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: STRATEGIES
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-strategies">
                <div class="page-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <div class="page-title-wrap">
                        <h1 class="page-title">STRATEGIES</h1>
                    </div>
                    <div class="scanner-toolbar" style="display: flex; gap: 12px; align-items: center;">
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">FILTERS ▼</button>
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">SEARCH 🔍</button>
                        <button class="btn-terminal" onclick="fetchStrategiesV2()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">REFRESH ↻</button>
                    </div>
                </div>

                <!-- MAIN TABLE -->
                <div class="panel-card table-card" style="margin-bottom: 16px; min-height: 400px;">
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense">
                            <thead>
                                <tr>
                                    <th>STRATEGY</th>
                                    <th>STATUS</th>
                                    <th>TIMEFRAMES</th>
                                    <th>EVALUATIONS</th>
                                    <th>SIGNALS</th>
                                    <th>TRADES</th>
                                    <th>WIN RATE</th>
                                </tr>
                            </thead>
                            <tbody id="strat2-body">
                                <tr><td colspan="7" class="idle-state-row" style="padding: 48px; text-align: center;">Loading strategy configuration...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-strategies">'
end_marker = '<div class="view-container" id="view-risk">'

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    if end_idx != -1:
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx
            
        content = content[:start_idx] + strategies_html + content[end_idx:]
        
        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Strategies HTML replaced successfully.')
    else:
        print('End marker not found.')
else:
    print('Start marker not found.')
