// ==========================================
// SPA ROUTING & NAVIGATION
// ==========================================
document.addEventListener("DOMContentLoaded", () => {
    const navItems = document.querySelectorAll(".nav-item");
    const views = document.querySelectorAll(".view-container");
    const pageTitle = document.getElementById("page-title");

    navItems.forEach(item => {
        item.addEventListener("click", (e) => {
            e.preventDefault();
            const targetView = item.getAttribute("data-view");

            // Update Nav
            navItems.forEach(nav => nav.classList.remove("active"));
            item.classList.add("active");

            // Update Views
            views.forEach(view => {
                if(view.id === "view-" + targetView) {
                    view.classList.add("active");
                } else {
                    view.classList.remove("active");
                }
            });

            // Update Title
            pageTitle.innerText = targetView.charAt(0).toUpperCase() + targetView.slice(1);
        });
    });
});


// ==========================================
// UTILITIES
// ==========================================
const formatCurrency = (val) => {
    const num = Number(val);
    if (isNaN(num)) return "$0.00";
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
};
const formatPct = (val) => (val * 100).toFixed(2) + '%';
const formatTime = (ts) => {
    if (!ts) return "-";
    const d = new Date(ts);
    return d.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false });
};
const formatDateTime = (ts) => {
    if (!ts) return "-";
    const d = new Date(ts);
    // Use sv-SE for YYYY-MM-DD HH:mm:ss format
    return d.toLocaleString('sv-SE', { timeZone: 'Asia/Kolkata' });
};


// ==========================================
// 1. CLOCK & UPTIME ARCHITECTURE (CLIENT-SIDE)
// ==========================================
let serverTimeOffset = 0;
let lastRenderedSecond = -1;
let isClockRunning = false;
let botStartTimeMs = null;

function renderFormattedTime(ms) {
    const d = new Date(ms);
    // Explicitly format as IST
    const timeStr = d.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false });
    
    const navClock = document.getElementById('nav-clock');
    if (navClock) navClock.innerText = timeStr + ' IST';
    
    // Authoritative Uptime from backend start time
    if (botStartTimeMs) {
        const uptimeSecs = Math.max(0, Math.floor((ms - botStartTimeMs) / 1000));
        const hrs = Math.floor(uptimeSecs / 3600).toString().padStart(2, '0');
        const mins = Math.floor((uptimeSecs % 3600) / 60).toString().padStart(2, '0');
        const secs = (uptimeSecs % 60).toString().padStart(2, '0');
        
        const hdrUptime = document.getElementById('hdr-uptime');
        if (hdrUptime) hdrUptime.innerText = `${hrs}:${mins}:${secs}`;
    }
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
// 2. DATA POLLING & BINDING
// ==========================================

