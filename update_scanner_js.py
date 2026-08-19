import sys

with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

scanner_js = """
// ==========================================
// SCANNER LOGIC V2
// ==========================================

let globalScannerData = [];
let isScannerPaused = false;

function toggleScannerPause() {
    isScannerPaused = !isScannerPaused;
    const btn = document.getElementById('btn-scan-pause');
    if (btn) {
        if (isScannerPaused) {
            btn.innerHTML = 'RESUME ▶';
            btn.style.color = 'var(--accent-primary)';
            btn.style.borderColor = 'var(--accent-primary)';
        } else {
            btn.innerHTML = 'PAUSE ⏸';
            btn.style.color = 'var(--text-secondary)';
            btn.style.borderColor = 'var(--border-medium)';
            fetchScannerDataV2();
        }
    }
}

async function fetchScannerDataV2() {
    if (isScannerPaused) return;
    try {
        const data = await apiClient.get('/api/scanner');
        if (!data) return;

        // KPI
        document.getElementById('scan2-evals').innerText = data.evaluations || 0;
        document.getElementById('scan2-signals').innerText = data.signals || 0;
        document.getElementById('scan2-qual').innerText = data.qualified || 0;
        document.getElementById('scan2-rej').innerText = data.rejected || 0;

        // Live Signals
        if (data.recent_signals && Array.isArray(data.recent_signals)) {
            globalScannerData = data.recent_signals;
            renderScannerTable();
        }
        
        // Active metrics
        if (data.active_symbols) document.getElementById('scan2-act-sym').innerText = data.active_symbols;
        if (data.active_timeframes) document.getElementById('scan2-act-tf').innerText = data.active_timeframes;
        if (data.active_strategies) document.getElementById('scan2-act-strat').innerText = data.active_strategies;
        
        // Live Footer
        const footer = document.getElementById('scan2-live-status');
        if (footer) {
            const syms = data.active_symbol_list || ['BTCUSDT', 'ETHUSDT', 'LINKUSDT'];
            footer.innerHTML = syms.map(s => `<span style="color: var(--profit-green);">${s} ● SCANNING</span>`).join('');
        }

    } catch (e) {
        console.error("fetchScannerDataV2 error:", e);
    }
}

function applyScannerFilters() {
    document.getElementById('scanner-filter-dropdown').classList.remove('show');
    renderScannerTable();
}

function renderScannerTable() {
    const symF = document.getElementById('sf-symbol')?.value || 'ALL';
    const tfF = document.getElementById('sf-tf')?.value || 'ALL';
    const sideF = document.getElementById('sf-side')?.value || 'ALL';
    const resF = document.getElementById('sf-result')?.value || 'ALL';
    const stratF = document.getElementById('sf-strategy')?.value || 'ALL';

    const filtered = globalScannerData.filter(s => {
        if (symF !== 'ALL' && s.symbol !== symF) return false;
        if (tfF !== 'ALL' && s.timeframe !== tfF) return false;
        if (sideF !== 'ALL' && s.side !== sideF) return false;
        if (resF !== 'ALL') {
            const isQual = s.evaluation && s.evaluation.profitability && s.evaluation.profitability.passed && s.evaluation.risk && s.evaluation.risk.passed;
            const resStatus = isQual ? 'QUALIFIED' : 'REJECTED';
            if (resStatus !== resF) return false;
        }
        if (stratF !== 'ALL' && s.strategy !== stratF) return false;
        return true;
    });

    document.getElementById('scan2-cand').innerText = filtered.length;

    const tbody = document.getElementById('scan2-body');
    if (!tbody) return;

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="idle-state-row">No signals match the active filters.</td></tr>';
        return;
    }

    let html = '';
    filtered.forEach(s => {
        const time = new Date(s.timestamp || Date.now()).toLocaleTimeString('en-US', { hour12: false });
        const sym = s.symbol || '-';
        const tf = s.timeframe || '-';
        const side = s.side || 'LONG';
        const entry = Number(s.entry_price || s.price || 0);
        
        let edgeVal = 0;
        let isQual = false;
        let reason = '—';
        
        if (s.evaluation) {
            edgeVal = Number(s.evaluation.expected_net_percent || 0);
            const prof = s.evaluation.profitability || {};
            const risk = s.evaluation.risk || {};
            isQual = prof.passed && risk.passed;
            if (!isQual) reason = prof.reason || risk.reason || 'REJECTED';
        }
        
        const sideClass = (side === 'LONG' || side === 'BUY') ? 'profit' : 'loss';
        const edgeStr = (edgeVal >= 0 ? '+' : '') + edgeVal.toFixed(2) + '%';
        const edgeClass = edgeVal >= 0 ? 'profit' : 'loss';
        const resStr = isQual ? 'QUALIFIED' : 'REJECTED';
        const resClass = isQual ? 'profit' : 'loss';

        // Fix quotes for inline JSON passing
        const sJson = JSON.stringify(s).replace(/'/g, "&#39;").replace(/"/g, "&quot;");

        html += `
            <tr onclick="inspectSignalLifecycle(${sJson})" style="cursor:pointer">
                <td class="mono text-secondary">${time}</td>
                <td class="td-strong">${sym}</td>
                <td class="mono">${tf}</td>
                <td class="${sideClass}">${side}</td>
                <td class="mono">${entry > 0 ? entry.toFixed(4) : '—'}</td>
                <td class="mono ${edgeClass}">${edgeStr}</td>
                <td class="mono ${resClass}">${resStr}</td>
                <td class="mono text-secondary" style="max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${reason}">${reason}</td>
            </tr>
        `;
    });
    tbody.innerHTML = html;
}

function inspectSignalLifecycle(s) {
    const sym = s.symbol || '-';
    const tf = s.timeframe || '-';
    const strat = s.strategy || '-';
    const side = s.side || 'LONG';
    const entry = Number(s.entry_price || s.price || 0);
    const sl = Number(s.stop_loss || 0);
    const tp = Number(s.take_profit || 0);
    const time = new Date(s.timestamp || Date.now()).toLocaleTimeString('en-US', { hour12: false });

    let prof = { passed: false, expected_net: 0, threshold: 0, reason: 'N/A' };
    let risk = { passed: false, requested_risk: 0, available_risk: 0, exposure_after: 0, exposure_limit: 0, reason: 'N/A' };
    
    if (s.evaluation) {
        if (s.evaluation.profitability) prof = s.evaluation.profitability;
        if (s.evaluation.risk) risk = s.evaluation.risk;
    }

    const isQual = prof.passed && risk.passed;

    // Update Drawer Title
    document.getElementById('drawer-title').innerText = 'SIGNAL DETAILS';
    document.querySelector('.drawer-title-wrap .badge-indigo').innerText = 'EVAL';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.background = isQual ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.color = isQual ? 'var(--profit-green)' : 'var(--loss-red)';
    document.querySelector('.drawer-title-wrap .badge-indigo').style.borderColor = isQual ? 'var(--profit-green)' : 'var(--loss-red)';

    let html = `
        <div style="font-family: var(--font-heading); font-size: 14px; font-weight: 700; color: var(--text-primary); margin-bottom: 4px;">
            ${sym} · ${tf} · ${strat} · <span class="${side === 'LONG' || side === 'BUY' ? 'profit' : 'loss'}">${side}</span>
        </div>
        <div class="mono text-secondary" style="font-size: 11px; margin-bottom: 24px;">${time} IST</div>

        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">SIGNAL SUMMARY</div>
            <div class="kv-row"><span>Entry</span><span class="mono">${entry > 0 ? entry.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Stop Loss</span><span class="mono">${sl > 0 ? sl.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Take Profit</span><span class="mono">${tp > 0 ? tp.toFixed(4) : '—'}</span></div>
            <div class="kv-row"><span>Confidence</span><span class="mono">${s.confidence ? (s.confidence * 100).toFixed(1) + '%' : '—'}</span></div>
        </div>

        <div class="terminal-card" style="margin-bottom: 16px;">
            <div class="card-title">EXPECTED EDGE</div>
            <div class="kv-row"><span>Expected Gross</span><span class="mono">${prof.expected_gross !== undefined ? prof.expected_gross.toFixed(2) + '%' : '—'}</span></div>
            <div class="kv-row"><span>Fees + Slippage</span><span class="mono">${prof.fees !== undefined ? '-' + prof.fees.toFixed(2) + '%' : '—'}</span></div>
            <div class="kv-row"><span>Expected Net</span><span class="mono">${prof.expected_net !== undefined ? prof.expected_net.toFixed(2) + '%' : '—'}</span></div>
            <div class="kv-row"><span>Minimum Required</span><span class="mono">${prof.threshold !== undefined ? prof.threshold.toFixed(2) + '%' : '—'}</span></div>
        </div>
    `;

    // Decision Block
    const finalStatus = isQual ? 'TRADE ELIGIBLE' : 'TRADE REJECTED';
    const finalColor = isQual ? 'var(--profit-green)' : 'var(--loss-red)';
    const combinedReason = prof.passed ? (risk.passed ? 'Bullish setup + valid strategy signal + positive expected net return + risk within limits.' : risk.reason) : prof.reason;

    html += `
        <div class="terminal-card" style="margin-bottom: 16px; border: 1px solid ${isQual ? 'var(--profit-green)' : 'var(--loss-red)'}; background: ${isQual ? 'rgba(16, 185, 129, 0.03)' : 'rgba(239, 68, 68, 0.03)'};">
            <div class="card-title" style="color: ${finalColor};">DECISION</div>
            <div class="kv-row"><span>Profitability</span><span class="mono ${prof.passed ? 'profit' : 'loss'}">${prof.passed ? '✓ PASSED' : '✕ FAILED'}</span></div>
            <div class="kv-row"><span>Risk</span><span class="mono ${risk.passed ? 'profit' : (isQual ? 'profit' : 'text-secondary')}">${isQual ? '✓ PASSED' : (risk.passed ? '✓ PASSED' : '—')}</span></div>
            <div class="kv-row"><span>Execution</span><span class="mono ${isQual ? 'profit' : 'text-secondary'}">${isQual ? '✓ READY' : '—'}</span></div>
            
            <div style="margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border-medium);">
                <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 4px;">WHY:</div>
                <div style="font-size: 12px; color: var(--text-primary); line-height: 1.4; margin-bottom: 12px;">${combinedReason}</div>
                
                <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 4px;">FINAL:</div>
                <div style="font-family: var(--font-heading); font-size: 14px; font-weight: 700; color: ${finalColor};">${finalStatus}</div>
            </div>
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
        window.modalChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['Prior', 'Signal', 'Next'],
                datasets: [{
                    label: 'Price',
                    data: [entry*0.99, entry, entry*1.01],
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
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } },
                    y: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } }
                }
            }
        });
    }
}
"""

if 'fetchScannerDataV2' not in content:
    content += scanner_js

content = content.replace('fetchDashboardData(), fetchDashboardDataV2(),', 'fetchDashboardData(), fetchDashboardDataV2(), fetchScannerDataV2(),')

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Scanner JS updated.")
