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

const apiClient = {
    async get(url) {
        try {
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) {
                throw new Error(`HTTP error! status: ${res.status}`);
            }
            return await res.json();
        } catch (e) {
            console.error(`API Client Error (${url}):`, e);
            return null; // Graceful failure
        }
    }
};

async function fetchDashboardData() {
    const requestStart = Date.now();
    try {
        const data = await apiClient.get('/api/status');
        if (!data) return; // Stale state visual handling can be added here
        
        const requestEnd = Date.now();
        
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

        // Full Risk Page
        const rviewUsed = document.getElementById('rview-used');
        if (rviewUsed) rviewUsed.innerText = "0.00%"; 
        const rviewMdd = document.getElementById('rview-mdd');
        if (rviewMdd) rviewMdd.innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        const rviewPos = document.getElementById('rview-pos');
        if (rviewPos) rviewPos.innerText = data.open_positions || 0;

        // Positions View
        const posAvail = document.getElementById('pos-avail');
        if (posAvail) posAvail.innerText = formatCurrency(data.cash);

        // Settings View
        const setMode = document.getElementById('set-mode');
        if (setMode) setMode.innerText = data.mode || "TESTNET";
        
        const engineData = data.engine_data || {};
        const stratBody = document.getElementById('set-strat-body');
        if (stratBody) {
            stratBody.innerHTML = `
                <tr><td>Active Strategies</td><td class="td-strong">${(engineData.strategies || []).join(', ') || '-'}</td></tr>
                <tr><td>Active Timeframes</td><td class="td-strong">${(engineData.timeframes || []).join(', ') || '-'}</td></tr>
                <tr><td>Symbols Tracked</td><td class="td-strong">${engineData.symbol_count || 0}</td></tr>
            `;
        }
        
        const sysHealthBody = document.getElementById('sys-health-body');
        if (sysHealthBody) {
            const h_tag = (s) => s === 'OK' ? '<span class="tag tag-qualified">ONLINE</span>' : '<span class="tag tag-offline">ERROR</span>';
            const comp = data.components || {};
            sysHealthBody.innerHTML = `
                <tr><td>Binance REST</td><td>${h_tag(comp.binance || 'ERROR')}</td><td>-</td></tr>
                <tr><td>Binance WebSocket</td><td>${h_tag(comp.data || 'ERROR')}</td><td>-</td></tr>
                <tr><td>Market Data Stream</td><td>${h_tag(comp.data || 'ERROR')}</td><td>-</td></tr>
                <tr><td>Strategy Engine</td><td>${h_tag(comp.strategy || 'ERROR')}</td><td>-</td></tr>
                <tr><td>Portfolio Manager</td><td>${h_tag('OK')}</td><td>-</td></tr>
                <tr><td>Supervisor</td><td>${h_tag('OK')}</td><td>-</td></tr>
            `;
        }

    } catch (e) {
        console.error("Failed to fetch status:", e);
    }
}

let dailyPnLData = {}; // For Analytics histogram

async function fetchTrades() {
    try {
        const data = await apiClient.get('/api/trades');
        if (!data) return;
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
                <td class="td-strong">${p.symbol}</td>
                <td><span class="${sideClass}">${p.action}</span></td>
                <td>${Number(p.entry_price).toFixed(4)}</td>
                <td>-</td>
                <td>${p.quantity}</td>
                <td>${uPnlStr}</td>
                <td>${p.sl || '-'}</td>
                <td>${p.tp || '-'}</td>
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
                <td class="td-strong">${p.symbol}</td>
                <td><span class="${sideClass}">${p.action}</span></td>
                <td>${Number(p.entry_price).toFixed(4)}</td>
                <td>${Number(p.exit_price).toFixed(4)}</td>
                <td>${p.quantity}</td>
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
        dailyPnLData = {}; // Reset

        allClosed.forEach(p => {
            if(p.pnl > 0) gross_win += p.pnl;
            else if(p.pnl < 0) gross_loss += Math.abs(p.pnl);
            
            // Group for daily histogram
            const day = p.timestamp ? p.timestamp.substring(0, 10) : 'Unknown';
            if (day !== 'Unknown') {
                if (!dailyPnLData[day]) dailyPnLData[day] = 0;
                dailyPnLData[day] += (p.pnl || 0);
            }
        });
        
        document.getElementById('an-pf').innerText = gross_loss > 0 ? (gross_win / gross_loss).toFixed(2) : (gross_win > 0 ? '∞' : '0.00');
        document.getElementById('an-avg-win').innerText = wins > 0 ? formatCurrency(gross_win / wins) : '$0.00';
        document.getElementById('an-avg-loss').innerText = losses > 0 ? formatCurrency(gross_loss / losses) : '$0.00';

        updateAnalyticsCharts();

    } catch (e) {
        console.error("Failed to fetch trades:", e);
    }
}