async function fetchDashboardData() {
    const requestStart = Date.now();
    try {
        const res = await fetch('/api/status', { cache: 'no-store' });
        const requestEnd = Date.now();
        const data = await res.json();
        
        // Sync Clock
        if (data.server_time) {
            const serverMs = new Date(data.server_time).getTime();
            const midpoint = (requestStart + requestEnd) / 2;
            serverTimeOffset = serverMs - midpoint;
        }

        // Sync Bot Uptime
        if (data.bot_start_time) {
            botStartTimeMs = new Date(data.bot_start_time).getTime();
        }

        // --- DASHBOARD: Performance Bar ---
        document.getElementById('pb-balance').innerText = formatCurrency(data.cash);
        document.getElementById('pb-today').innerText = formatCurrency(data.realized_pnl);
        
        const r_pnl = document.getElementById('pb-realized');
        r_pnl.innerText = formatCurrency(data.realized_pnl);
        r_pnl.className = data.realized_pnl >= 0 ? 'perf-value val-green' : 'perf-value val-red';
        
        const u_pnl = document.getElementById('pb-unrealized');
        u_pnl.innerText = formatCurrency(data.unrealized_pnl);
        u_pnl.className = data.unrealized_pnl >= 0 ? 'perf-value val-green' : 'perf-value val-red';
        
        document.getElementById('pb-fees').innerText = formatCurrency(data.fees);
        document.getElementById('pb-mdd').innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        
        // --- DASHBOARD: Equity Box (Authoritative High/Low) ---
        document.getElementById('eq-current').innerText = formatCurrency(data.equity);
        
        if (data.equity_high !== null && data.equity_high !== undefined) {
            document.getElementById('eq-high').innerText = formatCurrency(data.equity_high);
        } else {
            document.getElementById('eq-high').innerText = "Awaiting history";
        }

        if (data.equity_low !== null && data.equity_low !== undefined) {
            document.getElementById('eq-low').innerText = formatCurrency(data.equity_low);
        } else {
            document.getElementById('eq-low').innerText = "Awaiting history";
        }

        if (data.equity_change !== null && data.equity_change !== undefined) {
            document.getElementById('eq-change').innerText = (data.equity_change >= 0 ? "+" : "") + data.equity_change.toFixed(2) + "%";
        } else {
            document.getElementById('eq-change').innerText = "-";
        }
        
        // --- SIDEBAR HEALTH ---
        const h_status = (state) => state === 'OK' ? 'dot-green' : 'dot-red';
        if(data.components) {
            if(data.components.binance) document.getElementById('h-ws').className = `dot ${h_status(data.components.binance)}`;
            if(data.components.data) document.getElementById('h-md').className = `dot ${h_status(data.components.data)}`;
            if(data.components.execution) document.getElementById('h-ex').className = `dot ${h_status(data.components.execution)}`;
        }

        // --- RISK PAGE ---
        document.getElementById('rk-daily').innerText = formatCurrency(data.realized_pnl + data.unrealized_pnl);
        document.getElementById('rk-daily').className = (data.realized_pnl + data.unrealized_pnl) >= 0 ? 'val-green' : 'val-red';
        document.getElementById('rk-mdd').innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        document.getElementById('rk-pos').innerText = data.open_positions || 0;

        // --- POSITIONS SUMMARY ---
        document.getElementById('pos-avail').innerText = formatCurrency(data.cash);

        // --- SETTINGS PAGE ---
        document.getElementById('set-mode').innerText = data.mode || "TESTNET";

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
        const closedPos = positions.filter(p => p.status === 'CLOSED');
        
        // --- DASHBOARD & POSITIONS: Active Positions ---
        const activeBodies = [document.getElementById('active-pos-body'), document.getElementById('pos-full-body')];
        let exposure = 0;
        let total_upnl = 0;

        const posHtml = openPos.length === 0 ? '<tr><td colspan="12" class="empty-state">NO OPEN POSITIONS</td></tr>' : openPos.map(p => {
            const uPnlStr = p.pnl >= 0 ? `<span class="val-green">${formatCurrency(p.pnl)}</span>` : `<span class="val-red">${formatCurrency(p.pnl)}</span>`;
            const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag-long' : 'tag-short';
            const val = p.quantity * p.entry_price;
            exposure += val;
            total_upnl += p.pnl;
            
            return `<tr>
                <td>${p.symbol}</td>
                <td class="${sideClass}">${p.action}</td>
                <td>${Number(p.entry_price).toFixed(4)}</td>
                <td>-</td>
                <td>${p.quantity}</td>
                <td>${formatCurrency(val)}</td>
                <td>${uPnlStr}</td>
                <td>-</td>
                <td>-</td>
                <td>-</td>
                <td>-</td>
                <td>OPEN</td>
            </tr>`;
        }).join('');

        activeBodies.forEach(b => { if(b) b.innerHTML = posHtml; });
        
        // Update Pos Summary
        document.getElementById('pos-count').innerText = openPos.length;
        document.getElementById('pos-exposure').innerText = formatCurrency(exposure);
        document.getElementById('pos-upnl').innerText = formatCurrency(total_upnl);
        document.getElementById('pos-upnl').className = total_upnl >= 0 ? 'perf-value val-green' : 'perf-value val-red';


        // --- DASHBOARD & TRADES: Trade Ledger ---
        const dashTradesBody = document.getElementById('recent-trades-body');
        const fullTradesBody = document.getElementById('trades-full-body');
        
        let wins = 0;
        let losses = 0;
        let net_pnl = 0;
        let total_fees = 0;

        const allClosed = [...closedPos].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
        
        allClosed.forEach(p => {
            if (p.pnl > 0) wins++;
            else if (p.pnl < 0) losses++;
            net_pnl += (p.pnl || 0);
            total_fees += (p.fees || 0);
        });

        const mapTrade = (p) => {
            const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag-long' : 'tag-short';
            const tsShort = p.timestamp ? formatDateTime(p.timestamp) : '-';
            const orderIdStr = p.order_id ? String(p.order_id).substring(0, 8) + '...' : '-';
            const pnlClass = p.pnl >= 0 ? 'val-green' : 'val-red';
            
            return `<tr>
                <td>${tsShort}</td>
                <td>-</td>
                <td>${p.symbol}</td>
                <td class="${sideClass}">${p.action}</td>
                <td>${Number(p.entry_price).toFixed(4)}</td>
                <td>${Number(p.exit_price).toFixed(4)}</td>
                <td>${p.quantity}</td>
                <td class="${pnlClass}">${formatCurrency(p.pnl)}</td>
                <td>${formatCurrency(p.fees || 0)}</td>
                <td class="${pnlClass}">${formatCurrency(p.pnl - (p.fees||0))}</td>
                <td>TP/SL</td>
                <td title="${p.order_id}">${orderIdStr}</td>
            </tr>`;
        };

        if (allClosed.length === 0) {
            if(dashTradesBody) dashTradesBody.innerHTML = '<tr><td colspan="7" class="empty-state">NO TRADES YET</td></tr>';
            if(fullTradesBody) fullTradesBody.innerHTML = '<tr><td colspan="12" class="empty-state">NO HISTORICAL TRADES</td></tr>';
        } else {
            if(dashTradesBody) dashTradesBody.innerHTML = allClosed.slice(0, 10).map(mapTrade).join('');
            if(fullTradesBody) fullTradesBody.innerHTML = allClosed.map(mapTrade).join('');
        }

        // --- TRADES & ANALYTICS SUMMARY ---
        const tr_rate = allClosed.length > 0 ? (wins / allClosed.length) : 0;
        document.getElementById('tr-total').innerText = allClosed.length;
        document.getElementById('tr-wins').innerText = wins;
        document.getElementById('tr-loss').innerText = losses;
        document.getElementById('tr-rate').innerText = formatPct(tr_rate);
        document.getElementById('tr-net').innerText = formatCurrency(net_pnl);
        document.getElementById('tr-net').className = net_pnl >= 0 ? 'perf-value val-green' : 'perf-value val-red';
        document.getElementById('tr-fees').innerText = formatCurrency(total_fees);

        // Analytics
        document.getElementById('an-total').innerText = allClosed.length;
        document.getElementById('an-winrate').innerText = formatPct(tr_rate);
        
        let gross_win = 0, gross_loss = 0;
        allClosed.forEach(p => {
            if(p.pnl > 0) gross_win += p.pnl;
            else if(p.pnl < 0) gross_loss += Math.abs(p.pnl);
        });
        
        document.getElementById('an-pf').innerText = gross_loss > 0 ? (gross_win / gross_loss).toFixed(2) : (gross_win > 0 ? '∞' : '0.00');
        document.getElementById('an-exp').innerText = allClosed.length > 0 ? formatCurrency(net_pnl / allClosed.length) : '$0.00';
        document.getElementById('an-avg-win').innerText = wins > 0 ? formatCurrency(gross_win / wins) : '$0.00';
        document.getElementById('an-avg-loss').innerText = losses > 0 ? formatCurrency(gross_loss / losses) : '$0.00';

    } catch (e) {
        console.error("Failed to fetch trades:", e);
    }
}

