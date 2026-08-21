
with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

positions_js = """
// ==========================================
// POSITIONS LOGIC V2
// ==========================================

async function fetchPositionsV2() {
    try {
        const data = await apiClient.get('/api/positions');
        if (!data) return;

        let openCount = 0;
        let totalValue = 0;
        let totalUpnl = 0;
        let html = '';

        if (Array.isArray(data) && data.length > 0) {
            openCount = data.length;
            
            data.forEach(p => {
                const sym = p.symbol || '-';
                const tf = p.timeframe || '-';
                const side = p.side || 'LONG';
                const entry = Number(p.entry_price || p.price || 0);
                const mark = Number(p.mark_price || p.current_price || entry);
                const qty = Number(p.quantity || p.positionAmt || 0);
                const val = Math.abs(qty * mark);
                const upnl = Number(p.unrealized_pnl || 0);

                totalValue += val;
                totalUpnl += upnl;

                const sideClass = (side === 'LONG' || side === 'BUY') ? 'profit' : 'loss';
                const uStr = (upnl >= 0 ? '+' : '') + formatCurrency(upnl);
                const uClass = upnl >= 0 ? 'profit' : 'loss';
                
                const pJson = JSON.stringify(p).replace(/'/g, "&#39;").replace(/"/g, "&quot;");

                html += `
                    <tr onclick="inspectPosition(${pJson})" style="cursor:pointer">
                        <td class="td-strong">${sym}</td>
                        <td class="mono">${tf}</td>
                        <td class="${sideClass}">${side}</td>
                        <td class="mono">${entry > 0 ? entry.toFixed(4) : '—'}</td>
                        <td class="mono">${mark > 0 ? mark.toFixed(4) : '—'}</td>
                        <td class="mono">${Math.abs(qty)}</td>
                        <td class="mono">${formatCurrency(val)}</td>
                        <td class="mono ${uClass}">${uStr}</td>
                        <td class="mono cyan">OPEN</td>
                    </tr>
                `;
            });
            document.getElementById('pos2-body').innerHTML = html;
        } else {
            document.getElementById('pos2-body').innerHTML = `
                <tr>
                    <td colspan="9" class="idle-state-row" style="padding: 48px; text-align: center;">
                        <div style="font-family: var(--font-heading); font-size: 14px; font-weight: 700; color: var(--text-secondary); margin-bottom: 8px;">NO OPEN POSITIONS</div>
                        <div style="font-size: 12px; color: var(--text-muted);">The bot currently has no open positions.</div>
                    </td>
                </tr>
            `;
        }

        document.getElementById('pos2-open-count').innerText = openCount;
        document.getElementById('pos2-total-val').innerText = formatCurrency(totalValue);
        document.getElementById('pos2-upnl').innerText = (totalUpnl >= 0 ? '+' : '') + formatCurrency(totalUpnl);
        document.getElementById('pos2-upnl').className = 'kpi-val mono ' + (totalUpnl >= 0 ? 'profit' : 'loss');
        document.getElementById('pos2-active-ratio').innerText = `${openCount} / 5`;

    } catch (e) {
        console.error("fetchPositionsV2 error:", e);
    }
}

function inspectPosition(p) {
    const sym = p.symbol || '-';
    const tf = p.timeframe || '-';
    const strat = p.strategy || '-';
    const side = p.side || 'LONG';
    
    const entry = Number(p.entry_price || p.price || 0);
    const mark = Number(p.mark_price || p.current_price || entry);
    const qty = Math.abs(Number(p.quantity || p.positionAmt || 0));
    const val = qty * mark;
    
    const sl = Number(p.stop_loss || p.sl_price || 0);
    const tp = Number(p.take_profit || p.tp_price || 0);
    
    const upnl = Number(p.unrealized_pnl || 0);
    const retPct = entry > 0 ? (upnl / (qty * entry)) * 100 : 0;
    
    const time = p.entry_timestamp || p.timestamp || Date.now();
    const entryTimeStr = new Date(time).toLocaleTimeString('en-US', { hour12: false });
    const lastUpdStr = new Date(p.last_update || Date.now()).toLocaleTimeString('en-US', { hour12: false });
    const durStr = calcDurationBetween(time, Date.now());

    // Update Drawer Title
    document.getElementById('drawer-title').innerText = 'POSITION DETAILS';
    document.querySelector('.drawer-title-wrap .badge-indigo').innerText = 'OPEN';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.background = 'rgba(34, 211, 238, 0.1)';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.color = 'var(--text-primary)';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.borderColor = 'var(--accent-primary)';

    let html = `
        <div style="font-family: var(--font-heading); font-size: 14px; font-weight: 700; color: var(--text-primary); margin-bottom: 4px;">
            ${sym} · ${tf} · ${strat} · <span class="${side === 'LONG' || side === 'BUY' ? 'profit' : 'loss'}">${side}</span>
        </div>
        <div class="mono text-secondary" style="font-size: 11px; margin-bottom: 24px;">Active Trade</div>

        <!-- POSITION SUMMARY -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">POSITION SUMMARY</div>
            <div class="kv-row"><span>Entry Price</span><span class="mono">${entry > 0 ? entry.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Current Price</span><span class="mono">${mark > 0 ? mark.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Quantity</span><span class="mono">${qty > 0 ? qty : '—'}</span></div>
            <div class="kv-row"><span>Position Value</span><span class="mono">${val > 0 ? formatCurrency(val) : '—'}</span></div>
            <div class="kv-row"><span>Unrealized PnL</span><span class="mono ${upnl >= 0 ? 'profit' : 'loss'}">${(upnl >= 0 ? '+' : '') + formatCurrency(upnl)}</span></div>
            <div class="kv-row"><span>Return %</span><span class="mono ${retPct >= 0 ? 'profit' : 'loss'}">${(retPct >= 0 ? '+' : '') + retPct.toFixed(2)}%</span></div>
            <div class="kv-row"><span>Position ID</span><span class="mono">${p.position_id || p.order_id || '—'}</span></div>
        </div>

        <!-- POSITION STATE -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">POSITION STATE</div>
            <div class="kv-row"><span>Entry Time</span><span class="mono">${entryTimeStr}</span></div>
            <div class="kv-row"><span>Last Update</span><span class="mono">${lastUpdStr}</span></div>
            <div class="kv-row"><span>Duration</span><span class="mono">${durStr}</span></div>
        </div>

        <!-- ACCOUNT STATE -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">ACCOUNT STATE</div>
            <div class="kv-row"><span>Entry Balance</span><span class="mono">${p.entry_balance ? formatCurrency(p.entry_balance) : '—'}</span></div>
            <div class="kv-row"><span>Current Balance</span><span class="mono">${p.current_balance ? formatCurrency(p.current_balance) : '—'}</span></div>
        </div>

        <!-- PROTECTION -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">PROTECTION</div>
            <div class="kv-row"><span>Status</span><span class="mono ${sl > 0 || tp > 0 ? 'profit' : 'loss'}">${sl > 0 || tp > 0 ? 'ACTIVE' : 'NONE'}</span></div>
            <div class="kv-row"><span>Stop Loss</span><span class="mono">${sl > 0 ? sl.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Take Profit</span><span class="mono">${tp > 0 ? tp.toFixed(4) : '—'}</span></div>
        </div>

        <!-- EXECUTION -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">EXECUTION</div>
            <div class="kv-row"><span>Entry Order ID</span><span class="mono">${p.order_id || '—'}</span></div>
            <div class="kv-row"><span>Order Type</span><span class="mono">${p.order_type || 'MARKET'}</span></div>
            <div class="kv-row"><span>Filled Qty</span><span class="mono">${qty > 0 ? qty : '—'}</span></div>
            <div class="kv-row"><span>Average Entry</span><span class="mono">${entry > 0 ? entry.toFixed(4) : '—'}</span></div>
        </div>
    `;

    document.getElementById('drawer-body').innerHTML = html;
    
    document.getElementById('drawer-backdrop').style.display = 'block';
    document.getElementById('inspector-drawer').style.display = 'flex';

    // Chart
    const chartContainer = document.getElementById('modal-trade-chart');
    if (chartContainer) {
        let ctx = chartContainer.getContext('2d');
        if (window.modalChartInstance) { window.modalChartInstance.destroy(); }
        
        let pointData = [];
        if (entry > 0 && mark > 0) {
            pointData = [entry, (entry + mark)/2, mark];
        } else {
            pointData = [entry];
        }

        window.modalChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['Entry', 'Active', 'Current'],
                datasets: [{
                    label: 'Price',
                    data: pointData,
                    borderColor: '#3B82F6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { 
                    legend: { display: false },
                    annotation: {
                        annotations: {
                            lineSL: {
                                type: 'line',
                                yMin: sl,
                                yMax: sl,
                                borderColor: 'rgba(239, 68, 68, 0.5)',
                                borderWidth: 1,
                                borderDash: [5, 5],
                                display: sl > 0
                            },
                            lineTP: {
                                type: 'line',
                                yMin: tp,
                                yMax: tp,
                                borderColor: 'rgba(16, 185, 129, 0.5)',
                                borderWidth: 1,
                                borderDash: [5, 5],
                                display: tp > 0
                            }
                        }
                    }
                },
                scales: {
                    x: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } },
                    y: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } }
                }
            }
        });
    }
}
"""

if 'fetchPositionsV2' not in content:
    content += positions_js

content = content.replace('fetchDashboardData(), fetchDashboardDataV2(), fetchScannerDataV2(),', 'fetchDashboardData(), fetchDashboardDataV2(), fetchScannerDataV2(), fetchPositionsV2(),')

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Positions JS updated.")