let scannerRejectionData = {};

async function fetchScanner() {
    try {
        const data = await apiClient.get('/api/scanner');
        if (!data) return;
        
        // Funnel Pipeline
        document.getElementById('fn-signals').innerText = data.TOTAL_SIGNALS || 0;
        
        const profRej = data.PROFITABILITY_REJECTED || 0;
        const profAcc = Math.max(0, (data.TOTAL_SIGNALS || 0) - profRej);
        document.getElementById('fn-prof-rej').innerText = profRej;
        document.getElementById('fn-prof-acc').innerText = profAcc;
        
        const riskRej = (data.RISK_REJECTED || 0) + (data.COOLDOWN_REJECTED || 0) + (data.JIT_REJECTED || 0) + (data.OTHER_REJECTED || 0);
        const riskAcc = data.QUALIFIED || 0;
        document.getElementById('fn-risk-rej').innerText = riskRej;
        document.getElementById('fn-risk-acc').innerText = riskAcc;
        
        document.getElementById('fn-filled').innerText = data.ORDERS_FILLED || 0;
        
        // Markets Data (Matrix)
        let dataReceivingCount = 0;
        let evaluatedCount = 0;
        const totalSyms = data.symbols ? data.symbols.length : 0;
        const marketRows = [];
        const marketFullRows = [];

        if (data.market_data && Object.keys(data.market_data).length > 0) {
            for (const sym of Object.keys(data.market_data)) {
                dataReceivingCount++;
                const info = data.market_data[sym] || {};
                const tsStr = data.last_market_update ? data.last_market_update[sym] : null;
                const ts = tsStr ? new Date(tsStr).getTime() : 0;
                
                const price = info.close || 0;
                const chg = info.change_24h || 0;
                const chgStr = chg > 0 ? `<span class="val-green">+${chg.toFixed(2)}%</span>` : `<span class="val-red">${chg.toFixed(2)}%</span>`;
                
                marketRows.push(`<tr>
                    <td>${sym}</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                    <td><span class="tag tag-active">CONNECTED</span></td>
                    <td>${formatTime(ts)}</td>
                </tr>`);
                
                marketFullRows.push(`<tr>
                    <td class="td-strong">${sym}</td>
                    <td>${price.toFixed(4)}</td>
                    <td>${chgStr}</td>
                    <td>-</td>
                    <td><span class="tag tag-active">CONNECTED</span></td>
                    <td>${formatTime(ts)}</td>
                    <td>-</td>
                    <td>-</td>
                    <td>-</td>
                </tr>`);
            }
        }
        
        const marketBody = document.getElementById('market-body');
        if(marketBody) {
            if(marketRows.length > 0) marketBody.innerHTML = marketRows.join('');
            else marketBody.innerHTML = '<tr><td colspan="6" class="empty-state">No active symbols scanned</td></tr>';
        }
        
        const marketFullBody = document.getElementById('market-full-body');
        if(marketFullBody) {
            if(marketFullRows.length > 0) marketFullBody.innerHTML = marketFullRows.join('');
            else marketFullBody.innerHTML = '<tr><td colspan="9" class="empty-state">AWAITING MARKET DATA</td></tr>';
        }

        if (data.last_evaluation) {
            for (const sym of Object.keys(data.last_evaluation)) {
                evaluatedCount++;
            }
        }
        
        document.getElementById('sc-eval-ratio').innerText = `${evaluatedCount} Symbols`;
        document.getElementById('fn-mrk').innerText = dataReceivingCount || 0;
        
        // Ticker & Top Movers
        const topMoversBody = document.getElementById('top-movers-body');
        const tickerContent = document.getElementById('bottom-ticker-content');
        
        if (data.market_data && Object.keys(data.market_data).length > 0) {
            let mkts = [];
            for (const [sym, info] of Object.entries(data.market_data)) {
                if (info && info.close !== undefined) {
                    mkts.push({ sym, price: info.close, chg: info.change_24h || 0 });
                }
            }
            if(mkts.length > 0) {
                mkts.sort((a,b) => Math.abs(b.chg) - Math.abs(a.chg));
                
                // Top Movers
                const moverRows = mkts.slice(0, 5).map(m => {
                    const chgStr = m.chg > 0 ? `<span class="val-green">+${m.chg.toFixed(2)}%</span>` : `<span class="val-red">${m.chg.toFixed(2)}%</span>`;
                    return `<tr><td class="td-strong">${m.sym}</td><td>${m.price.toFixed(4)}</td><td>${chgStr}</td></tr>`;
                });
                if(topMoversBody) topMoversBody.innerHTML = moverRows.join('');
                
                // Ticker
                const tickerHtml = mkts.map(m => {
                    const colorClass = m.chg > 0 ? 'val-green' : 'val-red';
                    const sign = m.chg > 0 ? '▲' : '▼';
                    return `<div class="ticker-item"><span class="ticker-sym">${m.sym}</span><span class="ticker-px ${colorClass}">${m.price.toFixed(4)} ${sign} ${Math.abs(m.chg).toFixed(2)}%</span></div>`;
                });
                // Duplicate for smooth loop
                if(tickerContent) tickerContent.innerHTML = tickerHtml.join('') + tickerHtml.join('');
            }
        }
        
        // Strategy Metrics
        const stratBody = document.getElementById('strategy-metrics-body');
        const stratFullBody = document.getElementById('strat-full-body');
        if (data.strategy_metrics && Object.keys(data.strategy_metrics).length > 0) {
            let stratRows = [];
            let stratFullRows = [];
            for (const [strat, m] of Object.entries(data.strategy_metrics)) {
                stratRows.push(`<tr>
                    <td>${strat}</td>
                    <td>${m.signals || 0}</td>
                    <td>${m.qualified || 0}</td>
                    <td>${m.rejected || 0}</td>
                    <td>${m.executed || 0}</td>
                    <td><span class="tag tag-active">ACTIVE</span></td>
                </tr>`);
                stratFullRows.push(`<tr>
                    <td class="td-strong">${strat}</td>
                    <td><span class="tag tag-active">ACTIVE</span></td>
                    <td>-</td>
                    <td>${m.signals || 0}</td>
                    <td>${m.qualified || 0}</td>
                    <td>${m.rejected || 0}</td>
                    <td>${m.executed || 0}</td>
                    <td>${m.executed || 0}</td>
                    <td>-</td>
                    <td>-</td>
                </tr>`);
            }
            if (stratBody) stratBody.innerHTML = stratRows.join('');
            if (stratFullBody) stratFullBody.innerHTML = stratFullRows.join('');
        } else {
            if (stratBody) stratBody.innerHTML = '<tr><td colspan="6" class="empty-state">NO STRATEGY DATA</td></tr>';
            if (stratFullBody) stratFullBody.innerHTML = '<tr><td colspan="10" class="empty-state">NO STRATEGY DATA</td></tr>';
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
            const sideClass = o.side === 'BUY' || o.side === 'LONG' ? 'tag tag-long' : 'tag tag-short';
            const tsShort = o.timestamp ? formatDateTime(o.timestamp) : '-';
            const confStr = o.confidence ? formatPct(o.confidence) : '-';
            const netStr = o.expected_net_return ? (o.expected_net_return > 0 ? `<span class="val-green">+${formatPct(o.expected_net_return)}</span>` : `<span class="val-red">${formatPct(o.expected_net_return)}</span>`) : '-';
            const priceStr = o.current_price ? Number(o.current_price).toFixed(2) : '-';
            const decClass = o.decision === 'ACCEPTED' || o.decision === 'APPROVED' ? 'tag tag-qualified' : 'tag tag-rejected';
            const shortReason = o.reason ? (o.reason.length > 30 ? o.reason.substring(0, 30) + '...' : o.reason) : '-';
            
            const strat = o.strategy || '-';
            const tf = o.timeframe || '-';
            
            if (full) {
                return `<tr>
                    <td>${tsShort}</td><td class="td-strong">${o.symbol}</td><td>${tf}</td><td>${strat}</td><td><span class="${sideClass}">${o.side}</span></td>
                    <td>${priceStr}</td><td>${confStr}</td><td>${netStr}</td>
                    <td>-</td><td>-</td>
                    <td><span class="${decClass}">${o.decision || '-'}</span></td><td title="${o.reason}">${shortReason}</td>
                </tr>`;
            } else {
                const shTs = o.timestamp ? String(o.timestamp).substring(11, 19) : '-';
                return `<tr>
                    <td>${shTs}</td><td class="td-strong">${o.symbol}</td><td>${tf}</td><td>${strat}</td><td><span class="${sideClass}">${o.side}</span></td>
                    <td>${priceStr}</td><td>${confStr}</td><td>${netStr}</td>
                    <td><span class="${decClass}">${o.decision || '-'}</span></td>
                </tr>`;
            }
        };

        if (!data.top_opportunities || data.top_opportunities.length === 0) {
            if(oppBody) oppBody.innerHTML = '<tr><td colspan="6" class="empty-state">NO QUALIFYING OPPORTUNITIES</td></tr>';
            if(sigFullBody) sigFullBody.innerHTML = '<tr><td colspan="12" class="empty-state">NO SIGNALS LOGGED YET</td></tr>';
        } else {
            if(oppBody) oppBody.innerHTML = data.top_opportunities.slice(0, 5).map(o => mapOpp(o, false)).join('');
            if(sigFullBody) sigFullBody.innerHTML = data.top_opportunities.map(o => mapOpp(o, true)).join('');
        }
        // Capture rejection reasons for Analytics
        scannerRejectionData = {
            "Profitability": profRej,
            "Risk/Sizing": data.RISK_REJECTED || 0,
            "Cooldown": data.COOLDOWN_REJECTED || 0,
            "Market Data": data.MARKET_DATA_REJECTED || 0,
            "Execution": data.EXECUTION_REJECTED || 0,
            "Other": data.OTHER_REJECTED || 0
        };
        updateRejectionChart();

    } catch (e) {
        console.error("Failed to fetch scanner stats:", e);
    }
}

