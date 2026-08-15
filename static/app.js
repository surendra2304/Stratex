// Dashboard Javascript (Redesign)

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

// ==========================================
// 1. CLOCK ARCHITECTURE (CLIENT-SIDE)
// ==========================================
let serverTimeOffset = 0;
let lastRenderedSecond = -1;
let isClockRunning = false;
let startTimestamp = Date.now(); // For uptime approximation

function renderFormattedTime(ms) {
    const d = new Date(ms);
    const timeStr = d.toLocaleTimeString('en-US', { hour12: false });
    
    const navClock = document.getElementById('nav-clock');
    if (navClock) navClock.innerText = timeStr + ' UTC';
    
    const ftServerTime = document.getElementById('ft-server-time');
    if (ftServerTime) ftServerTime.innerText = timeStr;
    
    // Uptime approximation
    const uptimeSecs = Math.floor((ms - startTimestamp) / 1000);
    const hrs = Math.floor(uptimeSecs / 3600).toString().padStart(2, '0');
    const mins = Math.floor((uptimeSecs % 3600) / 60).toString().padStart(2, '0');
    const secs = (uptimeSecs % 60).toString().padStart(2, '0');
    
    const pbUptime = document.getElementById('pb-uptime');
    if (pbUptime) pbUptime.innerText = `${hrs}:${mins}:${secs}`;
    
    const ftUptime = document.getElementById('ft-uptime');
    if (ftUptime) ftUptime.innerText = `${hrs}:${mins}:${secs}`;
}

function renderClock() {
    const correctedNow = Date.now() + serverTimeOffset;
    const second = Math.floor(correctedNow / 1000);

    if (second !== lastRenderedSecond) {
        lastRenderedSecond = second;
        renderFormattedTime(correctedNow);
    }

    requestAnimationFrame(renderClock);
}

function startClockLoop() {
    if (!isClockRunning) {
        isClockRunning = true;
        requestAnimationFrame(renderClock);
    }
}

// ==========================================
// 2. DATA POLLING
// ==========================================

async function fetchDashboardData() {
    const requestStart = Date.now();
    try {
        const res = await fetch('/api/status', { cache: 'no-store' });
        const requestEnd = Date.now();
        const data = await res.json();
        
        // --- Sync Clock ---
        if (data.server_time) {
            const serverMs = new Date(data.server_time).getTime();
            const midpoint = (requestStart + requestEnd) / 2;
            serverTimeOffset = serverMs - midpoint;
            
            const latency = Math.floor(requestEnd - requestStart);
            const ftLatency = document.getElementById('ft-latency');
            if(ftLatency) ftLatency.innerText = `${latency} ms`;
        }

        // --- Bind Top Performance Bar ---
        document.getElementById('pb-balance').innerText = formatCurrency(data.cash); // Total balance
        document.getElementById('pb-today').innerText = formatCurrency(data.realized_pnl);
        
        const r_pnl = document.getElementById('pb-realized');
        r_pnl.innerText = formatCurrency(data.realized_pnl);
        r_pnl.className = data.realized_pnl >= 0 ? 'perf-value val-green' : 'perf-value val-red';
        
        const u_pnl = document.getElementById('pb-unrealized');
        u_pnl.innerText = formatCurrency(data.unrealized_pnl);
        u_pnl.className = data.unrealized_pnl >= 0 ? 'perf-value val-green' : 'perf-value val-red';
        
        document.getElementById('pb-fees').innerText = formatCurrency(data.fees);
        document.getElementById('pb-mdd').innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        
        // --- Equity Box ---
        document.getElementById('eq-current').innerText = formatCurrency(data.equity);
        // (High/Low mocked as we don't have historical series in /api/status yet)
        document.getElementById('eq-high').innerText = formatCurrency(data.equity);
        document.getElementById('eq-low').innerText = formatCurrency(data.equity);
        
        // --- Health Panel (Mapping from components) ---
        // Just as an example, map what we can
        
    } catch (e) {
        console.error("Failed to fetch status:", e);
    }
}

