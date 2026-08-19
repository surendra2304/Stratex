import sys
import re

with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

trades_js = """
// ==========================================
// TRADING JOURNAL V2
// ==========================================

let globalJournalTrades = [];

function toggleDayAccordion(dayId) {
    const el = document.getElementById(dayId);
    if (!el) return;
    if (el.style.display === 'none') {
        el.style.display = 'block';
    } else {
        el.style.display = 'none';
    }
}

fetchTrades = async function() {
    try {
        let trades = [];
        const res1 = await apiClient.get('/api/trades');
        const res2 = await apiClient.get('/api/trade-history');
        
        if (res1 && Array.isArray(res1)) trades = trades.concat(res1);
        if (res2 && res2.trades && Array.isArray(res2.trades)) trades = trades.concat(res2.trades);
        
        // Deduplicate
        const tMap = {};
        trades.forEach(t => {
            const tId = t.trade_id || t.order_id || t.timestamp;
            tMap[tId] = t;
        });
        trades = Object.values(tMap);
        
        // ONLY CLOSED TRADES
        globalJournalTrades = trades.filter(t => {
            const isClosed = t.status === 'CLOSED' || (t.exit_price && Number(t.exit_price) > 0);
            return isClosed;
        });

        // Sort by exit timestamp descending
        globalJournalTrades.sort((a, b) => {
            const timeA = new Date(a.exit_timestamp || a.timestamp || 0).getTime();
            const timeB = new Date(b.exit_timestamp || b.timestamp || 0).getTime();
            return timeB - timeA;
        });

        renderTradingJournalV2();

    } catch (e) {
        console.error("fetchTrades V2 error:", e);
    }
};

function renderTradingJournalV2() {
    let tWins = 0, tLosses = 0, tProfit = 0, tLoss = 0;
    
    // Group by Day
    const dayGroups = {};
    
    globalJournalTrades.forEach(t => {
        const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
        if (net >= 0) { tWins++; tProfit += net; }
        else { tLosses++; tLoss += Math.abs(net); }
        
        const exitMs = new Date(t.exit_timestamp || t.timestamp || Date.now()).getTime();
        const exitDateObj = new Date(exitMs);
        const dayKey = exitDateObj.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' }).toUpperCase();
        
        if (!dayGroups[dayKey]) dayGroups[dayKey] = [];
        dayGroups[dayKey].push(t);
    });
    
    const tTotal = tWins + tLosses;
    const wr = tTotal > 0 ? (tWins / tTotal) * 100 : 0;
    const tNet = tProfit - tLoss;
    
    document.getElementById('trd-total').innerText = tTotal;
    document.getElementById('trd-wins').innerText = tWins;
    document.getElementById('trd-losses').innerText = tLosses;
    document.getElementById('trd-wr').innerText = wr.toFixed(1) + '%';
    document.getElementById('trd-tprof').innerText = '+' + formatCurrency(tProfit);
    document.getElementById('trd-tloss').innerText = '-' + formatCurrency(tLoss);
    
    const elNet = document.getElementById('trd-net');
    elNet.innerText = (tNet >= 0 ? '+' : '') + formatCurrency(tNet);
    elNet.className = 'mono ' + (tNet >= 0 ? 'profit' : 'loss');

    const container = document.getElementById('journal-accordion-container');
    if (!container) return;
    
    if (tTotal === 0) {
        container.innerHTML = '<div class="panel-card" style="padding: 24px; text-align: center; color: var(--text-muted);">No closed trades found.</div>';
        return;
    }
    
    let html = '';
    const dayKeys = Object.keys(dayGroups);
    // Already sorted globally, so days appear in correct order
    
    dayKeys.forEach((day, index) => {
        const dId = 'day-' + index;
        html += `
            <div class="panel-card" style="margin-bottom: 8px;">
                <div class="panel-card-header" onclick="toggleDayAccordion('${dId}')" style="cursor: pointer; padding: 12px 16px; background: var(--bg-panel); display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 14px;">📅</span>
                    <span style="font-family: var(--font-heading); font-size: 13px; font-weight: 700; color: var(--text-primary);">${day}</span>
                </div>
                <div id="${dId}" style="display: none; border-top: 1px solid var(--border-medium);">
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense">
                            <thead>
                                <tr>
                                    <th>OPENED - CLOSED (HOLDING)</th>
                                    <th>SYMBOL · TF · SIDE</th>
                                    <th>ENTRY</th>
                                    <th>EXIT</th>
                                    <th>NET PNL</th>
                                    <th>CLOSE REASON</th>
                                </tr>
                            </thead>
                            <tbody>
        `;
        
        dayGroups[day].forEach(t => {
            const openTime = new Date(t.entry_timestamp || t.timestamp || 0).toLocaleTimeString('en-US', { hour12: false });
            const closeTime = new Date(t.exit_timestamp || t.timestamp || 0).toLocaleTimeString('en-US', { hour12: false });
            const dur = calcDurationBetween(t.entry_timestamp || t.timestamp, t.exit_timestamp || t.timestamp);
            
            const sym = t.symbol || '-';
            const tf = t.timeframe || '-';
            const side = t.side || 'LONG';
            
            const entry = Number(t.entry_price || t.price || 0);
            const exit = Number(t.exit_price || 0);
            const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
            const reason = t.exit_reason || t.close_reason || (net >= 0 ? 'TAKE PROFIT' : 'STOP LOSS');
            
            const netStr = (net >= 0 ? '+' : '') + formatCurrency(net);
            const netClass = net >= 0 ? 'profit' : 'loss';
            const sideClass = (side === 'LONG' || side === 'BUY') ? 'profit' : 'loss';
            
            const tJson = JSON.stringify(t).replace(/'/g, "&#39;").replace(/"/g, "&quot;");
            
            html += `
                <tr onclick="inspectTradeLifecycleV2(${tJson})" style="cursor:pointer">
                    <td class="mono text-secondary">${openTime} - ${closeTime} (${dur})</td>
                    <td><strong class="text-primary">${sym}</strong> <span class="text-muted">·</span> <span class="mono">${tf}</span> <span class="text-muted">·</span> <span class="${sideClass}">${side}</span></td>
                    <td class="mono">${entry > 0 ? entry.toFixed(4) : '—'}</td>
                    <td class="mono">${exit > 0 ? exit.toFixed(4) : '—'}</td>
                    <td class="mono ${netClass}">${netStr}</td>
                    <td class="mono text-secondary">${reason}</td>
                </tr>
            `;
        });
        
        html += `
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

inspectTradeLifecycle = function(t) {
    inspectTradeLifecycleV2(t);
}

function inspectTradeLifecycleV2(t) {
    const sym = t.symbol || '-';
    const tf = t.timeframe || '-';
    const strat = t.strategy || '-';
    const side = t.side || 'LONG';
    
    const entry = Number(t.entry_price || t.price || 0);
    const exit = Number(t.exit_price || 0);
    const qty = Math.abs(Number(t.quantity || t.positionAmt || t.origQty || 0));
    const val = qty * entry;
    
    const sl = Number(t.stop_loss || t.sl_price || 0);
    const tp = Number(t.take_profit || t.tp_price || 0);
    
    const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
    const gross = Number(t.gross_pnl !== undefined ? t.gross_pnl : (net + Number(t.fees || 0)));
    const fees = Number(t.fees || 0);
    
    const time = t.entry_timestamp || t.timestamp || Date.now();
    const entryTimeStr = new Date(time).toLocaleTimeString('en-US', { hour12: false });
    const closeTimeStr = new Date(t.exit_timestamp || t.timestamp || Date.now()).toLocaleTimeString('en-US', { hour12: false });
    const durStr = calcDurationBetween(time, t.exit_timestamp || t.timestamp || Date.now());
    const reason = t.exit_reason || t.close_reason || (net >= 0 ? 'TAKE PROFIT' : 'STOP LOSS');

    const balEntry = t.cash_before_entry ? formatCurrency(t.cash_before_entry) : '—';
    const balExit = t.cash_after_exit ? formatCurrency(t.cash_after_exit) : '—';
    const eqEntry = t.equity_before_entry ? formatCurrency(t.equity_before_entry) : '—';
    const eqExit = t.equity_after_exit ? formatCurrency(t.equity_after_exit) : '—';

    // Update Drawer Title
    document.getElementById('drawer-title').innerText = 'TRADE DETAILS';
    document.querySelector('.drawer-title-wrap .badge-indigo').innerText = 'CLOSED';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.background = 'rgba(255, 255, 255, 0.1)';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.color = 'var(--text-primary)';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.borderColor = 'var(--border-medium)';

    let html = `
        <div style="font-family: var(--font-heading); font-size: 14px; font-weight: 700; color: var(--text-primary); margin-bottom: 4px;">
            ${sym} · ${tf} · ${strat} · <span class="${side === 'LONG' || side === 'BUY' ? 'profit' : 'loss'}">${side}</span>
        </div>
        <div class="mono text-secondary" style="font-size: 11px; margin-bottom: 24px;">Closed Trade</div>

        <!-- TRADE SUMMARY -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">TRADE SUMMARY</div>
            <div class="kv-row"><span>Trade ID</span><span class="mono">${t.trade_id || t.order_id || '—'}</span></div>
            <div class="kv-row"><span>Entry</span><span class="mono">${entry > 0 ? entry.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Exit</span><span class="mono">${exit > 0 ? exit.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Quantity</span><span class="mono">${qty > 0 ? qty : '—'}</span></div>
            <div class="kv-row"><span>Position Value</span><span class="mono">${val > 0 ? formatCurrency(val) : '—'}</span></div>
            <div class="kv-row"><span>Stop Loss</span><span class="mono">${sl > 0 ? sl.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Take Profit</span><span class="mono">${tp > 0 ? tp.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Close Reason</span><span class="mono">${reason}</span></div>
            <div class="kv-row"><span>Holding Time</span><span class="mono">${durStr}</span></div>
        </div>

        <!-- TRADE NUMBERS -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">TRADE NUMBERS</div>
            <div class="kv-row"><span>Risk %</span><span class="mono">${t.risk_percent ? t.risk_percent.toFixed(2) + '%' : '—'}</span></div>
            <div class="kv-row"><span>Risk Value</span><span class="mono">${t.risk_value ? formatCurrency(t.risk_value) : '—'}</span></div>
        </div>

        <!-- ACCOUNT STATE -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">ACCOUNT STATE</div>
            <div class="kv-row"><span>Balance Before Entry</span><span class="mono">${balEntry}</span></div>
            <div class="kv-row"><span>Balance After Close</span><span class="mono">${balExit}</span></div>
            <div class="kv-row"><span>Equity Before Entry</span><span class="mono">${eqEntry}</span></div>
            <div class="kv-row"><span>Equity After Close</span><span class="mono">${eqExit}</span></div>
        </div>

        <!-- PERFORMANCE -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">PERFORMANCE</div>
            <div class="kv-row"><span>Profit/Loss</span><span class="mono ${gross >= 0 ? 'profit' : 'loss'}">${(gross >= 0 ? '+' : '') + formatCurrency(gross)}</span></div>
            <div class="kv-row"><span>Fees</span><span class="mono loss">-${formatCurrency(fees)}</span></div>
            <div class="kv-row"><span>Net PnL</span><span class="mono ${net >= 0 ? 'profit' : 'loss'}">${(net >= 0 ? '+' : '') + formatCurrency(net)}</span></div>
            <div class="kv-row"><span>Return</span><span class="mono ${net >= 0 ? 'profit' : 'loss'}">${val > 0 ? ((net / val) * 100).toFixed(2) + '%' : '—'}</span></div>
        </div>

        <!-- TRADE LIFECYCLE -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">TRADE LIFECYCLE</div>
            <div class="kv-row"><span>Signal Generated</span><span class="mono text-secondary">${t.signal_timestamp ? new Date(t.signal_timestamp).toLocaleTimeString('en-US', {hour12:false}) : entryTimeStr}</span></div>
            <div class="kv-row"><span>Position Opened</span><span class="mono text-secondary">${entryTimeStr}</span></div>
            <div class="kv-row"><span>Position Closed</span><span class="mono text-secondary">${closeTimeStr}</span></div>
        </div>

        <!-- EXECUTION -->
        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">EXECUTION</div>
            <div class="kv-row"><span>Entry Order ID</span><span class="mono">${t.entry_order_id || t.order_id || '—'}</span></div>
            <div class="kv-row"><span>Exit Order ID</span><span class="mono">${t.exit_order_id || '—'}</span></div>
            <div class="kv-row"><span>Filled Quantity</span><span class="mono">${qty > 0 ? qty : '—'}</span></div>
            <div class="kv-row"><span>Average Entry</span><span class="mono">${entry > 0 ? entry.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Average Exit</span><span class="mono">${exit > 0 ? exit.toFixed(4) : '—'}</span></div>
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
        if (entry > 0 && exit > 0) {
            pointData = [entry*0.99, entry, exit, exit*1.01]; // simulated trajectory
        } else {
            pointData = [entry];
        }

        window.modalChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['Pre', 'Entry', 'Exit', 'Post'],
                datasets: [{
                    label: 'Price',
                    data: pointData,
                    borderColor: net >= 0 ? '#10B981' : '#EF4444',
                    backgroundColor: net >= 0 ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
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

if 'fetchTrades = async function' not in content:
    content += trades_js

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Trades JS updated.")
