
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

system_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: SYSTEM
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-system">
                <div class="page-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <h1 class="page-title">SYSTEM</h1>
                    <button class="btn-terminal" onclick="fetchSystemData()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">REFRESH ↻</button>
                </div>

                <!-- ENGINE STATUS & MARKET DATA -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px;">
                    <div class="terminal-card">
                        <div class="card-title">ENGINE STATUS</div>
                        <div class="kv-row"><span>STATUS</span><span class="mono profit" id="sys-eng-status">● RUNNING</span></div>
                        <div class="kv-row"><span>UPTIME</span><span class="mono" id="sys-uptime">00:00:00</span></div>
                        <div class="kv-row"><span>PID</span><span class="mono" id="sys-pid">—</span></div>
                        <div class="kv-row"><span>RESTART COUNT</span><span class="mono">0</span></div>
                        <div class="kv-row"><span>HEARTBEAT</span><span class="mono" id="sys-hb">0.0s</span></div>
                        <div class="kv-row"><span>LAST EVALUATION</span><span class="mono text-secondary" id="sys-eval">—</span></div>
                        <div class="kv-row"><span>LAST CANDLE</span><span class="mono text-secondary" id="sys-candle">—</span></div>
                        <div class="kv-row"><span>SAFETY HALT</span><span class="mono text-muted">● INACTIVE</span></div>
                    </div>
                    <div class="terminal-card">
                        <div class="card-title">MARKET DATA</div>
                        <div class="kv-row"><span>ACTIVE SYMBOLS</span><span class="mono" id="sys-sym">0</span></div>
                        <div class="kv-row"><span>ACTIVE TIMEFRAMES</span><span class="mono" id="sys-tf">0</span></div>
                        <div class="kv-row"><span>ACTIVE STREAMS</span><span class="mono" id="sys-str">0</span></div>
                        <div class="kv-row"><span>LAST MARKET UPDATE</span><span class="mono text-secondary" id="sys-mkt">—</span></div>
                        <div class="kv-row"><span>LAST CANDLE CLOSE</span><span class="mono text-secondary" id="sys-candle2">—</span></div>
                        <div class="kv-row"><span>STALE STREAMS</span><span class="mono profit">0</span></div>
                    </div>
                </div>

                <!-- CONNECTIVITY & SUPERVISOR -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px;">
                    <div class="terminal-card">
                        <div class="card-title">CONNECTIVITY & LATENCY</div>
                        <div class="kv-row"><span>BINANCE REST</span><span class="mono profit" id="sys-rest">● CONNECTED</span></div>
                        <div class="kv-row"><span>REST LATENCY</span><span class="mono" id="sys-rest-lat">0 ms</span></div>
                        <div class="kv-row"><span>REST RECONNECTS</span><span class="mono">0</span></div>
                        <div class="kv-row" style="margin-top: 12px; border-top: 1px solid var(--border-medium); padding-top: 12px;"><span>WEBSOCKET</span><span class="mono profit" id="sys-ws">● CONNECTED</span></div>
                        <div class="kv-row"><span>WS LATENCY</span><span class="mono" id="sys-ws-lat">0 ms</span></div>
                        <div class="kv-row"><span>CONNECTION STATE</span><span class="mono profit" id="sys-conn-state">● STABLE</span></div>
                    </div>
                    <div class="terminal-card">
                        <div class="card-title">SUPERVISOR & PERSISTENCE</div>
                        <div class="kv-row"><span>SUPERVISOR STATUS</span><span class="mono profit">● RUNNING</span></div>
                        <div class="kv-row"><span>LAST HEALTH CHECK</span><span class="mono text-secondary" id="sys-hc">—</span></div>
                        <div class="kv-row"><span>RESTART HISTORY</span><span class="mono">0</span></div>
                        <div class="kv-row" style="margin-top: 12px; border-top: 1px solid var(--border-medium); padding-top: 12px;"><span>LEDGER</span><span class="mono profit">● HEALTHY</span></div>
                        <div class="kv-row"><span>PORTFOLIO STATE</span><span class="mono profit">● HEALTHY</span></div>
                        <div class="kv-row"><span>OPPORTUNITY LOG</span><span class="mono profit">● HEALTHY</span></div>
                        <div class="kv-row"><span>LAST SYNC</span><span class="mono text-secondary" id="sys-sync">—</span></div>
                    </div>
                </div>

                <!-- RECENT EVENTS -->
                <div class="panel-card" style="margin-bottom: 16px;">
                    <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                        RECENT SYSTEM EVENTS
                    </div>
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense" style="width: 100%;">
                            <tbody id="sys-events-body">
                                <tr><td class="text-center text-muted" style="padding: 24px;">No recent events</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- DIAGNOSTICS & DEPLOYMENT -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px;">
                    <div class="terminal-card">
                        <div class="card-title">DIAGNOSTICS</div>
                        <div class="kv-row"><span>RECENT ERRORS</span><span class="mono profit">0</span></div>
                        <div class="kv-row"><span>WARNINGS</span><span class="mono profit">0</span></div>
                        <div class="kv-row"><span>RECOVERY EVENTS</span><span class="mono text-muted">0</span></div>
                    </div>
                    <div class="terminal-card" style="background: var(--bg-panel); border: 1px solid var(--border-medium);">
                        <div class="card-title">DEPLOYMENT</div>
                        <div class="kv-row"><span>RENDER</span><span class="mono profit">● LIVE</span></div>
                        <div class="kv-row"><span>UPTIMEROBOT</span><span class="mono cyan">● MONITORING</span></div>
                        <div class="kv-row"><span>ENVIRONMENT</span><span class="mono text-secondary">TESTNET</span></div>
                        <div class="kv-row"><span>REGION</span><span class="mono text-secondary">FRANKFURT</span></div>
                        <div class="kv-row"><span>VERSION</span><span class="mono text-secondary">5.0</span></div>
                        <div class="kv-row"><span>COMMIT</span><span class="mono text-secondary">c726f13</span></div>
                        <div class="kv-row"><span>CONTAINER</span><span class="mono profit">● HEALTHY</span></div>
                    </div>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-system">'
end_marker = '<div class="view-container" id="view-settings">'

# Note: In previous step I put analytics above settings, system is already above settings but we should find system.
start_idx = content.find(start_marker)
if start_idx != -1:
    # Actually wait, view-settings is the last view. So end_marker can just be view-settings.
    end_idx = content.find(end_marker, start_idx + len(start_marker))
    if end_idx != -1:
        sep_marker = '<!-- ════'
        sep_idx = content.rfind(sep_marker, start_idx, end_idx)
        if sep_idx != -1:
            end_idx = sep_idx
            
        content = content[:start_idx] + system_html + content[end_idx:]
        
        with open('static/index.html', 'w', encoding='utf-8') as f:
            f.write(content)
        print('System HTML replaced successfully.')
    else:
        print('End marker not found.')
else:
    print('Start marker not found.')
