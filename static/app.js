// Dashboard Javascript

const formatCurrency = (val) => {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
};

const formatPct = (val) => {
    return (val * 100).toFixed(2) + '%';
};

const formatTime = (ts) => {
    if (!ts) return "-";
    const d = new Date(ts);
    return d.toLocaleTimeString('en-US', { hour12: false });
};

async function fetchDashboardData() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        // Status Indicators
        document.getElementById('val-status').innerText = `${data.mode} ONLINE`;
        
        // Metrics
        document.getElementById('val-balance').innerText = formatCurrency(data.equity);
        document.getElementById('val-equity').innerText = formatCurrency(data.equity);
        document.getElementById('val-cash').innerText = formatCurrency(data.cash);
        
        // PnLs
        const r_pnl = document.getElementById('val-realized');
        r_pnl.innerText = formatCurrency(data.realized_pnl);
        r_pnl.className = data.realized_pnl >= 0 ? 'metric-value val-green' : 'metric-value val-red';
        
        const u_pnl = document.getElementById('val-unrealized');
        u_pnl.innerText = formatCurrency(data.unrealized_pnl);
        u_pnl.className = data.unrealized_pnl >= 0 ? 'metric-value val-green' : 'metric-value val-red';
        
        document.getElementById('val-today').innerText = formatCurrency(data.realized_pnl); // Today = realized for now
        document.getElementById('val-fees').innerText = formatCurrency(data.fees);
        document.getElementById('val-funding').innerText = formatCurrency(data.funding);
        document.getElementById('val-mdd').innerText = data.max_drawdown.toFixed(2) + '%';
        
    } catch (e) {
        console.error("Failed to fetch status:", e);
    }
}

async function fetchTrades() {
    try {
        const res = await fetch('/api/trades');
        const data = await res.json();
        
        const positions = data.positions || [];
        
        const openPos = positions.filter(p => p.status === 'OPEN');
        const closedPos = positions.filter(p => p.status === 'CLOSED').reverse().slice(0, 5); // Last 5 closed
        
        // Active Positions Table
        const posBody = document.getElementById('positions-body');
        if (openPos.length === 0) {
            posBody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">NO OPEN POSITIONS</td></tr>';
        } else {
            posBody.innerHTML = openPos.map(p => {
                const uPnlStr = p.pnl >= 0 ? `<span class="val-green">${formatCurrency(p.pnl)}</span>` : `<span class="val-red">${formatCurrency(p.pnl)}</span>`;
                const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag-long' : 'tag-short';
                
                return `<tr>
                    <td>${p.symbol}</td>
                    <td class="${sideClass}">${p.action}</td>
                    <td>${p.entry_price.toFixed(4)}</td>
                    <td>-</td>
                    <td>${p.quantity}</td>
                    <td>${uPnlStr}</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                </tr>`;
            }).join('');
        }
        
        // Live Trade Feed Table
        const tradesBody = document.getElementById('trades-body');
        if (closedPos.length === 0) {
            tradesBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">NO TRADES YET</td></tr>';
        } else {
            tradesBody.innerHTML = closedPos.map(p => {
                const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag-long' : 'tag-short';
                const pnlStr = p.pnl >= 0 ? `<span class="val-green">+${formatCurrency(p.pnl)}</span>` : `<span class="val-red">${formatCurrency(p.pnl)}</span>`;
                return `<tr>
                    <td>${formatTime(p.timestamp)}</td>
                    <td>${p.symbol}</td>
                    <td class="${sideClass}">${p.action}</td>
                    <td>CLOSED</td>
                    <td>${pnlStr}</td>
                </tr>`;
            }).join('');
        }
        
    } catch (e) {
        console.error("Failed to fetch trades:", e);
    }
}

async function fetchScanner() {
    try {
        const res = await fetch('/api/scanner');
        const data = await res.json();
        
        document.getElementById('scan-total').innerText = data.symbols_scanned || 0;
        document.getElementById('scan-signals').innerText = data.signals_detected || 0;
        document.getElementById('scan-qual').innerText = data.orders_submitted || 0;
        document.getElementById('scan-rej').innerText = data.signals_rejected || 0;
        
        // Ratios and Timestamps
        let dataReceivingCount = 0;
        let evaluatedCount = 0;
        let lastMarketTs = 0;
        let lastEvalTs = 0;
        const totalSyms = data.symbols ? data.symbols.length : 0;
        
        if (data.last_market_update) {
            for (const sym of Object.keys(data.last_market_update)) {
                dataReceivingCount++;
                const ts = new Date(data.last_market_update[sym]).getTime();
                if (ts > lastMarketTs) lastMarketTs = ts;
            }
        }
        
        if (data.last_evaluation) {
            for (const sym of Object.keys(data.last_evaluation)) {
                evaluatedCount++;
                const ts = new Date(data.last_evaluation[sym]).getTime();
                if (ts > lastEvalTs) lastEvalTs = ts;
            }
        }
        
        document.getElementById('scan-data-ratio').innerText = `${dataReceivingCount}/${totalSyms}`;
        document.getElementById('scan-eval-ratio').innerText = `${evaluatedCount}/${totalSyms}`;
        
        document.getElementById('scan-last-update').innerText = lastMarketTs > 0 ? formatTime(lastMarketTs) : '-';
        document.getElementById('scan-last-eval').innerText = lastEvalTs > 0 ? formatTime(lastEvalTs) : '-';
        
        const oppBody = document.getElementById('opportunities-body');
        if (!data.top_opportunities || data.top_opportunities.length === 0) {
            oppBody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-muted);">NO OPPORTUNITIES YET</td></tr>';
        } else {
            oppBody.innerHTML = data.top_opportunities.map(o => {
                const sideClass = o.side === 'BUY' ? 'tag-long' : 'tag-short';
                return `<tr>
                    <td>${o.symbol}</td>
                    <td class="${sideClass}">${o.side}</td>
                    <td class="val-green">+${formatPct(o.expected_net_return)}</td>
                </tr>`;
            }).join('');
        }
    } catch (e) {
        console.error("Failed to fetch scanner stats:", e);
    }
}

function updateDashboard() {
    fetchDashboardData();
    fetchTrades();
    fetchScanner();
}

// Initial fetch and 5-second polling
updateDashboard();
setInterval(updateDashboard, 5000);