let equityChartInst = null;
let pnlHistChartInst = null;
let rejPieChartInst = null;

async function initChart() {
    const ctx = document.getElementById('equityChart');
    if(!ctx) return;
    
    const eqData = await apiClient.get('/api/equity');
    if (!eqData || eqData.length === 0) return; // No data yet

    const labels = eqData.map(p => {
        const d = new Date(p.time);
        return d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
    });
    const data = eqData.map(p => p.equity);

    if (equityChartInst) {
        equityChartInst.data.labels = labels;
        equityChartInst.data.datasets[0].data = data;
        equityChartInst.update('none');
        return;
    }

    equityChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Equity',
                data: data,
                borderColor: '#1e88e5',
                backgroundColor: 'rgba(30, 136, 229, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.1,
                pointRadius: 0,
                pointHitRadius: 10
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: '#192233',
                    titleColor: '#ffffff',
                    bodyColor: '#9cb2c9',
                    borderColor: '#243048',
                    borderWidth: 1
                }
            },
            scales: {
                x: { display: false },
                y: { 
                    display: true, 
                    position: 'right',
                    grid: { color: '#243048' },
                    ticks: {
                        color: '#5e738d',
                        font: { family: "'JetBrains Mono', monospace", size: 9 },
                        callback: function(value) { return '$' + value.toLocaleString(); }
                    },
                    border: { display: false }
                }
            },
            interaction: {
                mode: 'nearest',
                axis: 'x',
                intersect: false
            }
        }
    });
}

