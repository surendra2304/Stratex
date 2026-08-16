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
            const textContent = item.querySelector("span:not(.nav-icon)").innerText;
            pageTitle.innerText = textContent;
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
    return d.toLocaleString('sv-SE', { timeZone: 'Asia/Kolkata' });
};
const applyColor = (el, val, isPct = false) => {
    if(!el) return;
    el.innerText = isPct ? formatPct(val) : formatCurrency(val);
    el.className = 'metric-value ' + (val > 0 ? 'val-green' : (val < 0 ? 'val-red' : 'val-neutral'));
};

// ==========================================
// 1. CLOCK & UPTIME
// ==========================================
let serverTimeOffset = 0;
let lastRenderedSecond = -1;
let isClockRunning = false;
let botStartTimeMs = null;

function renderFormattedTime(ms) {
    const d = new Date(ms);
    const timeStr = d.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false });
    
    const navClock = document.getElementById('nav-clock');
    if (navClock) navClock.innerText = timeStr + ' IST';
    
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
        
        if (data.server_time) {
            const serverMs = new Date(data.server_time).getTime();
            const midpoint = (requestStart + requestEnd) / 2;
            serverTimeOffset = serverMs - midpoint;
        }

        if (data.bot_start_time) {
            botStartTimeMs = new Date(data.bot_start_time).getTime();
        }

        // Top Metrics
        document.getElementById('pb-balance').innerText = formatCurrency(data.cash);
        applyColor(document.getElementById('pb-today'), data.realized_pnl);
        applyColor(document.getElementById('pb-realized'), data.realized_pnl);
        applyColor(document.getElementById('pb-unrealized'), data.unrealized_pnl);
        document.getElementById('pb-fees').innerText = formatCurrency(data.fees);
        document.getElementById('pb-mdd').innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        
        // Header Status
        const engineDot = document.getElementById('hdr-engine-dot');
        const engineText = document.getElementById('hdr-engine-text');
        if (engineDot && engineText) {
            if (data.engine_status === 'ONLINE' || data.engine_healthy) {
                engineDot.className = 'dot dot-green';
                engineText.className = 'status-online';
                engineText.innerText = 'ENGINE ONLINE (TESTNET)';
            } else {
                engineDot.className = 'dot dot-red';
                engineText.className = 'status-offline';
                engineText.innerText = 'ENGINE OFFLINE';
            }
        }
        
        // Sidebar Health
        const h_status = (state) => state === 'OK' ? 'dot-green' : 'dot-red';
        if(data.components) {
            if(data.components.binance) {
                document.getElementById('h-bn').className = `dot ${h_status(data.components.binance)}`;
                document.getElementById('h-ws').className = `dot ${h_status(data.components.binance)}`;
            }
            if(data.components.data) document.getElementById('h-md').className = `dot ${h_status(data.components.data)}`;
            if(data.components.execution) document.getElementById('h-ex').className = `dot ${h_status(data.components.execution)}`;
            if(document.getElementById('h-st')) document.getElementById('h-st').className = `dot ${h_status(data.components.strategy || 'OK')}`;
            if(document.getElementById('h-pt')) document.getElementById('h-pt').className = `dot dot-green`; // Implicitly ok if process runs
            if(document.getElementById('h-rs')) document.getElementById('h-rs').className = `dot dot-green`;
            if(document.getElementById('h-pe')) document.getElementById('h-pe').className = `dot dot-green`;
        }

        // Risk View
        document.getElementById('rk-daily').innerText = formatCurrency(data.realized_pnl + data.unrealized_pnl);
        document.getElementById('rk-daily').className = 'status-val ' + ((data.realized_pnl + data.unrealized_pnl) >= 0 ? 'val-green' : 'val-red');
        document.getElementById('rk-mdd').innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        document.getElementById('rk-pos').innerText = data.open_positions || 0;

        // Positions View
        document.getElementById('pos-avail').innerText = formatCurrency(data.cash);

        // Settings View
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
        
        const activeBodies = [document.getElementById('active-pos-body'), document.getElementById('pos-full-body')];
        let exposure = 0;
        let total_upnl = 0;

        const posHtml = openPos.length === 0 ? '<tr><td colspan="12" class="empty-state">NO OPEN POSITIONS</td></tr>' : openPos.map(p => {
            const uPnlStr = p.pnl > 0 ? `<span class="val-green">+${formatCurrency(p.pnl)}</span>` : (p.pnl < 0 ? `<span class="val-red">${formatCurrency(p.pnl)}</span>` : '$0.00');
            const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag tag-long' : 'tag tag-short';
            const val = p.quantity * p.entry_price;
            exposure += val;
            total_upnl += p.pnl;
            
            return `<tr>
                <td>${p.symbol}</td>
                <td><span class="${sideClass}">${p.action}</span></td>
                <td>${Number(p.entry_price).toFixed(4)}</td>
                <td>-</td>
                <td>${p.quantity}</td>
                <td>${formatCurrency(val)}</td>
                <td>${uPnlStr}</td>
                <td>${p.sl || '-'}</td>
                <td>${p.tp || '-'}</td>
                <td><span class="tag tag-active">OPEN</span></td>
            </tr>`;
        }).join('');

        activeBodies.forEach(b => { if(b) b.innerHTML = posHtml; });
        
        document.getElementById('pos-count').innerText = openPos.length;
        document.getElementById('pos-exposure').innerText = formatCurrency(exposure);
        applyColor(document.getElementById('pos-upnl'), total_upnl);

        // Ledger
        const dashTradesBody = document.getElementById('recent-trades-body');
        const fullTradesBody = document.getElementById('trades-full-body');
        
        let wins = 0, losses = 0, net_pnl = 0, total_fees = 0;
        const allClosed = [...closedPos].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
        
        allClosed.forEach(p => {
            if (p.pnl > 0) wins++;
            else if (p.pnl < 0) losses++;
            net_pnl += (p.pnl || 0);
            total_fees += (p.fees || 0);
        });

        const mapTrade = (p) => {
            const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag tag-long' : 'tag tag-short';
            const tsShort = p.timestamp ? formatDateTime(p.timestamp) : '-';
            const orderIdStr = p.order_id ? String(p.order_id).substring(0, 8) + '...' : '-';
            const pnlStr = p.pnl > 0 ? `<span class="val-green">+${formatCurrency(p.pnl)}</span>` : (p.pnl < 0 ? `<span class="val-red">${formatCurrency(p.pnl)}</span>` : '$0.00');
            const netStr = (p.pnl - (p.fees||0)) > 0 ? `<span class="val-green">+${formatCurrency(p.pnl - (p.fees||0))}</span>` : (p.pnl - (p.fees||0) < 0 ? `<span class="val-red">${formatCurrency(p.pnl - (p.fees||0))}</span>` : '$0.00');
            
            return `<tr>
                <td>${tsShort}</td>
                <td>-</td>
                <td>${p.symbol}</td>
                <td><span class="${sideClass}">${p.action}</span></td>
                <td>${Number(p.entry_price).toFixed(4)}</td>
                <td>${Number(p.exit_price).toFixed(4)}</td>
                <td>${p.quantity}</td>
                <td>${pnlStr}</td>
                <td>${formatCurrency(p.fees || 0)}</td>
                <td>${netStr}</td>
                <td><span class="tag ${p.pnl >= 0 ? 'tag-win' : 'tag-loss'}">${p.pnl >= 0 ? 'WIN' : 'LOSS'}</span></td>
                <td title="${p.order_id}">${orderIdStr}</td>
            </tr>`;
        };

        const mapDashTrade = (p) => {
            const sideClass = (p.action === 'LONG' || p.action === 'BUY') ? 'tag tag-long' : 'tag tag-short';
            const tsShort = p.timestamp ? formatDateTime(p.timestamp) : '-';
            const pnlStr = p.pnl > 0 ? `<span class="val-green">+${formatCurrency(p.pnl)}</span>` : (p.pnl < 0 ? `<span class="val-red">${formatCurrency(p.pnl)}</span>` : '$0.00');
            const statusTag = p.pnl >= 0 ? '<span class="tag tag-win">WIN</span>' : '<span class="tag tag-loss">LOSS</span>';
            
            return `<tr>
                <td>${tsShort}</td>
                <td>${p.symbol}</td>
                <td><span class="${sideClass}">${p.action}</span></td>
                <td>${Number(p.entry_price).toFixed(4)}</td>
                <td>${Number(p.exit_price).toFixed(4)}</td>
                <td>${pnlStr}</td>
                <td>${statusTag}</td>
            </tr>`;
        };

        if (allClosed.length === 0) {
            if(dashTradesBody) dashTradesBody.innerHTML = '<tr><td colspan="7" class="empty-state">NO TRADES YET</td></tr>';
            if(fullTradesBody) fullTradesBody.innerHTML = '<tr><td colspan="12" class="empty-state">NO HISTORICAL TRADES</td></tr>';
        } else {
            if(dashTradesBody) dashTradesBody.innerHTML = allClosed.slice(0, 10).map(mapDashTrade).join('');
            if(fullTradesBody) fullTradesBody.innerHTML = allClosed.map(mapTrade).join('');
        }

        // Summary
        const tr_rate = allClosed.length > 0 ? (wins / allClosed.length) : 0;
        document.getElementById('tr-total').innerText = allClosed.length;
        document.getElementById('tr-wins').innerText = wins;
        document.getElementById('tr-loss').innerText = losses;
        document.getElementById('tr-rate').innerText = formatPct(tr_rate);
        applyColor(document.getElementById('tr-net'), net_pnl);
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
        
        // Funnel
        document.getElementById('fn-signals').innerText = data.TOTAL_SIGNALS || 0;
        document.getElementById('fn-prof-rej').innerText = data.PROFITABILITY_REJECTED || 0;
        document.getElementById('fn-risk-rej').innerText = (data.RISK_REJECTED || 0) + (data.COOLDOWN_REJECTED || 0) + (data.JIT_REJECTED || 0) + (data.OTHER_REJECTED || 0);
        document.getElementById('fn-qual').innerText = data.QUALIFIED || 0;
        document.getElementById('fn-sub').innerText = data.ORDERS_SUBMITTED || 0;
        document.getElementById('fn-filled').innerText = data.ORDERS_FILLED || 0;
        
        // Markets Data (Matrix)
        let dataReceivingCount = 0;
        let evaluatedCount = 0;
        const totalSyms = data.symbols ? data.symbols.length : 0;
        const marketRows = [];

        if (data.last_market_update) {
            for (const sym of Object.keys(data.last_market_update)) {
                dataReceivingCount++;
                const ts = new Date(data.last_market_update[sym]).getTime();
                
                marketRows.push(`<tr>
                    <td>${sym}</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td><span class="tag tag-active">CONNECTED</span></td>
                    <td>${formatTime(ts)}</td>
                </tr>`);
            }
        }
        
        const marketBody = document.getElementById('market-body');
        if(marketBody) {
            if(marketRows.length > 0) marketBody.innerHTML = marketRows.join('');
            else marketBody.innerHTML = '<tr><td colspan="6" class="empty-state">No active symbols scanned</td></tr>';
        }

        if (data.last_evaluation) {
            for (const sym of Object.keys(data.last_evaluation)) {
                evaluatedCount++;
            }
        }
        
        document.getElementById('sc-eval-ratio').innerText = `${evaluatedCount}/${totalSyms} Symbols Evaluated`;
        
        // Strategy Metrics
        const stratBody = document.getElementById('strategy-metrics-body');
        if (data.strategy_metrics && Object.keys(data.strategy_metrics).length > 0) {
            let stratRows = [];
            for (const [strat, m] of Object.entries(data.strategy_metrics)) {
                stratRows.push(`<tr>
                    <td>${strat}</td>
                    <td>${m.signals || 0}</td>
                    <td>${m.qualified || 0}</td>
                    <td>${m.rejected || 0}</td>
                    <td>${m.executed || 0}</td>
                    <td><span class="tag tag-active">ACTIVE</span></td>
                </tr>`);
            }
            if (stratBody) stratBody.innerHTML = stratRows.join('');
        } else {
            if (stratBody) stratBody.innerHTML = '<tr><td colspan="6" class="empty-state">NO STRATEGY DATA</td></tr>';
        }
        
        // Timeframe Metrics
        const tfBody = document.getElementById('timeframe-metrics-body');
        if (data.timeframe_metrics && Object.keys(data.timeframe_metrics).length > 0) {
            let tfRows = [];
            for (const [tf, m] of Object.entries(data.timeframe_metrics)) {
                tfRows.push(`<tr>
                    <td>${tf}</td>
                    <td>${m.signals || 0}</td>
                    <td>${m.qualified || 0}</td>
                    <td>${m.rejected || 0}</td>
                    <td>${m.executed || 0}</td>
                </tr>`);
            }
            if (tfBody) tfBody.innerHTML = tfRows.join('');
        } else {
            if (tfBody) tfBody.innerHTML = '<tr><td colspan="5" class="empty-state">NO METRICS AVAILABLE</td></tr>';
        }
        
        // Opportunities & Signals
        const oppBody = document.getElementById('opp-short-body');
        const sigFullBody = document.getElementById('signals-full-body');
        
        const mapOpp = (o, full) => {
            const sideClass = o.side === 'BUY' ? 'tag tag-long' : 'tag tag-short';
            const tsShort = o.timestamp ? String(o.timestamp).substring(11, 19) : '-';
            const confStr = o.confidence ? formatPct(o.confidence) : '-';
            const netStr = o.expected_net_return ? (o.expected_net_return > 0 ? `<span class="val-green">+${formatPct(o.expected_net_return)}</span>` : `<span class="val-red">${formatPct(o.expected_net_return)}</span>`) : '-';
            const priceStr = o.current_price ? Number(o.current_price).toFixed(2) : '-';
            const decClass = o.decision === 'ACCEPTED' ? 'tag tag-qualified' : 'tag tag-rejected';
            const shortReason = o.reason ? (o.reason.length > 25 ? o.reason.substring(0, 25) + '...' : o.reason) : '-';
            
            if (full) {
                return `<tr>
                    <td>${tsShort}</td><td>${o.symbol}</td><td><span class="${sideClass}">${o.side}</span></td>
                    <td>${priceStr}</td><td>${confStr}</td><td>${netStr}</td>
                    <td>-</td><td>-</td>
                    <td><span class="${decClass}">${o.decision || '-'}</span></td><td title="${o.reason}">${shortReason}</td>
                </tr>`;
            } else {
                return `<tr>
                    <td>${tsShort}</td><td>${o.symbol}</td><td><span class="${sideClass}">${o.side}</span></td>
                    <td>${confStr}</td><td>${netStr}</td>
                    <td><span class="${decClass}">${o.decision || '-'}</span></td>
                </tr>`;
            }
        };

        if (!data.top_opportunities || data.top_opportunities.length === 0) {
            if(oppBody) oppBody.innerHTML = '<tr><td colspan="6" class="empty-state">NO QUALIFYING OPPORTUNITIES</td></tr>';
            if(sigFullBody) sigFullBody.innerHTML = '<tr><td colspan="10" class="empty-state">NO SIGNALS LOGGED YET</td></tr>';
        } else {
            if(oppBody) oppBody.innerHTML = data.top_opportunities.slice(0, 5).map(o => mapOpp(o, false)).join('');
            if(sigFullBody) sigFullBody.innerHTML = data.top_opportunities.map(o => mapOpp(o, true)).join('');
        }
    } catch (e) {
        console.error("Failed to fetch scanner stats:", e);
    }
}

function updateDashboard() {
    Promise.all([
        fetchDashboardData(),
        fetchTrades(),
        fetchScanner()
    ]).finally(() => {
        // any spinner removal can go here
    });
}

// ==========================================
// INITIALIZATION
// ==========================================
startClockLoop(); 
updateDashboard(); 
setInterval(updateDashboard, 2000);