async function fetchScanner() {
    try {
        const res = await fetch('/api/scanner', { cache: 'no-store' });
        const data = await res.json();
        
        // --- DASHBOARD Scanner ---
        document.getElementById('sc-total').innerText = data.symbols_scanned || 0;
        document.getElementById('sc-signals').innerText = data.signals_detected || 0;
        document.getElementById('sc-qual').innerText = data.orders_submitted || 0;
        document.getElementById('sc-rej').innerText = data.signals_rejected || 0;
        
        let dataReceivingCount = 0;
        let evaluatedCount = 0;
        let lastMarketTs = 0;
        let lastEvalTs = 0;
        const totalSyms = data.symbols ? data.symbols.length : 0;
        
        const marketRows = [];

        if (data.last_market_update) {
            for (const sym of Object.keys(data.last_market_update)) {
                dataReceivingCount++;
                const ts = new Date(data.last_market_update[sym]).getTime();
                if (ts > lastMarketTs) lastMarketTs = ts;
                
                marketRows.push(`<tr>
                    <td>${sym}</td>
                    <td>-</td>
                    <td>-</td>
                    <td>4H</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td class="val-green">CONNECTED</td>
                    <td>${formatTime(ts)}</td>
                </tr>`);
            }
        }
        
        // --- MARKET VIEW ---
        const marketBody = document.getElementById('market-body');
        if(marketBody) {
            if(marketRows.length > 0) marketBody.innerHTML = marketRows.join('');
            else marketBody.innerHTML = '<tr><td colspan="9" class="empty-state">No active symbols scanned</td></tr>';
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
        
        // --- DASHBOARD & SIGNALS: Opportunities ---
        const oppBody = document.getElementById('opp-short-body');
        const sigFullBody = document.getElementById('signals-full-body');
        
        // --- STRATEGY METRICS ---
        const stratBody = document.getElementById('strategy-metrics-body');
        if (data.strategy_metrics && Object.keys(data.strategy_metrics).length > 0) {
            document.getElementById('hdr-strategies').innerText = Object.keys(data.strategy_metrics).length + " ACTIVE";
            let stratRows = [];
            for (const [strat, m] of Object.entries(data.strategy_metrics)) {
                stratRows.push(`<tr>
                    <td>${strat}</td>
                    <td>${m.signals || 0}</td>
                    <td>${m.qualified || 0}</td>
                    <td>${m.rejected || 0}</td>
                    <td>${m.executed || 0}</td>
                </tr>`);
            }
            if (stratBody) stratBody.innerHTML = stratRows.join('');
        } else {
            if (stratBody) stratBody.innerHTML = '<tr><td colspan="5" class="empty-state">No metrics available</td></tr>';
        }
        
        const mapOpp = (o, full) => {
            const sideClass = o.side === 'BUY' ? 'tag-long' : 'tag-short';
            const tsShort = o.timestamp ? String(o.timestamp).substring(11, 19) : '-';
            const confStr = o.confidence ? formatPct(o.confidence) : '-';
            const grossStr = o.expected_gross_return ? formatPct(o.expected_gross_return) : '-';
            const netStr = o.expected_net_return ? (o.expected_net_return > 0 ? `<span class="val-green">+${formatPct(o.expected_net_return)}</span>` : `<span class="val-red">${formatPct(o.expected_net_return)}</span>`) : '-';
            const feeStr = o.estimated_fees ? formatPct(o.estimated_fees) : '0.00%';
            const priceStr = o.current_price ? Number(o.current_price).toFixed(2) : '-';
            const decClass = o.decision === 'ACCEPTED' ? 'val-green' : 'val-red';
            const shortReason = o.reason ? (o.reason.length > 25 ? o.reason.substring(0, 25) + '...' : o.reason) : '-';
            
            if (full) {
                return `<tr>
                    <td>${tsShort}</td><td>${o.symbol}</td><td class="${sideClass}">${o.side}</td>
                    <td>${priceStr}</td><td>-</td><td>${confStr}</td><td>${grossStr}</td><td>${feeStr}</td>
                    <td>${netStr}</td><td>-</td><td>-</td>
                    <td class="${decClass}">${o.decision || '-'}</td><td title="${o.reason}">${o.reason || '-'}</td>
                </tr>`;
            } else {
                return `<tr>
                    <td>${tsShort}</td><td>${o.symbol}</td><td class="${sideClass}">${o.side}</td>
                    <td>${priceStr}</td><td>${confStr}</td><td>${netStr}</td>
                    <td class="${decClass}">${o.decision || '-'}</td>
                </tr>`;
            }
        };

        if (!data.top_opportunities || data.top_opportunities.length === 0) {
            if(oppBody) oppBody.innerHTML = '<tr><td colspan="7" class="empty-state">NO OPPORTUNITIES</td></tr>';
            if(sigFullBody) sigFullBody.innerHTML = '<tr><td colspan="13" class="empty-state">NO OPPORTUNITIES / SIGNALS LOGGED YET</td></tr>';
        } else {
            if(oppBody) oppBody.innerHTML = data.top_opportunities.slice(0, 5).map(o => mapOpp(o, false)).join('');
            if(sigFullBody) sigFullBody.innerHTML = data.top_opportunities.map(o => mapOpp(o, true)).join('');
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
// INITIALIZATION
// ==========================================
startClockLoop(); 
updateDashboard(); 
setInterval(updateDashboard, 2000); 