function updateAnalyticsCharts() {
    const ctx = document.getElementById('pnlHistChart');
    if (!ctx) return;

    const labels = Object.keys(dailyPnLData).sort();
    const data = labels.map(l => dailyPnLData[l]);
    const bgColors = data.map(v => v >= 0 ? '#10b981' : '#f43f5e');

    if (pnlHistChartInst) {
        pnlHistChartInst.data.labels = labels;
        pnlHistChartInst.data.datasets[0].data = data;
        pnlHistChartInst.data.datasets[0].backgroundColor = bgColors;
        pnlHistChartInst.update('none');
        return;
    }

    pnlHistChartInst = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Daily PnL',
                data: data,
                backgroundColor: bgColors,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#192233',
                    titleColor: '#ffffff',
                    bodyColor: '#9cb2c9',
                    borderColor: '#243048',
                    borderWidth: 1,
                    callbacks: {
                        label: function(ctx) {
                            return formatCurrency(ctx.raw);
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#5e738d', font: { family: "'JetBrains Mono', monospace", size: 10 } }
                },
                y: { 
                    grid: { color: '#243048' },
                    ticks: {
                        color: '#5e738d',
                        font: { family: "'JetBrains Mono', monospace", size: 10 },
                        callback: function(value) { return '$' + value.toLocaleString(); }
                    },
                    border: { display: false }
                }
            }
        }
    });
}

function updateRejectionChart() {
    const ctx = document.getElementById('rejPieChart');
    if (!ctx) return;

    const labels = Object.keys(scannerRejectionData);
    const data = labels.map(l => scannerRejectionData[l]);
    
    // Only draw if there's data
    if (data.reduce((a,b) => a+b, 0) === 0) return;

    if (rejPieChartInst) {
        rejPieChartInst.data.labels = labels;
        rejPieChartInst.data.datasets[0].data = data;
        rejPieChartInst.update('none');
        return;
    }

    rejPieChartInst = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                backgroundColor: ['#1e88e5', '#f43f5e', '#f59e0b', '#8b5cf6', '#64748b', '#cbd5e1'],
                borderWidth: 0,
                hoverOffset: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: {
                legend: {
                    position: 'right',
                    labels: { color: '#9cb2c9', font: { family: "'Inter', sans-serif", size: 12 } }
                }
            }
        }
    });
}

function updateDashboard() {
    Promise.all([
        fetchDashboardData(),
        fetchTrades(),
        fetchScanner(),
        initChart()
    ]).finally(() => {
        // any spinner removal can go here
    });
}

// ==========================================
// INITIALIZATION
// ==========================================
startClockLoop(); 
initChart();
updateDashboard(); 
setInterval(updateDashboard, 2000);