async function fetchTrades() {
    try {
        const res = await fetch('/api/trades', { cache: 'no-store' });
        const data = await res.json();
        const positions = data.positions || [];
        const openPos = positions.filter(p => p.status === 'OPEN');
        
        // --- Active Positions ---
        const posBody = document.getElementById('positions-body');
        if (openPos.length === 0) {
            posBody.innerHTML = '<tr><td colspan="9" class="empty-state">NO OPEN POSITIONS</td></tr>';
        } else {
            posBody.innerHTML = openPos.map(p => {
                const uPnlStr = p.pnl >= 0 ? `<span class="val-green">${formatCurrency(p.pnl)}</span>` : `<span class="val-red">${formatCurrency(p.pnl)}</span>`;
                const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag-long' : 'tag-short';
                
                return `<tr>
                    <td>${p.symbol}</td>
                    <td class="${sideClass}">${p.action}</td>
                    <td>${Number(p.entry_price).toFixed(4)}</td>
                    <td>-</td>
                    <td>${p.quantity}</td>
                    <td>${uPnlStr}</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                </tr>`;
            }).join('');
        }
        
        // --- Live Trade Feed ---
        const tradesBody = document.getElementById('trades-body');
        if (positions.length === 0) {
            tradesBody.innerHTML = '<tr><td colspan="8" class="empty-state">NO TRADES YET</td></tr>';
        } else {
            const allPos = [...positions].sort((a, b) => {
                return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
            }).slice(0, 15);
            
            tradesBody.innerHTML = allPos.map(p => {
                const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag-long' : 'tag-short';
                const tsShort = p.timestamp ? String(p.timestamp).substring(11, 19) : '-';
                const orderIdStr = p.order_id ? String(p.order_id).substring(0, 8) + '...' : '-';
                const statClass = p.status === 'OPEN' ? 'val-green' : 'val-red';
                const pnlClass = p.pnl >= 0 ? 'val-green' : 'val-red';
                
                return `<tr>
                    <td>${tsShort}</td>
                    <td>${p.symbol}</td>
                    <td class="${sideClass}">${p.action}</td>
                    <td title="${p.order_id}">${orderIdStr}</td>
                    <td>${p.quantity}</td>
                    <td>${Number(p.entry_price).toFixed(4)}</td>
                    <td class="${pnlClass}">${formatCurrency(p.pnl)}</td>
                    <td class="${statClass}">${p.status}</td>
                </tr>`;
            }).join('');
        }
    } catch (e) {
        console.error("Failed to fetch trades:", e);
    }
}

async function fetchScanner() {
    try {
        const res = await fetch('/api/scanner', { cache: 'no-store' });
        const data = await res.json();
        
        document.getElementById('sc-total').innerText = data.symbols_scanned || 0;
        document.getElementById('sc-signals').innerText = data.signals_detected || 0;
        document.getElementById('sc-qual').innerText = data.orders_submitted || 0;
        document.getElementById('sc-rej').innerText = data.signals_rejected || 0;
        
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
        
        document.getElementById('sc-data-ratio').innerText = `${dataReceivingCount}/${totalSyms}`;
        document.getElementById('sc-eval-ratio').innerText = `${evaluatedCount}/${totalSyms}`;
        
        if(lastMarketTs > 0) document.getElementById('st-last-market').innerText = formatTime(lastMarketTs);
        if(lastEvalTs > 0) document.getElementById('st-last-eval').innerText = formatTime(lastEvalTs);
        
        // Delay approximation
        const ftDelay = document.getElementById('ft-delay');
        if(ftDelay && lastMarketTs > 0) {
            const delay = Math.max(0, Date.now() + serverTimeOffset - lastMarketTs);
            ftDelay.innerText = `${delay} ms`;
        }
        
        const oppBody = document.getElementById('opportunities-body');
        if (!data.top_opportunities || data.top_opportunities.length === 0) {
            oppBody.innerHTML = '<tr><td colspan="10" class="empty-state">NO OPPORTUNITIES AT THE MOMENT</td></tr>';
        } else {
            oppBody.innerHTML = data.top_opportunities.map(o => {
                const sideClass = o.side === 'BUY' ? 'tag-long' : 'tag-short';
                const tsShort = o.timestamp ? String(o.timestamp).substring(11, 19) : '-';
                const confStr = o.confidence ? formatPct(o.confidence) : '-';
                const grossStr = o.expected_gross_return ? formatPct(o.expected_gross_return) : '-';
                const netStr = o.expected_net_return ? (o.expected_net_return > 0 ? `<span class="val-green">+${formatPct(o.expected_net_return)}</span>` : `<span class="val-red">${formatPct(o.expected_net_return)}</span>`) : '-';
                const feeStr = o.estimated_fees ? formatPct(o.estimated_fees) : '0.00%';
                const priceStr = o.current_price ? Number(o.current_price).toFixed(2) : '-';
                const decClass = o.decision === 'ACCEPTED' ? 'val-green' : 'val-red';
                const shortReason = o.reason ? (o.reason.length > 25 ? o.reason.substring(0, 25) + '...' : o.reason) : '-';
                
                return `<tr>
                    <td>${tsShort}</td>
                    <td>${o.symbol}</td>
                    <td class="${sideClass}">${o.side}</td>
                    <td>${priceStr}</td>
                    <td>${confStr}</td>
                    <td>${grossStr}</td>
                    <td>${feeStr}</td>
                    <td>${netStr}</td>
                    <td class="${decClass}">${o.decision || '-'}</td>
                    <td title="${o.reason}">${shortReason}</td>
                </tr>`;
            }).join('');
        }
    } catch (e) {
        console.error("Failed to fetch scanner stats:", e);
    }
}

function updateDashboard() {
    const spinner = document.getElementById('refresh-spinner');
    if(spinner) spinner.classList.add('spinning');
    
    Promise.all([
        fetchDashboardData(),
        fetchTrades(),
        fetchScanner()
    ]).finally(() => {
        if(spinner) spinner.classList.remove('spinning');
    });
}

// ==========================================
// 3. INITIALIZATION
// ==========================================
startClockLoop(); // Start the 1-second clock loop immediately (runs on requestAnimationFrame)

updateDashboard(); // Initial fetch
setInterval(updateDashboard, 2000); // Decoupled polling interval
