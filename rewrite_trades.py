import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

trades_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: TRADES (TRADING JOURNAL)
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-trades">
                <div class="page-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <div class="page-title-wrap">
                        <h1 class="page-title">TRADING JOURNAL</h1>
                    </div>
                    <div class="scanner-toolbar" style="display: flex; gap: 12px; align-items: center;">
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">SEARCH 🔍</button>
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">FILTERS ▼</button>
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">DATE RANGE</button>
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">EXPORT</button>
                        <button class="btn-terminal" onclick="fetchTrades()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">↻</button>
                    </div>
                </div>

                <!-- TOP KPI SUMMARY -->
                <div class="panel-card table-card" style="margin-bottom: 16px;">
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense">
                            <thead>
                                <tr>
                                    <th>TOTAL TRADES</th>
                                    <th>WINS</th>
                                    <th>LOSSES</th>
                                    <th>WIN RATE</th>
                                    <th>TOTAL PROFIT</th>
                                    <th>TOTAL LOSS</th>
                                    <th>NET PNL</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td class="mono" id="trd-total" style="font-size: 14px; font-weight: 700; color: var(--text-primary);">0</td>
                                    <td class="mono profit" id="trd-wins" style="font-size: 14px; font-weight: 700;">0</td>
                                    <td class="mono loss" id="trd-losses" style="font-size: 14px; font-weight: 700;">0</td>
                                    <td class="mono" id="trd-wr" style="font-size: 14px; font-weight: 700; color: var(--text-primary);">0.0%</td>
                                    <td class="mono profit" id="trd-tprof" style="font-size: 14px; font-weight: 700;">+$0.00</td>
                                    <td class="mono loss" id="trd-tloss" style="font-size: 14px; font-weight: 700;">-$0.00</td>
                                    <td class="mono" id="trd-net" style="font-size: 14px; font-weight: 700;">+$0.00</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- DAY ACCORDION -->
                <div id="journal-accordion-container" style="display: flex; flex-direction: column; gap: 8px;">
                    <div class="panel-card" style="padding: 24px; text-align: center; color: var(--text-muted);">
                        Loading closed trades...
                    </div>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-trades">'
end_marker = '<div class="view-container" id="view-analytics">'

start_idx = content.find(start_marker)
if start_idx != -1:
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    if end_idx != -1:
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx
            
        content = content[:start_idx] + trades_html + content[end_idx:]
        
        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Trades HTML replaced successfully.')
    else:
        print('End marker not found.')
else:
    print('Start marker not found.')
