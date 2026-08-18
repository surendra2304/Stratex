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
// STATE DECLARATIONS
// ==========================================
let rawEquityPoints = [];
let rawOpportunities = [];
let signalFilterState = 'ALL';

function setSignalFilter(filterType) {
    signalFilterState = filterType;
    document.querySelectorAll('.btn-filter').forEach(b => b.classList.remove('active'));
    const bId = filterType === 'ALL' ? 'sig-btn-all' : (filterType === 'ACCEPTED' ? 'sig-btn-acc' : 'sig-btn-rej');
    const btn = document.getElementById(bId);
    if (btn) btn.classList.add('active');
    renderSignalsTable();
}

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
        if (!data) {
            handleDataUnavailable();
            return;
        }
        
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
        document.getElementById('pb-balance').innerText = formatCurrency(data.equity);
        applyColor(document.getElementById('pb-today'), data.today_pnl);
        applyColor(document.getElementById('pb-realized'), data.realized_pnl);
        applyColor(document.getElementById('pb-unrealized'), data.unrealized_pnl);
        document.getElementById('pb-fees').innerText = formatCurrency(data.fees);
        document.getElementById('pb-mdd').innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        
        // Header Status
        const engineDot = document.getElementById('hdr-engine-dot');
        const engineText = document.getElementById('hdr-engine-text');
        if (engineDot && engineText) {
            if (data.safety_halt) {
                engineDot.className = 'dot dot-red';
                engineText.className = 'status-offline';
                engineText.innerText = 'SAFETY HALT';
            } else if (data.engine_status === 'ONLINE' || data.engine_healthy) {
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

        // Overview Account Snapshot Bindings
        const snapBotEq = document.getElementById('snap-bot-equity');
        if (snapBotEq) snapBotEq.innerText = formatCurrency(data.equity);
        
        const snapWallet = document.getElementById('snap-wallet');
        if (snapWallet) snapWallet.innerText = formatCurrency(data.full_wallet_value || (Number(data.equity || 0) + Number(data.unmanaged_assets_value || 0)));
        
        const snapCash = document.getElementById('snap-cash');
        if (snapCash) snapCash.innerText = formatCurrency(data.cash);
        
        const snapManaged = document.getElementById('snap-managed');
        if (snapManaged) snapManaged.innerText = formatCurrency(data.crypto_holdings_value);
        
        const snapPos = document.getElementById('snap-pos');
        if (snapPos) snapPos.innerText = `${data.open_positions || 0} / 5`;
        
        const snapExp = document.getElementById('snap-exposure');
        if (snapExp) {
            const expPct = data.exposure_pct !== undefined ? data.exposure_pct : (data.equity > 0 ? (Number(data.crypto_holdings_value || 0) / Number(data.equity)) * 100 : 0);
            snapExp.innerText = expPct.toFixed(1) + '%';
        }
        
        const snapAvailRisk = document.getElementById('snap-avail-risk');
        if (snapAvailRisk) snapAvailRisk.innerText = (data.available_risk !== undefined ? data.available_risk : 20.0).toFixed(1) + '%';

        // Overview Compact Funnel
        const fnMrk = document.getElementById('fn-mrk');
        if (fnMrk) fnMrk.innerText = data.market_updates_count || data.symbol_count || 13;
        const fnSig = document.getElementById('fn-signals');
        if (fnSig) fnSig.innerText = data.signals_evaluated || data.total_signals || 0;
        const fnProfAcc = document.getElementById('fn-prof-acc');
        if (fnProfAcc) fnProfAcc.innerText = data.signals_accepted_profit || 0;
        const fnProfRej = document.getElementById('fn-prof-rej');
        if (fnProfRej) fnProfRej.innerText = data.signals_rejected_profit || 0;
        const fnRiskAcc = document.getElementById('fn-risk-acc');
        if (fnRiskAcc) fnRiskAcc.innerText = data.signals_accepted_risk || 0;
        const fnRiskRej = document.getElementById('fn-risk-rej');
        if (fnRiskRej) fnRiskRej.innerText = data.signals_rejected_risk || 0;
        const fnExec = document.getElementById('fn-exec');
        if (fnExec) fnExec.innerText = data.orders_submitted || 0;
        const fnFilled = document.getElementById('fn-filled');
        if (fnFilled) fnFilled.innerText = data.orders_filled || 0;

        // Bottom Status Strip
        const btmLastMkt = document.getElementById('btm-last-mkt');
        if (btmLastMkt) btmLastMkt.innerText = data.server_time ? formatTime(data.server_time) : '--:--:--';
        const btmLastStrat = document.getElementById('btm-last-strat');
        if (btmLastStrat) btmLastStrat.innerText = data.last_evaluation ? formatTime(data.last_evaluation) : (data.server_time ? formatTime(data.server_time) : '--:--:--');

        // Capital Allocation Transparency Bar
        const cashVal = Number(data.cash !== undefined ? data.cash : 0);
        const cryptoVal = Number(data.crypto_holdings_value !== undefined ? data.crypto_holdings_value : 0);
        const totalVal = (cashVal + cryptoVal) || Number(data.equity || 0);

        const allocCashTxt = document.getElementById('alloc-cash-txt');
        const allocCryptoTxt = document.getElementById('alloc-crypto-txt');
        const barCash = document.getElementById('alloc-bar-cash');
        const barCrypto = document.getElementById('alloc-bar-crypto');
        const snapExpBadge = document.getElementById('snap-exposure-badge');

        if (totalVal > 0) {
            const cashPct = Math.min(100, Math.max(0, (cashVal / totalVal) * 100));
            const cryptoPct = Math.min(100, Math.max(0, 100 - cashPct));

            if (allocCashTxt) allocCashTxt.innerText = formatCurrency(cashVal);
            if (allocCryptoTxt) allocCryptoTxt.innerText = formatCurrency(cryptoVal);
            if (barCash) barCash.style.width = `${cashPct.toFixed(1)}%`;
            if (barCrypto) barCrypto.style.width = `${cryptoPct.toFixed(1)}%`;
            if (snapExpBadge) snapExpBadge.innerText = `${cryptoPct.toFixed(1)}% EXPOSURE`;
        } else {
            if (allocCashTxt) allocCashTxt.innerText = '--';
            if (allocCryptoTxt) allocCryptoTxt.innerText = '--';
            if (barCash) barCash.style.width = `100%`;
            if (barCrypto) barCrypto.style.width = `0%`;
        }

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
        handleDataUnavailable();
    }
}

async function fetchOpenOrders() {
    try {
        const orders = await apiClient.get('/api/open-orders');
        const ordersBody = document.getElementById('open-orders-body');
        if (!ordersBody) return;

        if (!orders || orders.length === 0) {
            ordersBody.innerHTML = '<tr><td colspan="8" class="empty-state">No open orders on Binance Testnet</td></tr>';
            return;
        }

        ordersBody.innerHTML = orders.map(o => {
            const sideClass = o.side === 'BUY' ? 'tag tag-long' : 'tag tag-short';
            const stopStr = o.stop_price > 0 ? Number(o.stop_price).toFixed(4) : '-';

            return `<tr>
                <td class="td-strong">${o.order_id}</td>
                <td>${o.symbol}</td>
                <td><span class="${sideClass}">${o.side}</span></td>
                <td>${o.type}</td>
                <td>${Number(o.price).toFixed(4)}</td>
                <td class="val-amber">${stopStr}</td>
                <td>${o.orig_qty}</td>
                <td><span class="tag tag-qualified">${o.status}</span></td>
            </tr>`;
        }).join('');
    } catch (e) {
        console.error("Failed to fetch open orders:", e);
    }
}

function handleDataUnavailable() {
    const engineDot = document.getElementById('hdr-engine-dot');
    const engineText = document.getElementById('hdr-engine-text');
    if (engineDot && engineText) {
        engineDot.className = 'dot dot-red';
        engineText.className = 'status-offline';
        engineText.innerText = 'DATA UNAVAILABLE';
    }
    const els = ['pb-balance', 'pb-today', 'pb-realized', 'pb-unrealized', 'rk-daily'];
    els.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.innerText = 'DATA UNAVAILABLE';
            el.className = 'metric-value val-neutral';
        }
    });
}

let dailyPnLData = {}; // For Analytics histogram
let knownTradeIds = new Set();
let isInitialTradesLoad = true;

function showTradeNotification(trade) {
    const container = document.getElementById("trade-toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    const pnl = Number(trade.pnl || 0);
    const isWin = pnl > 0;
    const isLoss = pnl < 0;
    const isBuy = trade.action === "BUY" || trade.action === "LONG";
    
    let toastTypeClass = isBuy ? "toast-buy" : (isLoss ? "toast-loss" : "");
    let badgeClass = isBuy ? "buy" : "sell";
    let pnlDisplay = "";
    
    if (trade.status === "CLOSED") {
        pnlDisplay = `
            <div class="toast-detail-row">
                <span>Net Return:</span>
                <span class="toast-detail-val ${isWin ? 'val-green' : (isLoss ? 'val-red' : '')}">
                    ${isWin ? '+' : ''}${formatCurrency(pnl)}
                </span>
            </div>
        `;
    }

    toast.className = `trade-toast ${toastTypeClass}`;
    toast.innerHTML = `
        <div class="toast-header">
            <div class="toast-title-wrap">
                <span class="toast-badge ${badgeClass}">${trade.action || 'ORDER'}</span>
                <span class="toast-title">${trade.status === 'CLOSED' ? (isWin ? '🎯 Take-Profit Hit / Closed' : '🛑 Stop-Loss Hit / Closed') : '🚀 Order Executed & Filled'}</span>
            </div>
            <button class="toast-close" onclick="this.parentElement.parentElement.remove()">×</button>
        </div>
        <div class="toast-body">
            <div class="toast-detail-row">
                <span>Symbol / Pair:</span>
                <strong style="color: #fff; font-size: 12px;">${trade.symbol}</strong>
            </div>
            <div class="toast-detail-row">
                <span>Execution Price:</span>
                <span class="toast-detail-val">${trade.exit_price ? Number(trade.exit_price).toFixed(4) : Number(trade.entry_price).toFixed(4)} USDT</span>
            </div>
            <div class="toast-detail-row">
                <span>Filled Qty:</span>
                <span class="toast-detail-val">${trade.quantity || '-'}</span>
            </div>
            ${pnlDisplay}
            <div class="toast-detail-row" style="font-size: 9px; color: var(--text-muted); margin-top: 4px; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 4px;">
                <span>Executed At:</span>
                <span>${new Date().toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST</span>
            </div>
        </div>
    `;

    container.appendChild(toast);

    // Auto-remove after 7.5 seconds
    setTimeout(() => {
        toast.classList.add("hide");
        setTimeout(() => {
            if (toast.parentElement) toast.remove();
        }, 400);
    }, 7500);
}

// ==========================================
// 3. SIGNALS TERMINAL LOGIC & FILTERS
// ==========================================
let allRawSignals = [];
let signalQuickFilter = 'ALL';

function setSignalQuickFilter(filterType) {
    signalQuickFilter = filterType;
    document.querySelectorAll('#view-signals .btn-filter').forEach(b => b.classList.remove('active'));
    const btnMap = {
        'ALL': 'sig-btn-all',
        'BUY': 'sig-btn-buy',
        'SELL': 'sig-btn-sell',
        'HOLD': 'sig-btn-hold',
        'ACCEPTED': 'sig-btn-acc',
        'REJECTED': 'sig-btn-rej'
    };
    const bId = btnMap[filterType] || 'sig-btn-all';
    const btn = document.getElementById(bId);
    if (btn) btn.classList.add('active');
    renderSignalsTable();
}

function applySignalFilters() {
    renderSignalsTable();
}

async function fetchSignals() {
    try {
        let signals = [];
        const res = await apiClient.get('/api/signals?limit=500');
        if (res && Array.isArray(res.signals) && res.signals.length > 0) {
            signals = res.signals;
        } else {
            // Fallback: check opportunities log
            const oppData = await apiClient.get('/api/opportunities');
            if (oppData && Array.isArray(oppData.top_opportunities)) {
                signals = oppData.top_opportunities.map(o => ({
                    timestamp: o.timestamp,
                    signal_id: o.signal_id || o.symbol,
                    symbol: o.symbol,
                    timeframe: o.timeframe || '5m',
                    strategy: o.strategy || 'ADX_EMA',
                    decision: o.side || o.decision || 'HOLD',
                    entry: o.current_price || o.entry || 0,
                    stop: o.sl || o.stop || 0,
                    target: o.tp || o.target || 0,
                    confidence: o.confidence || 0,
                    expected_gross: o.expected_gross_return || o.expected_gross || 0,
                    expected_net: o.expected_net_return || o.expected_net || 0,
                    profitability_decision: o.decision === 'ACCEPTED' ? 'ACCEPTED' : (o.decision === 'REJECTED' ? 'REJECTED' : 'PENDING'),
                    profitability_reason: o.reason || '',
                    risk_decision: o.decision === 'ACCEPTED' ? 'ACCEPTED' : (o.decision === 'REJECTED' ? 'REJECTED' : 'PENDING'),
                    risk_reason: o.reason || '',
                    final_decision: o.decision || 'PENDING'
                }));
            }
        }

        allRawSignals = signals;
        updateSignalSummaryKPIs(allRawSignals);
        renderSignalsTable();
        updateDecisionTicker(allRawSignals);
        renderOpportunityScanner(allRawSignals);
    } catch (e) {
        console.error("Failed to fetch signals:", e);
    }
}

function updateDecisionTicker(signals) {
    const track = document.getElementById('decision-ticker-track');
    if (!track) return;

    if (!signals || signals.length === 0) return;

    const items = signals.slice(0, 10).map(s => {
        const sym = s.symbol || 'PAIR';
        const strat = s.strategy || 'ADX_EMA';
        const tf = s.timeframe || '5m';
        const dec = (s.final_decision || s.decision || (s.profitability_decision === 'ACCEPTED' ? 'PASS' : 'REJECT')).toUpperCase();
        const isPass = dec === 'ACCEPTED' || dec === 'PASS' || dec === 'EXECUTED';
        const tagClass = isPass ? 'tag-pass' : 'tag-rej';
        const reason = s.profitability_reason || s.reason || (isPass ? 'Net Alpha > Friction' : 'Threshold Filtered');
        const shortReason = reason.length > 32 ? reason.substring(0, 32) + '...' : reason;

        return `<span class="ticker-item"><strong class="ticker-sym">${sym}</strong> <span class="ticker-strat">${strat}</span> <span class="ticker-tf">${tf}</span> <span class="${tagClass}">${isPass ? 'PASS' : 'REJECT'}: ${shortReason}</span></span>`;
    });

    if (items.length > 0) {
        track.innerHTML = items.join('<span class="ticker-sep">•</span>') + '<span class="ticker-sep">•</span>' + items.join('<span class="ticker-sep">•</span>');
    }
}

function renderOpportunityScanner(signals) {
    const oppBody = document.getElementById('opp-short-body');
    if (!oppBody) return;

    if (!signals || signals.length === 0) {
        oppBody.innerHTML = `<tr><td colspan="5" class="idle-state-row"><div class="idle-state-content"><span>Market in consolidation • Waiting for net alpha &gt; friction hurdle</span></div></td></tr>`;
        return;
    }

    oppBody.innerHTML = signals.slice(0, 5).map((s, idx) => {
        const sym = s.symbol || '-';
        const tf = s.timeframe || '5m';
        const strat = s.strategy || 'ADX_EMA';
        const netVal = Number(s.expected_net || s.expected_net_return || 0);
        const netStr = netVal !== 0 ? `<span class="${netVal > 0 ? 'profit' : 'loss'}">${netVal > 0 ? '+' : ''}${(netVal * 100).toFixed(2)}%</span>` : '-';
        const dec = (s.final_decision || s.decision || (s.profitability_decision === 'ACCEPTED' ? 'PASS' : 'REJECT')).toUpperCase();
        const isPass = dec === 'ACCEPTED' || dec === 'PASS' || dec === 'EXECUTED';
        const tagClass = isPass ? 'tag-pass' : 'tag-rej';

        return `<tr onclick="inspectSignalByIndex(${idx})" style="cursor: pointer;">
            <td class="td-strong">${sym}</td>
            <td>${tf}</td>
            <td class="cyan">${strat}</td>
            <td>${netStr}</td>
            <td><span class="${tagClass}">${isPass ? 'PASS' : 'REJECT'}</span></td>
        </tr>`;
    }).join('');
}

function updateSignalSummaryKPIs(signals) {
    let total = signals.length;
    let buy = 0, sell = 0, hold = 0;
    let profAcc = 0, profRej = 0;
    let riskAcc = 0, riskRej = 0;

    signals.forEach(s => {
        const side = (s.decision || s.side || '').toUpperCase();
        if (side === 'BUY' || side === 'LONG') buy++;
        else if (side === 'SELL' || side === 'SHORT') sell++;
        else hold++;

        const pDec = (s.profitability_decision || '').toUpperCase();
        if (pDec === 'ACCEPTED') profAcc++;
        else if (pDec === 'REJECTED') profRej++;

        const rDec = (s.risk_decision || '').toUpperCase();
        if (rDec === 'ACCEPTED') riskAcc++;
        else if (rDec === 'REJECTED') riskRej++;
    });

    const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.innerText = v;
    };

    setVal('sig-kpi-total', total);
    setVal('sig-kpi-buy', buy);
    setVal('sig-kpi-sell', sell);
    setVal('sig-kpi-hold', hold);
    setVal('sig-kpi-prof-acc', profAcc);
    setVal('sig-kpi-prof-rej', profRej);
    setVal('sig-kpi-risk-acc', riskAcc);
    setVal('sig-kpi-risk-rej', riskRej);
}

function renderSignalsTable() {
    const tbody = document.getElementById('signals-full-body');
    if (!tbody) return;

    if (!allRawSignals || allRawSignals.length === 0) {
        tbody.innerHTML = '<tr><td colspan="15" class="idle-state-row">Evaluating 13 pairs across 6 multi-timeframe strategies • Awaiting setups exceeding minimum edge threshold</td></tr>';
        return;
    }

    const stratFilter = (document.getElementById('sig-filter-strat')?.value || 'ALL').toUpperCase();
    const tfFilter = (document.getElementById('sig-filter-tf')?.value || 'ALL').toLowerCase();
    const symFilter = (document.getElementById('sig-filter-sym')?.value || '').trim().toUpperCase();
    const rangeFilter = document.getElementById('sig-filter-range')?.value || 'ALL';

    const now = Date.now();
    const rangeMsMap = {
        '1h': 3600 * 1000,
        '6h': 6 * 3600 * 1000,
        '24h': 24 * 3600 * 1000,
        '7d': 7 * 86400 * 1000
    };

    let filtered = allRawSignals.filter(s => {
        // Quick button filter
        const side = (s.decision || s.side || '').toUpperCase();
        const finalDec = (s.final_decision || s.decision || '').toUpperCase();
        const pDec = (s.profitability_decision || '').toUpperCase();
        const rDec = (s.risk_decision || '').toUpperCase();

        if (signalQuickFilter === 'BUY' && side !== 'BUY' && side !== 'LONG') return false;
        if (signalQuickFilter === 'SELL' && side !== 'SELL' && side !== 'SHORT') return false;
        if (signalQuickFilter === 'HOLD' && side !== 'HOLD') return false;
        if (signalQuickFilter === 'ACCEPTED' && finalDec !== 'ACCEPTED' && finalDec !== 'EXECUTED' && pDec !== 'ACCEPTED' && rDec !== 'ACCEPTED') return false;
        if (signalQuickFilter === 'REJECTED' && finalDec !== 'REJECTED' && pDec !== 'REJECTED' && rDec !== 'REJECTED') return false;

        // Additional filters
        if (stratFilter !== 'ALL' && (s.strategy || '').toUpperCase() !== stratFilter) return false;
        if (tfFilter !== 'ALL' && (s.timeframe || '').toLowerCase() !== tfFilter) return false;
        if (symFilter && !(s.symbol || '').toUpperCase().includes(symFilter)) return false;

        if (rangeFilter !== 'ALL' && rangeMsMap[rangeFilter]) {
            const tsMs = s.timestamp ? new Date(s.timestamp).getTime() : 0;
            if (tsMs > 0 && (now - tsMs) > rangeMsMap[rangeFilter]) return false;
        }

        return true;
    });

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="15" class="idle-state-row">No signals matching active filter criteria • Adjust filter parameters to widen search window</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map((s, idx) => {
        const timeStr = s.timestamp ? formatTime(s.timestamp) : '-';
        const sym = s.symbol || '-';
        const tf = s.timeframe || '5m';
        const strat = s.strategy || 'ADX_EMA';
        const side = (s.decision || s.side || 'HOLD').toUpperCase();
        const sideClass = (side === 'BUY' || side === 'LONG') ? 'tag tag-long' : ((side === 'SELL' || side === 'SHORT') ? 'tag tag-short' : 'tag tag-neutral');

        const entryStr = Number(s.entry || s.current_price || 0) > 0 ? Number(s.entry || s.current_price).toFixed(4) : '-';
        const stopStr = Number(s.stop || s.sl || 0) > 0 ? Number(s.stop || s.sl).toFixed(4) : '-';
        const targetStr = Number(s.target || s.tp || 0) > 0 ? Number(s.target || s.tp).toFixed(4) : '-';
        const confStr = Number(s.confidence || 0) > 0 ? (Number(s.confidence) * 100).toFixed(1) + '%' : '-';

        const grossVal = Number(s.expected_gross || s.expected_gross_return || 0);
        const grossStr = grossVal !== 0 ? (grossVal > 0 ? `+${(grossVal * 100).toFixed(2)}%` : `${(grossVal * 100).toFixed(2)}%`) : '-';

        const netVal = Number(s.expected_net || s.expected_net_return || 0);
        const netClass = netVal > 0 ? 'val-green' : (netVal < 0 ? 'val-red' : '');
        const netStr = netVal !== 0 ? `<span class="${netClass}">${netVal > 0 ? '+' : ''}${(netVal * 100).toFixed(2)}%</span>` : '-';

        const pDec = (s.profitability_decision || 'PENDING').toUpperCase();
        const pClass = pDec === 'ACCEPTED' ? 'tag tag-qualified' : (pDec === 'REJECTED' ? 'tag tag-rejected' : 'tag tag-neutral');

        const rDec = (s.risk_decision || 'PENDING').toUpperCase();
        const rClass = rDec === 'ACCEPTED' ? 'tag tag-qualified' : (rDec === 'REJECTED' ? 'tag tag-rejected' : 'tag tag-neutral');

        const fDec = (s.final_decision || (pDec === 'ACCEPTED' && rDec === 'ACCEPTED' ? 'EXECUTED' : 'REJECTED')).toUpperCase();
        const fClass = (fDec === 'EXECUTED' || fDec === 'ACCEPTED') ? 'tag tag-win' : 'tag tag-loss';

        const reason = s.profitability_reason || s.risk_reason || s.reason || '-';
        const shortReason = reason.length > 28 ? reason.substring(0, 28) + '...' : reason;

        return `<tr style="cursor: pointer;" onclick="inspectSignalByIndex(${idx})" title="Click to open Signal Inspector">
            <td>${timeStr}</td>
            <td class="td-strong">${sym}</td>
            <td>${tf}</td>
            <td>${strat}</td>
            <td><span class="${sideClass}">${side}</span></td>
            <td>${entryStr}</td>
            <td class="val-red">${stopStr}</td>
            <td class="val-green">${targetStr}</td>
            <td>${confStr}</td>
            <td>${grossStr}</td>
            <td>${netStr}</td>
            <td><span class="${pClass}">${pDec}</span></td>
            <td><span class="${rClass}">${rDec}</span></td>
            <td><span class="${fClass}">${fDec}</span></td>
            <td title="${reason}">${shortReason}</td>
        </tr>`;
    }).join('');
}

function inspectSignalByIndex(idx) {
    if (allRawSignals && allRawSignals[idx]) {
        inspectSignal(allRawSignals[idx]);
    }
}

function inspectSignal(sig) {
    const sym = sig.symbol || 'SYSTEM';
    const side = (sig.decision || sig.side || 'HOLD').toUpperCase();
    const strat = sig.strategy || 'ADX_EMA';
    const tf = sig.timeframe || '5m';
    const conf = Number(sig.confidence || 0);

    const gross = Number(sig.expected_gross || sig.expected_gross_return || 0);
    const net = Number(sig.expected_net || sig.expected_net_return || 0);
    const fees = 0.001; // 0.1% spot fee
    const slippage = 0.0005; // 0.05% est. slippage

    const pDec = (sig.profitability_decision || 'PENDING').toUpperCase();
    const pReason = sig.profitability_reason || (pDec === 'ACCEPTED' ? 'Expected Net Edge > Friction Costs' : 'Fails minimal net edge requirement');

    const rDec = (sig.risk_decision || 'PENDING').toUpperCase();
    const rReason = sig.risk_reason || (rDec === 'ACCEPTED' ? 'Exposure and daily loss limits respected' : 'Risk limit or exposure ceiling exceeded');

    const finalDec = (sig.final_decision || (pDec === 'ACCEPTED' && rDec === 'ACCEPTED' ? 'EXECUTED' : 'REJECTED')).toUpperCase();
    const isExecuted = finalDec === 'EXECUTED' || finalDec === 'ACCEPTED';

    let whyNoOrderHtml = '';
    if (isExecuted) {
        whyNoOrderHtml = `
            <div class="decision-banner accepted">
                <div class="decision-banner-title">✅ ORDER QUALIFIED & SUBMITTED</div>
                <div class="decision-banner-body">All quantitative risk gates and profitability revalidation checks passed. Order dispatched to exchange.</div>
            </div>
        `;
    } else {
        const rejectionGate = pDec === 'REJECTED' ? 'Profitability Gate' : (rDec === 'REJECTED' ? 'Risk Gate' : 'Safety / Validation Filter');
        const rejectionReason = sig.risk_reason || sig.profitability_reason || sig.reason || 'Criteria not met';
        whyNoOrderHtml = `
            <div class="decision-banner rejected">
                <div class="decision-banner-title">🚫 NO ORDER TAKEN • REJECTED</div>
                <div class="decision-banner-body">
                    <strong>Rejection Gate:</strong> ${rejectionGate}<br>
                    <strong>Reason:</strong> ${rejectionReason}<br>
                    Capital protected from negative expected edge or risk limit breach.
                </div>
            </div>
        `;
    }

    const drawerHtml = `
        <!-- DECISION BANNER -->
        ${whyNoOrderHtml}

        <!-- 1. SIGNAL OVERVIEW -->
        <div class="inspector-card">
            <div class="inspector-card-header">
                <span>📡 Signal Overview</span>
                <span class="badge badge-mono">${sig.signal_id || 'SIG'}</span>
            </div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Signal Time</span><span class="inspector-val">${sig.timestamp ? formatDateTime(sig.timestamp) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Symbol</span><span class="inspector-val td-strong">${sym}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Timeframe</span><span class="inspector-val">${tf}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Strategy</span><span class="inspector-val">${strat}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Intended Side</span><span class="inspector-val"><span class="tag ${side === 'BUY' ? 'tag-long' : 'tag-short'}">${side}</span></span></div>
                <div class="inspector-row"><span class="inspector-lbl">Confidence</span><span class="inspector-val">${conf > 0 ? (conf * 100).toFixed(1) + '%' : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Entry Price</span><span class="inspector-val">${Number(sig.entry || sig.current_price || 0).toFixed(4)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Stop Loss</span><span class="inspector-val val-red">${Number(sig.stop || sig.sl || 0) > 0 ? Number(sig.stop || sig.sl).toFixed(4) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Take Profit</span><span class="inspector-val val-green">${Number(sig.target || sig.tp || 0) > 0 ? Number(sig.target || sig.tp).toFixed(4) : '-'}</span></div>
            </div>
        </div>

        <!-- 2. EXPECTED EDGE & COSTS -->
        <div class="inspector-card">
            <div class="inspector-card-header"><span>💵 Expected Edge & Friction</span></div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Expected Gross</span><span class="inspector-val">${gross !== 0 ? (gross * 100).toFixed(2) + '%' : '0.00%'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Exchange Fees (0.1%)</span><span class="inspector-val val-amber">-${(fees * 100).toFixed(2)}%</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Est. Slippage (0.05%)</span><span class="inspector-val val-amber">-${(slippage * 100).toFixed(2)}%</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Expected Net Edge</span><span class="inspector-val ${net > 0 ? 'val-green' : 'val-red'}">${net > 0 ? '+' : ''}${(net * 100).toFixed(2)}%</span></div>
            </div>
        </div>

        <!-- 3. GATE DECISIONS -->
        <div class="inspector-card">
            <div class="inspector-card-header"><span>🛡️ Multi-Gate Audit Evaluation</span></div>
            <div class="inspector-row">
                <span class="inspector-lbl">Profitability Gate:</span>
                <span class="inspector-val"><span class="tag ${pDec === 'ACCEPTED' ? 'tag-qualified' : 'tag-rejected'}">${pDec}</span></span>
            </div>
            <div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 6px;">${pReason}</div>

            <div class="inspector-row">
                <span class="inspector-lbl">Risk & Exposure Gate:</span>
                <span class="inspector-val"><span class="tag ${rDec === 'ACCEPTED' ? 'tag-qualified' : 'tag-rejected'}">${rDec}</span></span>
            </div>
            <div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 6px;">${rReason}</div>

            <div class="inspector-row">
                <span class="inspector-lbl">Execution Status:</span>
                <span class="inspector-val"><span class="tag ${isExecuted ? 'tag-win' : 'tag-loss'}">${finalDec}</span></span>
            </div>
        </div>
    `;

    openInspectorDrawer(`SIGNAL INSPECTOR • ${sym}`, drawerHtml);
}


// ==========================================
// 4. POSITIONS TERMINAL & INSPECTOR
// ==========================================
let allRawPositions = [];

async function fetchPositions() {
    try {
        let openPositions = [];
        const res = await apiClient.get('/api/positions?status=OPEN');
        if (res && Array.isArray(res.positions)) {
            openPositions = res.positions.filter(p => p.status === 'OPEN');
        }

        // Also check if /api/trades has active positions
        if (openPositions.length === 0) {
            const tradesRes = await apiClient.get('/api/trades');
            if (tradesRes && Array.isArray(tradesRes.positions)) {
                openPositions = tradesRes.positions.filter(p => p.status === 'OPEN');
            }
        }

        allRawPositions = openPositions;
        renderPositionsTable(openPositions);
    } catch (e) {
        console.error("Failed to fetch positions:", e);
    }
}

function renderPositionsTable(positions) {
    const fullBody = document.getElementById('pos-full-body');
    const dashBody = document.getElementById('active-pos-body');

    let totalExposure = 0;
    let totalUnrealized = 0;

    if (!positions || positions.length === 0) {
        if (fullBody) fullBody.innerHTML = '<tr><td colspan="13" class="idle-state-row">No active positions on Binance Testnet</td></tr>';
        if (dashBody) dashBody.innerHTML = '<tr><td colspan="6" class="idle-state-row"><div class="idle-state-content"><span class="radar-pulse"></span><span>All 5 execution slots available • Continuous scanning active across 13 spot pairs</span></div></td></tr>';
        const posCountEl = document.getElementById('pos-count');
        if (posCountEl) posCountEl.innerText = '0 / 5';
        const posNotionalEl = document.getElementById('pos-notional');
        if (posNotionalEl) posNotionalEl.innerText = '$0.00';
        const posUpnlEl = document.getElementById('pos-unrealized');
        if (posUpnlEl) {
            posUpnlEl.innerText = '$0.00';
            posUpnlEl.className = 'kpi-val mono';
        }
        return;
    }

    const fullRows = positions.map((p, idx) => {
        const sym = p.symbol || '-';
        const tf = p.timeframe || '5m';
        const strat = p.strategy || 'ADX_EMA';
        const side = (p.side || p.action || 'BUY').toUpperCase();
        const sideClass = (side === 'BUY' || side === 'LONG') ? 'tag tag-long' : 'tag tag-short';

        const entryPx = Number(p.entry_price || p.price || 0);
        const currPx = Number(p.current_price || p.entry_price || 0);
        const qty = Number(p.quantity || 0);
        const val = qty * (currPx || entryPx);
        totalExposure += val;

        const uPnl = Number(p.current_unrealized_pnl || p.pnl || 0);
        totalUnrealized += uPnl;
        const uPnlClass = uPnl > 0 ? 'profit' : (uPnl < 0 ? 'loss' : '');
        const uPnlStr = `<span class="${uPnlClass}">${uPnl >= 0 ? '+' : ''}${formatCurrency(uPnl)}</span>`;

        const slStr = Number(p.stop_loss || p.sl || 0) > 0 ? Number(p.stop_loss || p.sl).toFixed(4) : '-';
        const tpStr = Number(p.take_profit || p.tp || 0) > 0 ? Number(p.take_profit || p.tp).toFixed(4) : '-';

        // Duration calculation
        let durStr = '-';
        if (p.entry_timestamp || p.timestamp) {
            const entryMs = new Date(p.entry_timestamp || p.timestamp).getTime();
            const diffSec = Math.max(0, Math.floor((Date.now() - entryMs) / 1000));
            const m = Math.floor(diffSec / 60);
            const s = diffSec % 60;
            durStr = `${m}m ${s}s`;
        }

        return `<tr style="cursor: pointer;" onclick="inspectPositionByIndex(${idx})" title="Click to inspect Position details">
            <td class="td-strong">${sym}</td>
            <td>${tf}</td>
            <td>${strat}</td>
            <td><span class="${sideClass}">${side}</span></td>
            <td>${entryPx.toFixed(4)}</td>
            <td>${currPx.toFixed(4)}</td>
            <td>${qty}</td>
            <td>${formatCurrency(val)}</td>
            <td>${uPnlStr}</td>
            <td class="loss">${slStr}</td>
            <td class="profit">${tpStr}</td>
            <td>${durStr}</td>
            <td><span class="tag-pass">OPEN</span></td>
        </tr>`;
    }).join('');

    const dashRows = positions.map((p, idx) => {
        const sym = p.symbol || '-';
        const strat = p.strategy || 'ADX_EMA';
        const tf = p.timeframe || '5m';
        const entryPx = Number(p.entry_price || 0);
        const currPx = Number(p.current_price || entryPx);
        const uPnl = Number(p.current_unrealized_pnl || p.pnl || 0);
        const uPnlStr = `<span class="${uPnl >= 0 ? 'profit' : 'loss'}">${uPnl >= 0 ? '+' : ''}${formatCurrency(uPnl)}</span>`;
        const sl = Number(p.stop_loss || p.sl || 0) > 0 ? '$' + Number(p.stop_loss || p.sl).toFixed(4) : '-';
        const tp = Number(p.take_profit || p.tp || 0) > 0 ? '$' + Number(p.take_profit || p.tp).toFixed(4) : '-';

        return `<tr onclick="inspectPositionByIndex(${idx})" style="cursor: pointer;">
            <td class="td-strong">${sym}</td>
            <td><span class="cyan">${strat}</span> <span style="font-size: 8px; color: var(--text-muted);">(${tf})</span></td>
            <td>$${entryPx.toFixed(4)}</td>
            <td>$${currPx.toFixed(4)}</td>
            <td><span class="loss">${sl}</span> / <span class="profit">${tp}</span></td>
            <td class="mono">${uPnlStr}</td>
        </tr>`;
    }).join('');

    if (fullBody) fullBody.innerHTML = fullRows;
    if (dashBody) dashBody.innerHTML = dashRows;

    const posCountEl = document.getElementById('pos-count');
    if (posCountEl) posCountEl.innerText = `${positions.length} / 5`;
    const posNotionalEl = document.getElementById('pos-notional');
    if (posNotionalEl) posNotionalEl.innerText = formatCurrency(totalExposure);
    const posUpnlEl = document.getElementById('pos-unrealized');
    if (posUpnlEl) {
        posUpnlEl.innerText = `${totalUnrealized >= 0 ? '+' : ''}${formatCurrency(totalUnrealized)}`;
        posUpnlEl.className = `kpi-val mono ${totalUnrealized >= 0 ? 'profit' : 'loss'}`;
    }
}

function inspectPositionByIndex(idx) {
    if (allRawPositions && allRawPositions[idx]) {
        inspectPosition(allRawPositions[idx]);
    }
}

async function inspectPosition(pos) {
    const sym = pos.symbol || '-';
    const entryPx = Number(pos.entry_price || 0);
    const currPx = Number(pos.current_price || entryPx);
    const qty = Number(pos.quantity || 0);
    const uPnl = Number(pos.current_unrealized_pnl || pos.pnl || 0);
    const tradeId = pos.trade_id || pos.position_id || sym;

    // Fetch working orders to see if any OCO protection is attached
    let workingOrdersHtml = '<div style="font-size: 10px; color: var(--text-muted);">No open OCO working orders attached</div>';
    try {
        const orders = await apiClient.get('/api/open-orders');
        if (orders && Array.isArray(orders)) {
            const matched = orders.filter(o => o.symbol === sym);
            if (matched.length > 0) {
                workingOrdersHtml = matched.map(o => `
                    <div class="inspector-row">
                        <span class="inspector-lbl">${o.type} (${o.side}):</span>
                        <span class="inspector-val">${Number(o.price || o.stop_price || 0).toFixed(4)} (Qty: ${o.orig_qty})</span>
                    </div>
                `).join('');
            }
        }
    } catch (e) {
        console.warn("Error fetching position working orders:", e);
    }

    let durStr = '-';
    if (pos.entry_timestamp || pos.timestamp) {
        const entryMs = new Date(pos.entry_timestamp || pos.timestamp).getTime();
        const diffSec = Math.max(0, Math.floor((Date.now() - entryMs) / 1000));
        const m = Math.floor(diffSec / 60);
        const s = diffSec % 60;
        durStr = `${m}m ${s}s`;
    }

    const drawerHtml = `
        <div class="inspector-card">
            <div class="inspector-card-header">
                <span>💼 Position Details</span>
                <span class="tag tag-qualified">ACTIVE</span>
            </div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Trade ID</span><span class="inspector-val">${tradeId}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Symbol</span><span class="inspector-val td-strong">${sym}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Strategy</span><span class="inspector-val">${pos.strategy || 'ADX_EMA'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Timeframe</span><span class="inspector-val">${pos.timeframe || '5m'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Entry Time</span><span class="inspector-val">${pos.entry_timestamp ? formatDateTime(pos.entry_timestamp) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Active Duration</span><span class="inspector-val">${durStr}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Position Quantity</span><span class="inspector-val">${qty}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Current Notional</span><span class="inspector-val">${formatCurrency(qty * currPx)}</span></div>
            </div>
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>💵 Pricing & Protection</span></div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Entry Price</span><span class="inspector-val">${entryPx.toFixed(4)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Current Price</span><span class="inspector-val">${currPx.toFixed(4)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Stop Loss</span><span class="inspector-val val-red">${Number(pos.stop_loss || pos.sl || 0) > 0 ? Number(pos.stop_loss || pos.sl).toFixed(4) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Take Profit</span><span class="inspector-val val-green">${Number(pos.take_profit || pos.tp || 0) > 0 ? Number(pos.take_profit || pos.tp).toFixed(4) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Unrealized PnL</span><span class="inspector-val ${uPnl >= 0 ? 'val-green' : 'val-red'}">${uPnl >= 0 ? '+' : ''}${formatCurrency(uPnl)}</span></div>
            </div>
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>🛡️ On-Exchange Working Protection Orders</span></div>
            ${workingOrdersHtml}
        </div>
    `;

    openInspectorDrawer(`POSITION INSPECTOR • ${sym}`, drawerHtml);
}
// ==========================================
// 5. TRADE JOURNAL TERMINAL & DAY-BY-DAY AUDIT
// ==========================================
let allRawTrades = [];
let journalViewMode = 'table'; // 'table' or 'calendar'
let journalStatusFilter = 'ALL'; // 'ALL', 'OPEN', 'CLOSED'
let currentCalendarDate = new Date();
let selectedDayFilter = null;

function setJournalViewMode(mode) {
    journalViewMode = mode;
    const btnTable = document.getElementById('btn-journal-view-table');
    const btnCal = document.getElementById('btn-journal-view-cal');
    const tableContainer = document.getElementById('journal-table-view-container');
    const calContainer = document.getElementById('journal-calendar-view-container');

    if (mode === 'table') {
        if (btnTable) btnTable.classList.add('active');
        if (btnCal) btnCal.classList.remove('active');
        if (tableContainer) tableContainer.style.display = 'block';
        if (calContainer) calContainer.style.display = 'none';
    } else {
        if (btnTable) btnTable.classList.remove('active');
        if (btnCal) btnCal.classList.add('active');
        if (tableContainer) tableContainer.style.display = 'none';
        if (calContainer) calContainer.style.display = 'block';
        renderCalendarHeatmap();
    }
}

function setJournalStatusFilter(status) {
    journalStatusFilter = status;
    document.querySelectorAll('.journal-filter-toolbar .btn-filter').forEach(b => {
        if (b.id && b.id.startsWith('btn-jnl-')) b.classList.remove('active');
    });
    const btn = document.getElementById(`btn-jnl-${status.toLowerCase()}`);
    if (btn) btn.classList.add('active');
    renderTradeJournal();
}

function applyJournalFilters() {
    renderTradeJournal();
}

async function fetchTrades() {
    try {
        let trades = [];
        const res = await apiClient.get('/api/trade-history');
        if (res && Array.isArray(res.trades)) {
            trades = res.trades;
        }

        if (trades.length === 0) {
            const legRes = await apiClient.get('/api/trades');
            if (legRes && Array.isArray(legRes.positions)) {
                trades = legRes.positions;
            }
        }

        allRawTrades = trades;
        renderTradeJournal();
    } catch (e) {
        console.error("Failed to fetch trade journal data:", e);
    }
}

function renderTradeJournal() {
    // 1. Separate into Active vs Closed
    const activePositions = allRawPositions || [];
    const closedTrades = allRawTrades.filter(t => t.status === 'CLOSED' || (t.exit_price && Number(t.exit_price) > 0));

    // Update Header / Nav Counts
    const openCountEl = document.getElementById('jnl-open-count');
    if (openCountEl) openCountEl.innerText = activePositions.length;
    const closedCountEl = document.getElementById('jnl-closed-count');
    if (closedCountEl) closedCountEl.innerText = closedTrades.length;
    const navBadge = document.getElementById('nav-open-trades-badge');
    if (navBadge) navBadge.innerText = `${activePositions.length} OPEN`;

    // 2. Summary Metric Cards
    let totalClosed = closedTrades.length;
    let wins = 0, losses = 0;
    let grossWin = 0, grossLoss = 0;
    let totalFees = 0;
    let netPnL = 0;

    closedTrades.forEach(t => {
        const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl !== undefined ? t.pnl : 0));
        const fee = Number(t.fees || t.total_fees || 0);
        netPnL += net;
        totalFees += fee;
        if (net > 0) {
            wins++;
            grossWin += net;
        } else if (net < 0) {
            losses++;
            grossLoss += Math.abs(net);
        }
    });

    const winRate = totalClosed > 0 ? (wins / totalClosed) * 100 : 0;
    const avgWin = wins > 0 ? grossWin / wins : 0;
    const avgLoss = losses > 0 ? grossLoss / losses : 0;
    const pf = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? 999.0 : 0);

    const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.innerText = v;
    };

    setVal('trd-total', totalClosed + activePositions.length);
    setVal('trd-winrate', winRate.toFixed(1) + '%');
    const netEl = document.getElementById('trd-net-pnl');
    if (netEl) {
        netEl.innerText = `${netPnL >= 0 ? '+' : ''}${formatCurrency(netPnL)}`;
        netEl.className = `journal-metric-val mono ${netPnL >= 0 ? 'profit' : 'loss'}`;
    }
    setVal('trd-pf', pf.toFixed(2));
    setVal('trd-fees', formatCurrency(totalFees));
    const avgWinEl = document.getElementById('trd-avg-win');
    if (avgWinEl) avgWinEl.innerText = `+${formatCurrency(avgWin)}`;
    const avgLossEl = document.getElementById('trd-avg-loss');
    if (avgLossEl) avgLossEl.innerText = `-${formatCurrency(avgLoss)}`;

    // 3. Render Section 1: Active Trades
    const activeSection = document.getElementById('journal-active-section');
    const activeBody = document.getElementById('journal-active-tbody');
    const slotsBadge = document.getElementById('journal-active-slots-badge');
    if (slotsBadge) slotsBadge.innerText = `${activePositions.length} / 5 SLOTS IN USE`;

    if (journalStatusFilter === 'CLOSED') {
        if (activeSection) activeSection.style.display = 'none';
    } else {
        if (activeSection) activeSection.style.display = 'block';
        if (activeBody) {
            if (activePositions.length === 0) {
                activeBody.innerHTML = `<tr><td colspan="11" class="idle-state-row"><div class="idle-state-content"><span class="radar-pulse"></span><span>All 5 execution slots available • Continuous scanning active across 13 spot pairs</span></div></td></tr>`;
            } else {
                activeBody.innerHTML = activePositions.map((p, idx) => {
                    const sym = p.symbol || '-';
                    const side = (p.side || p.action || 'BUY').toUpperCase();
                    const sideClass = side === 'BUY' ? 'tag-buy' : 'tag-sell';
                    const strat = p.strategy || 'ADX_EMA';
                    const tf = p.timeframe || '5m';
                    const entryPx = Number(p.entry_price || p.price || 0);
                    const markPx = Number(p.current_price || entryPx);
                    const qty = Number(p.quantity || 0);
                    const notional = qty * (markPx || entryPx);
                    const sl = Number(p.stop_loss || p.sl || 0) > 0 ? '$' + Number(p.stop_loss || p.sl).toFixed(4) : '-';
                    const tp = Number(p.take_profit || p.tp || 0) > 0 ? '$' + Number(p.take_profit || p.tp).toFixed(4) : '-';
                    const uPnl = Number(p.current_unrealized_pnl || p.pnl || 0);
                    const uPnlPct = entryPx > 0 ? ((markPx - entryPx) / entryPx) * 100 : 0;
                    const uPnlStr = `<span class="${uPnl >= 0 ? 'profit' : 'loss'} td-strong">${uPnl >= 0 ? '+' : ''}${formatCurrency(uPnl)} (${uPnlPct >= 0 ? '+' : ''}${uPnlPct.toFixed(2)}%)</span>`;
                    const openTimeStr = p.entry_timestamp || p.timestamp ? formatTime(p.entry_timestamp || p.timestamp) : '-';
                    const durStr = p.duration || (p.entry_timestamp ? calcDurationStr(p.entry_timestamp) : '-');
                    const balEntryStr = p.cash_before_entry ? formatCurrency(p.cash_before_entry) : '-';

                    return `<tr style="cursor: pointer;" onclick="inspectTradeLifecycle(${JSON.stringify(p).replace(/"/g, '&quot;')})">
                        <td>${openTimeStr}</td>
                        <td><strong class="td-strong" style="color: #fff;">${sym}</strong> <span class="${sideClass}">${side}</span></td>
                        <td><span class="cyan">${strat}</span> <span style="color: var(--text-muted); font-size: 10px;">(${tf})</span></td>
                        <td>$${entryPx.toFixed(4)}</td>
                        <td>$${markPx.toFixed(4)}</td>
                        <td>${qty} <span style="color: var(--text-muted); font-size: 10px;">(${formatCurrency(notional)})</span></td>
                        <td><span class="loss">${sl}</span> / <span class="profit">${tp}</span></td>
                        <td>${uPnlStr}</td>
                        <td>${durStr}</td>
                        <td>${balEntryStr}</td>
                        <td><button class="btn-terminal-pill" style="height: 24px; padding: 2px 8px; color: var(--accent-primary);" onclick="event.stopPropagation(); inspectTradeLifecycle(${JSON.stringify(p).replace(/"/g, '&quot;')})">VIEW</button></td>
                    </tr>`;
                }).join('');
            }
        }
    }

    // 4. Render Section 2: Closed Trades Grouped by Day
    const closedSection = document.getElementById('journal-closed-section');
    const daysContainer = document.getElementById('journal-days-container');

    if (journalStatusFilter === 'OPEN') {
        if (closedSection) closedSection.style.display = 'none';
        return;
    } else {
        if (closedSection) closedSection.style.display = 'block';
    }

    if (!daysContainer) return;

    // Filters
    const stratFilter = (document.getElementById('journal-filter-strat')?.value || 'ALL').toUpperCase();
    const tfFilter = (document.getElementById('journal-filter-tf')?.value || 'ALL').toLowerCase();
    const searchFilter = (document.getElementById('journal-filter-search')?.value || '').trim().toUpperCase();

    let filteredClosed = closedTrades.filter(t => {
        if (stratFilter !== 'ALL' && (t.strategy || '').toUpperCase() !== stratFilter) return false;
        if (tfFilter !== 'ALL' && (t.timeframe || '').toLowerCase() !== tfFilter) return false;
        if (searchFilter) {
            const matchSym = (t.symbol || '').toUpperCase().includes(searchFilter);
            const matchStrat = (t.strategy || '').toUpperCase().includes(searchFilter);
            const matchId = (t.trade_id || t.order_id || '').toUpperCase().includes(searchFilter);
            if (!matchSym && !matchStrat && !matchId) return false;
        }
        if (selectedDayFilter) {
            const dStr = t.close_time || t.exit_timestamp || t.timestamp;
            if (!dStr) return false;
            const tDate = new Date(dStr).toISOString().split('T')[0];
            if (tDate !== selectedDayFilter) return false;
        }
        return true;
    });

    if (filteredClosed.length === 0) {
        daysContainer.innerHTML = `<div class="idle-state-row"><div class="idle-state-content"><span>No closed trades matching filter criteria • Adjust search or clear date filters</span></div></div>`;
        return;
    }

    // Group by Day (YYYY-MM-DD)
    const dayGroups = {};
    filteredClosed.forEach(t => {
        const rawTs = t.close_time || t.exit_timestamp || t.timestamp || Date.now();
        const dateObj = new Date(rawTs);
        const dayKey = dateObj.toISOString().split('T')[0];
        const dayLabel = dateObj.toLocaleDateString('en-US', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

        if (!dayGroups[dayKey]) {
            dayGroups[dayKey] = {
                dayKey: dayKey,
                dayLabel: dayLabel,
                trades: [],
                totalNet: 0,
                totalFees: 0
            };
        }

        const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
        dayGroups[dayKey].trades.push(t);
        dayGroups[dayKey].totalNet += net;
        dayGroups[dayKey].totalFees += Number(t.fees || t.total_fees || 0);
    });

    // Sort days descending
    const sortedDays = Object.keys(dayGroups).sort((a, b) => b.localeCompare(a));

    daysContainer.innerHTML = sortedDays.map(dayKey => {
        const grp = dayGroups[dayKey];
        const dayNet = grp.totalNet;
        const dayNetClass = dayNet >= 0 ? 'profit' : 'loss';
        const dayNetStr = `${dayNet >= 0 ? '+' : ''}${formatCurrency(dayNet)}`;

        // Sort trades within day descending
        const dayTradesSorted = grp.trades.sort((a, b) => {
            const tsA = new Date(a.close_time || a.exit_timestamp || a.timestamp || 0).getTime();
            const tsB = new Date(b.close_time || b.exit_timestamp || b.timestamp || 0).getTime();
            return tsB - tsA;
        });

        const rowsHtml = dayTradesSorted.map(t => {
            const sym = t.symbol || '-';
            const side = (t.side || t.action || 'BUY').toUpperCase();
            const sideClass = side === 'BUY' ? 'tag-buy' : 'tag-sell';
            const strat = t.strategy || 'ADX_EMA';
            const tf = t.timeframe || '5m';
            const entryPx = Number(t.entry_price || 0);
            const exitPx = Number(t.exit_price || 0);
            const qty = t.quantity || '-';
            const gross = Number(t.gross_pnl !== undefined ? t.gross_pnl : t.net_pnl || 0);
            const fees = Number(t.fees || t.total_fees || 0);
            const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
            const netPct = entryPx > 0 ? ((exitPx - entryPx) / entryPx) * 100 : 0;
            const netStr = `<span class="${net >= 0 ? 'profit' : 'loss'} td-strong">${net >= 0 ? '+' : ''}${formatCurrency(net)} (${netPct >= 0 ? '+' : ''}${netPct.toFixed(2)}%)</span>`;
            const reason = t.close_reason || t.exit_reason || (net >= 0 ? 'TAKE_PROFIT' : 'STOP_LOSS');
            const openTime = t.entry_timestamp || t.signal_time ? formatTime(t.entry_timestamp || t.signal_time) : '--:--';
            const closeTime = t.close_time || t.exit_timestamp || t.timestamp ? formatTime(t.close_time || t.exit_timestamp || t.timestamp) : '--:--';
            const dur = t.duration || (t.duration_seconds ? `${Math.round(t.duration_seconds)}s` : '-');

            return `<tr style="cursor: pointer;" onclick="inspectTradeLifecycle(${JSON.stringify(t).replace(/"/g, '&quot;')})">
                <td>${openTime} &rarr; ${closeTime}</td>
                <td><strong class="td-strong" style="color: #fff;">${sym}</strong> <span class="${sideClass}">${side}</span></td>
                <td><span class="cyan">${strat}</span> <span style="color: var(--text-muted); font-size: 10px;">(${tf})</span></td>
                <td>$${entryPx.toFixed(4)} &rarr; $${exitPx.toFixed(4)}</td>
                <td>${qty}</td>
                <td>${dur}</td>
                <td>${formatCurrency(gross)}</td>
                <td>${formatCurrency(fees)}</td>
                <td>${netStr}</td>
                <td><span class="tag-reason">${reason}</span></td>
                <td><button class="btn-terminal-pill" style="height: 24px; padding: 2px 8px; color: var(--accent-primary);" onclick="event.stopPropagation(); inspectTradeLifecycle(${JSON.stringify(t).replace(/"/g, '&quot;')})">LIFECYCLE</button></td>
            </tr>`;
        }).join('');

        return `
            <div class="journal-day-group">
                <div class="journal-day-header">
                    <div class="day-header-left">
                        <span class="day-header-date">📅 ${grp.dayLabel}</span>
                        <span class="day-header-count">${grp.trades.length} Trades Logged</span>
                    </div>
                    <div class="day-header-pnl ${dayNetClass}">
                        <span>Day Net Return: <strong>${dayNetStr}</strong></span>
                    </div>
                </div>
                <div class="panel-table-wrap">
                    <table class="terminal-table">
                        <thead>
                            <tr>
                                <th>OPEN &rarr; CLOSE</th>
                                <th>SYMBOL &amp; SIDE</th>
                                <th>STRATEGY &amp; TF</th>
                                <th>ENTRY &rarr; EXIT PRICE</th>
                                <th>QTY</th>
                                <th>DURATION</th>
                                <th>GROSS PNL</th>
                                <th>FEES</th>
                                <th>NET PNL ($ / %)</th>
                                <th>CLOSE REASON</th>
                                <th>AUDIT</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${rowsHtml}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }).join('');
}

function calcDurationStr(openTs) {
    const diffSec = Math.max(0, Math.floor((Date.now() - new Date(openTs).getTime()) / 1000));
    const m = Math.floor(diffSec / 60);
    const s = diffSec % 60;
    return `${m}m ${s}s`;
}

// ─── CALENDAR HEATMAP LOGIC ───
function navCalendarMonth(dir) {
    currentCalendarDate.setMonth(currentCalendarDate.getMonth() + dir);
    renderCalendarHeatmap();
}

function renderCalendarHeatmap() {
    const titleEl = document.getElementById('calendar-month-title');
    const gridEl = document.getElementById('calendar-days-grid');
    if (!gridEl) return;

    const year = currentCalendarDate.getFullYear();
    const month = currentCalendarDate.getMonth();
    if (titleEl) {
        titleEl.innerText = new Date(year, month, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' }).toUpperCase();
    }

    // Build map of daily PnL & trade counts
    const dayStats = {};
    (allRawTrades || []).forEach(t => {
        const rawTs = t.close_time || t.exit_timestamp || t.timestamp;
        if (!rawTs) return;
        const d = new Date(rawTs);
        if (d.getFullYear() === year && d.getMonth() === month) {
            const dayNum = d.getDate();
            if (!dayStats[dayNum]) dayStats[dayNum] = { count: 0, netPnl: 0, dateKey: d.toISOString().split('T')[0] };
            dayStats[dayNum].count++;
            dayStats[dayNum].netPnl += Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
        }
    });

    const firstDayIndex = (new Date(year, month, 1).getDay() + 6) % 7; // Monday = 0
    const totalDaysInMonth = new Date(year, month + 1, 0).getDate();

    let cellsHtml = '';

    // Empty lead cells
    for (let i = 0; i < firstDayIndex; i++) {
        cellsHtml += `<div class="calendar-day-cell" style="opacity: 0.2; pointer-events: none;"><span class="cal-day-num">-</span></div>`;
    }

    // Active day cells
    for (let day = 1; day <= totalDaysInMonth; day++) {
        const stat = dayStats[day];
        const hasTrades = stat && stat.count > 0;
        const net = hasTrades ? stat.netPnl : 0;
        const isProfit = net > 0;
        const isLoss = net < 0;
        const cellClass = hasTrades ? (isProfit ? 'profit-day' : (isLoss ? 'loss-day' : '')) : '';
        const pnlStr = hasTrades ? `<span class="${isProfit ? 'profit' : 'loss'} cal-day-pnl">${isProfit ? '+' : ''}${formatCurrency(net)}</span>` : '<span class="cal-day-pnl" style="color: var(--text-dim);">$0.00</span>';
        const countStr = hasTrades ? `<span class="cal-day-count">${stat.count} trade${stat.count > 1 ? 's' : ''}</span>` : '<span class="cal-day-count" style="color: var(--text-dim);">0 trades</span>';
        const dateKey = stat ? stat.dateKey : `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;

        cellsHtml += `
            <div class="calendar-day-cell ${cellClass}" onclick="filterTradesByDay('${dateKey}')">
                <div style="display: flex; justify-content: space-between; align-items: baseline;">
                    <span class="cal-day-num">${day}</span>
                    ${countStr}
                </div>
                ${pnlStr}
            </div>
        `;
    }

    gridEl.innerHTML = cellsHtml;
}

function filterTradesByDay(dateKey) {
    selectedDayFilter = dateKey;
    setJournalViewMode('table');
}

// ─── FULL TRADE LIFECYCLE DRAWER ───
function inspectTradeLifecycle(trade) {
    const sym = trade.symbol || 'PAIR';
    const tradeId = trade.trade_id || trade.order_id || 'TRD-LIFECYCLE';
    const strat = trade.strategy || 'ADX_EMA';
    const tf = trade.timeframe || '5m';
    const side = (trade.side || trade.action || 'BUY').toUpperCase();
    const isClosed = trade.status === 'CLOSED' || (trade.exit_price && Number(trade.exit_price) > 0);

    const net = Number(trade.net_pnl !== undefined ? trade.net_pnl : (trade.pnl || 0));
    const gross = Number(trade.gross_pnl !== undefined ? trade.gross_pnl : (net + Number(trade.fees || 0)));
    const fees = Number(trade.fees || trade.total_fees || 0);

    const entryPx = Number(trade.entry_price || trade.price || 0);
    const exitPx = Number(trade.exit_price || entryPx);
    const slPx = Number(trade.stop_loss || trade.sl || 0);
    const tpPx = Number(trade.take_profit || trade.tp || 0);
    const qty = trade.quantity || trade.orig_qty || '-';

    const balOpen = Number(trade.cash_before_entry || trade.balance_before_entry || 10000);
    const eqOpen = Number(trade.equity_before_entry || balOpen);
    const balClose = Number(trade.cash_after_exit || trade.balance_after_exit || (balOpen + net));
    const eqClose = Number(trade.equity_after_exit || balClose);

    const closeReason = trade.close_reason || trade.exit_reason || (isClosed ? (net >= 0 ? 'TAKE_PROFIT_OCO' : 'STOP_LOSS_OCO') : 'STILL_OPEN');
    const openTimeStr = trade.entry_timestamp || trade.signal_time ? formatDateTime(trade.entry_timestamp || trade.signal_time) : '-';
    const closeTimeStr = isClosed ? (trade.close_time || trade.exit_timestamp ? formatDateTime(trade.close_time || trade.exit_timestamp) : '-') : 'ACTIVE POSITION';
    const durStr = trade.duration || (trade.duration_seconds ? `${Math.round(trade.duration_seconds)}s` : (trade.entry_timestamp ? calcDurationStr(trade.entry_timestamp) : '-'));

    const drawerHtml = `
        <div class="lifecycle-card">
            <div class="lifecycle-card-title">
                <span class="td-strong" style="font-size: 14px; color: #fff;">${sym} • ${side}</span>
                <span class="tag ${isClosed ? (net >= 0 ? 'tag-buy' : 'tag-sell') : 'tag-buy'}">${isClosed ? (net >= 0 ? '🏆 WIN' : '🔻 LOSS') : '⚡ ACTIVE'}</span>
            </div>
            <div class="lifecycle-grid">
                <div class="lifecycle-row"><span class="lifecycle-lbl">Trade ID</span><span class="lifecycle-val td-strong">${tradeId}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Strategy</span><span class="lifecycle-val cyan">${strat} (${tf})</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Quantity</span><span class="lifecycle-val">${qty}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Duration</span><span class="lifecycle-val">${durStr}</span></div>
            </div>
        </div>

        <div class="lifecycle-card">
            <div class="lifecycle-card-title"><span>⏳ Execution Progression & Balance Impact</span></div>
            <div class="lifecycle-grid">
                <div class="lifecycle-row"><span class="lifecycle-lbl">Entry Timestamp</span><span class="lifecycle-val">${openTimeStr}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Entry Fill Price</span><span class="lifecycle-val td-strong">$${entryPx.toFixed(4)}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Cash @ Entry</span><span class="lifecycle-val cyan">${formatCurrency(balOpen)}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Equity @ Entry</span><span class="lifecycle-val">${formatCurrency(eqOpen)}</span></div>
            </div>
            <div style="border-top: 1px solid var(--border-medium); margin: 8px 0;"></div>
            <div class="lifecycle-grid">
                <div class="lifecycle-row"><span class="lifecycle-lbl">Exit Timestamp</span><span class="lifecycle-val">${closeTimeStr}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Exit Fill Price</span><span class="lifecycle-val td-strong">$${exitPx.toFixed(4)}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Cash @ Exit</span><span class="lifecycle-val cyan">${formatCurrency(balClose)}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Equity @ Exit</span><span class="lifecycle-val">${formatCurrency(eqClose)}</span></div>
            </div>
        </div>

        <div class="lifecycle-card">
            <div class="lifecycle-card-title"><span>💵 Accounting &amp; Return Breakdown</span></div>
            <div class="lifecycle-grid">
                <div class="lifecycle-row"><span class="lifecycle-lbl">Gross Return</span><span class="lifecycle-val">${formatCurrency(gross)}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Exchange Fees</span><span class="lifecycle-val loss">-${formatCurrency(fees)}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Net Realized PnL</span><span class="lifecycle-val ${net >= 0 ? 'profit' : 'loss'} td-strong">${net >= 0 ? '+' : ''}${formatCurrency(net)}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Close Trigger Reason</span><span class="lifecycle-val tag-reason">${closeReason}</span></div>
            </div>
        </div>

        <div class="lifecycle-card">
            <div class="lifecycle-card-title"><span>🛡️ Protection Bracket Levels</span></div>
            <div class="lifecycle-grid">
                <div class="lifecycle-row"><span class="lifecycle-lbl">Stop Loss Level</span><span class="lifecycle-val loss">${slPx > 0 ? '$' + slPx.toFixed(4) : '-'}</span></div>
                <div class="lifecycle-row"><span class="lifecycle-lbl">Take Profit Level</span><span class="lifecycle-val profit">${tpPx > 0 ? '$' + tpPx.toFixed(4) : '-'}</span></div>
            </div>
        </div>
    `;

    openInspectorDrawer(`TRADE LIFECYCLE • ${sym}`, drawerHtml);
}


// ==========================================
// 6. MARKETS TERMINAL (3-COLUMN LAYOUT)
// ==========================================
let activeMarketSymbol = 'BTCUSDT';
let activeMarketTf = '5m';
let marketChartInst = null;
let rawMarketDataMap = {};
let latestCandlesData = [];

function setMarketChartTf(tf) {
    activeMarketTf = tf;
    document.querySelectorAll('.market-center-panel .btn-tf').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById(`btn-mkt-tf-${tf}`);
    if (btn) btn.classList.add('active');
    loadMarketCandles();
}

function selectMarketSymbol(symbol) {
    if (!symbol) return;
    activeMarketSymbol = symbol.toUpperCase();
    
    // Highlight in watchlist table
    document.querySelectorAll('#market-watchlist-body tr').forEach(tr => {
        if (tr.getAttribute('data-sym') === activeMarketSymbol) {
            tr.classList.add('market-row-active');
        } else {
            tr.classList.remove('market-row-active');
        }
    });

    const info = rawMarketDataMap[activeMarketSymbol] || {};
    const symEl = document.getElementById('mkt-active-sym');
    if (symEl) symEl.innerText = activeMarketSymbol;

    const pxEl = document.getElementById('mkt-active-price');
    if (pxEl && info.close !== undefined) pxEl.innerText = formatCurrency(info.close);

    const chgEl = document.getElementById('mkt-active-chg');
    if (chgEl && info.change_24h !== undefined) {
        const chg = Number(info.change_24h);
        chgEl.innerHTML = chg > 0 ? `<span class="val-green">+${chg.toFixed(2)}%</span>` : `<span class="val-red">${chg.toFixed(2)}%</span>`;
    }

    renderMarketDetails(activeMarketSymbol);
    loadMarketCandles();
}

async function loadMarketCandles() {
    try {
        const res = await apiClient.get(`/api/candles?symbol=${activeMarketSymbol}&tf=${activeMarketTf}&limit=100`);
        if (res && Array.isArray(res)) {
            latestCandlesData = res;
            renderMarketChart(res);
            renderMarketDetails(activeMarketSymbol);
        }
    } catch (e) {
        console.error("Failed to load candles for", activeMarketSymbol, activeMarketTf, e);
    }
}

function renderMarketChart(candles) {
    const ctx = document.getElementById('marketCandleChart') || document.getElementById('marketChart');
    if (!ctx || !candles || candles.length === 0) return;

    const labels = candles.map(c => {
        const d = new Date(c.time * 1000);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    });
    const closes = candles.map(c => c.close);
    const volumes = candles.map(c => c.volume);

    // Calculate EMA20
    const ema20 = [];
    const k = 2 / (20 + 1);
    let prevEma = closes[0];
    for (let i = 0; i < closes.length; i++) {
        if (i < 20) {
            let sum = 0;
            for (let j = 0; j <= i; j++) sum += closes[j];
            prevEma = sum / (i + 1);
        } else {
            prevEma = (closes[i] * k) + (prevEma * (1 - k));
        }
        ema20.push(prevEma);
    }

    const firstPx = closes[0] || 1;
    const lastPx = closes[closes.length - 1] || 1;
    const isBull = lastPx >= firstPx;
    const lineColor = isBull ? '#00FF88' : '#FF2A55';
    const fillColor = isBull ? 'rgba(0, 255, 136, 0.08)' : 'rgba(255, 42, 85, 0.08)';

    if (marketChartInst) {
        marketChartInst.data.labels = labels;
        marketChartInst.data.datasets[0].data = closes;
        marketChartInst.data.datasets[0].borderColor = lineColor;
        marketChartInst.data.datasets[0].backgroundColor = fillColor;
        marketChartInst.data.datasets[1].data = ema20;
        marketChartInst.update('none');
        return;
    }

    marketChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Price',
                    data: closes,
                    borderColor: lineColor,
                    backgroundColor: fillColor,
                    borderWidth: 2,
                    fill: true,
                    tension: 0.15,
                    pointRadius: 0,
                    pointHitRadius: 8
                },
                {
                    label: 'EMA (20)',
                    data: ema20,
                    borderColor: '#FFB800',
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    fill: false,
                    pointRadius: 0
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#94a3b8', boxWidth: 10, font: { family: "'JetBrains Mono', monospace", size: 8.5 } }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: '#090e17',
                    titleColor: '#00f0ff',
                    bodyColor: '#f8fafc',
                    borderColor: 'rgba(0, 240, 255, 0.3)',
                    borderWidth: 1,
                    callbacks: {
                        label: function(ctx) {
                            return `${ctx.dataset.label}: $${Number(ctx.raw).toFixed(4)}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#52637a', font: { family: "'JetBrains Mono', monospace", size: 8 } }
                },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(0, 240, 255, 0.04)' },
                    ticks: {
                        color: '#67e8f9',
                        font: { family: "'JetBrains Mono', monospace", size: 8 },
                        callback: function(v) { return '$' + Number(v).toFixed(2); }
                    }
                }
            }
        }
    });
}

function renderMarketDetails(symbol) {
    const container = document.getElementById('market-details-content');
    if (!container) return;

    const info = rawMarketDataMap[symbol] || {};
    const price = Number(info.close || 0);
    const chg = Number(info.change_24h || 0);
    const chgClass = chg > 0 ? 'val-green' : (chg < 0 ? 'val-red' : 'val-neutral');
    const chgSign = chg > 0 ? '+' : '';

    // Find latest signal for this symbol
    let symSig = allRawSignals.find(s => (s.symbol || '').toUpperCase() === symbol);
    let sigSide = symSig ? (symSig.decision || symSig.side || 'HOLD').toUpperCase() : 'HOLD';
    let sigSideClass = (sigSide === 'BUY' || sigSide === 'LONG') ? 'tag tag-long' : ((sigSide === 'SELL' || sigSide === 'SHORT') ? 'tag tag-short' : 'tag tag-neutral');
    let sigConf = symSig && Number(symSig.confidence || 0) > 0 ? (Number(symSig.confidence) * 100).toFixed(1) + '%' : '-';
    let sigStrat = symSig ? (symSig.strategy || 'ADX_EMA') : 'MULTI-SCAN';

    let lastEvalTime = '-';
    if (info.last_eval) {
        lastEvalTime = formatDateTime(info.last_eval);
    } else if (info.last_update) {
        lastEvalTime = formatDateTime(info.last_update);
    }

    let lastCandleTime = '-';
    if (latestCandlesData && latestCandlesData.length > 0) {
        const lastC = latestCandlesData[latestCandlesData.length - 1];
        lastCandleTime = formatDateTime(new Date(lastC.time * 1000).toISOString());
    }

    container.innerHTML = `
        <div class="inspector-card">
            <div class="inspector-card-header">
                <span class="td-strong" style="font-size: 13px;">${symbol}</span>
                <span class="tag tag-qualified">SPOT TRADING</span>
            </div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Current Price</span><span class="inspector-val td-strong">${price > 0 ? formatCurrency(price) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">24h Change</span><span class="inspector-val ${chgClass} td-strong">${chgSign}${chg.toFixed(2)}%</span></div>
                <div class="inspector-row"><span class="inspector-lbl">24h Volume</span><span class="inspector-val">${info.volume ? Number(info.volume).toFixed(2) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Active TF</span><span class="inspector-val td-strong">${activeMarketTf}</span></div>
            </div>
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>⏰ Stream & Engine Cadence</span></div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Last Candle</span><span class="inspector-val">${lastCandleTime}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Last Strategy Eval</span><span class="inspector-val">${lastEvalTime}</span></div>
            </div>
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>⚡ Active Strategies</span></div>
            <div style="display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px;">
                <span class="badge badge-mono">AGGRESSOR</span>
                <span class="badge badge-mono">SCALPER</span>
                <span class="badge badge-mono">ADX_EMA</span>
                <span class="badge badge-mono">ML</span>
                <span class="badge badge-mono">SUPERTREND</span>
                <span class="badge badge-mono">SWING</span>
            </div>
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>📡 Current Signal State</span></div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Signal Decision</span><span class="inspector-val"><span class="${sigSideClass}">${sigSide}</span></span></div>
                <div class="inspector-row"><span class="inspector-lbl">Leading Strategy</span><span class="inspector-val">${sigStrat}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Confidence</span><span class="inspector-val">${sigConf}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Stop Loss</span><span class="inspector-val val-red">${symSig && Number(symSig.stop || symSig.sl || 0) > 0 ? Number(symSig.stop || symSig.sl).toFixed(4) : '-'}</span></div>
            </div>
        </div>
    `;
}

async function fetchMarketsData() {
    try {
        const data = await apiClient.get('/api/scanner');
        if (!data) return;

        const watchlistBody = document.getElementById('market-watchlist-body');
        const countEl = document.getElementById('mkt-watchlist-count');

        if (data.market_data && Object.keys(data.market_data).length > 0) {
            const syms = Object.keys(data.market_data);
            if (countEl) countEl.innerText = `${syms.length} Pairs`;

            rawMarketDataMap = {};
            const rowsHtml = syms.map(sym => {
                const info = data.market_data[sym] || {};
                const lastUpStr = data.last_market_update ? data.last_market_update[sym] : null;
                const lastEvalStr = data.last_evaluation ? data.last_evaluation[sym] : null;
                
                rawMarketDataMap[sym] = {
                    ...info,
                    last_update: lastUpStr,
                    last_eval: lastEvalStr
                };

                const price = Number(info.close || 0);
                const chg = Number(info.change_24h || 0);
                const chgClass = chg > 0 ? 'val-green' : (chg < 0 ? 'val-red' : 'val-neutral');
                const chgSign = chg > 0 ? '+' : '';

                // Signal match
                const sig = allRawSignals.find(s => (s.symbol || '').toUpperCase() === sym);
                const sigSide = sig ? (sig.decision || sig.side || 'HOLD').toUpperCase() : 'HOLD';
                const sigClass = (sigSide === 'BUY' || sigSide === 'LONG') ? 'tag tag-long' : ((sigSide === 'SELL' || sigSide === 'SHORT') ? 'tag tag-short' : 'tag tag-neutral');

                const trendStr = chg > 1.0 ? '▲ BULL' : (chg < -1.0 ? '▼ BEAR' : '▶ RANGE');
                const trendClass = chg > 1.0 ? 'val-green' : (chg < -1.0 ? 'val-red' : 'val-neutral');

                const upTime = lastUpStr ? formatTime(lastUpStr) : '-';
                const isActive = sym === activeMarketSymbol ? 'market-row-active' : '';

                return `<tr data-sym="${sym}" class="${isActive}" style="cursor: pointer;" onclick="selectMarketSymbol('${sym}')">
                    <td class="td-strong">${sym}</td>
                    <td>${price.toFixed(4)}</td>
                    <td class="${chgClass}">${chgSign}${chg.toFixed(2)}%</td>
                    <td>${info.volume ? Number(info.volume).toFixed(1) : '-'}</td>
                    <td class="${trendClass}">${trendStr}</td>
                    <td>${upTime}</td>
                    <td><span class="${sigClass}">${sigSide}</span></td>
                </tr>`;
            }).join('');

            if (watchlistBody) watchlistBody.innerHTML = rowsHtml;

            // Overview Market Table
            const marketBody = document.getElementById('market-body');
            if (marketBody) {
                marketBody.innerHTML = syms.map(sym => {
                    const info = data.market_data[sym] || {};
                    const lastUpStr = data.last_market_update ? data.last_market_update[sym] : null;
                    const ts = lastUpStr ? new Date(lastUpStr).getTime() : 0;
                    return `<tr>
                        <td class="td-strong">${sym}</td>
                        <td>${Number(info.close || 0).toFixed(4)}</td>
                        <td>${Number(info.change_24h || 0) >= 0 ? '+' : ''}${Number(info.change_24h || 0).toFixed(2)}%</td>
                        <td>${info.volume ? Number(info.volume).toFixed(1) : '-'}</td>
                        <td><span class="tag tag-active">CONNECTED</span></td>
                        <td>${formatTime(ts)}</td>
                    </tr>`;
                }).join('');
            }

            // Ticker & Movers
            const tickerContent = document.getElementById('bottom-ticker-content');
            if (tickerContent) {
                let mkts = [];
                for (const [sym, info] of Object.entries(data.market_data)) {
                    if (info && info.close !== undefined) {
                        mkts.push({ sym, price: info.close, chg: info.change_24h || 0 });
                    }
                }
                if (mkts.length > 0) {
                    const tickerHtml = mkts.map(m => {
                        const colorClass = m.chg > 0 ? 'val-green' : 'val-red';
                        const sign = m.chg > 0 ? '▲' : '▼';
                        return `<div class="ticker-item"><span class="ticker-sym">${m.sym}</span><span class="ticker-px ${colorClass}">${m.price.toFixed(4)} ${sign} ${Math.abs(m.chg).toFixed(2)}%</span></div>`;
                    });
                    tickerContent.innerHTML = tickerHtml.join('') + tickerHtml.join('');
                }
            }

            // Funnel Pipeline
            const fnMrk = document.getElementById('fn-mrk');
            if (fnMrk) fnMrk.innerText = syms.length;
            const fnSig = document.getElementById('fn-signals');
            if (fnSig) fnSig.innerText = data.TOTAL_SIGNALS || allRawSignals.length || 0;
            const fnProfRej = document.getElementById('fn-prof-rej');
            if (fnProfRej) fnProfRej.innerText = data.PROFITABILITY_REJECTED || 0;
            const fnProfAcc = document.getElementById('fn-prof-acc');
            if (fnProfAcc) fnProfAcc.innerText = data.PROFITABILITY_ACCEPTED || 0;
            const fnRiskRej = document.getElementById('fn-risk-rej');
            if (fnRiskRej) fnRiskRej.innerText = (data.RISK_REJECTED || 0) + (data.COOLDOWN_REJECTED || 0) + (data.JIT_REJECTED || 0) + (data.OTHER_REJECTED || 0);
            const fnRiskAcc = document.getElementById('fn-risk-acc');
            if (fnRiskAcc) fnRiskAcc.innerText = data.RISK_ACCEPTED || 0;
            const fnFilled = document.getElementById('fn-filled');
            if (fnFilled) fnFilled.innerText = data.ORDERS_FILLED || 0;

            // Load initial chart if not loaded
            if (!latestCandlesData || latestCandlesData.length === 0) {
                selectMarketSymbol(activeMarketSymbol);
            }
        }
    } catch (e) {
        console.error("Failed to fetch markets watchlist:", e);
    }
}


// ==========================================
// 7. MULTI-STRATEGY TERMINAL & MATRIX
// ==========================================
let rawStrategyData = {};

async function fetchStrategies() {
    try {
        const res = await apiClient.get('/api/strategy-metrics');
        if (!res || !res.strategies) return;

        rawStrategyData = res.strategies;
        renderStrategiesTable(res.strategies);
        if (res.matrix && res.timeframe_keys) {
            renderStrategyMatrix(res.strategies, res.matrix, res.timeframe_keys);
        }
    } catch (e) {
        console.error("Failed to fetch strategy metrics:", e);
    }
}

function renderStrategiesTable(strategies) {
    const tbody = document.getElementById('strat-full-body');
    if (!tbody) return;

    const stratKeys = ["aggressor", "scalper", "supertrend", "ml", "swing", "adx_ema"];
    const rowsHtml = stratKeys.map(k => {
        const s = strategies[k] || {
            name: k.toUpperCase(),
            status: "ACTIVE",
            timeframes: ["5m"],
            evaluations: 0,
            BUY: 0,
            SELL: 0,
            HOLD: 0,
            qualified: 0,
            profitability_rejected: 0,
            risk_rejected: 0,
            orders: 0,
            fills: 0,
            trades: 0,
            win_rate: null,
            net_pnl: 0.0
        };

        const tfsStr = Array.isArray(s.timeframes) ? s.timeframes.join(', ') : '5m';
        
        let winRateHtml = '<span class="tag-insufficient">INSUFFICIENT DATA</span>';
        if (s.win_rate !== null && s.win_rate !== undefined && s.trades > 0) {
            winRateHtml = `<span class="${s.win_rate >= 50 ? 'val-green' : 'val-red'} td-strong">${s.win_rate.toFixed(1)}%</span>`;
        }

        let pnlHtml = '$0.00';
        if (s.trades > 0) {
            const pnl = Number(s.net_pnl || 0);
            pnlHtml = `<span class="${pnl >= 0 ? 'val-green' : 'val-red'} td-strong">${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}</span>`;
        }

        return `<tr style="cursor: pointer;" onclick="inspectStrategy('${k}')" title="Click to view deep strategy telemetry and diagnostics">
            <td class="td-strong">${s.name}</td>
            <td><span class="tag tag-qualified">${s.status || 'ACTIVE'}</span></td>
            <td>${tfsStr}</td>
            <td>${s.evaluations || 0}</td>
            <td class="val-green">${s.BUY || 0}</td>
            <td class="val-red">${s.SELL || 0}</td>
            <td>${s.HOLD || 0}</td>
            <td><span class="tag tag-active">${s.qualified || 0}</span></td>
            <td>${s.profitability_rejected || 0}</td>
            <td>${s.risk_rejected || 0}</td>
            <td>${s.orders || 0}</td>
            <td>${s.fills || 0}</td>
            <td>${winRateHtml}</td>
            <td>${pnlHtml}</td>
        </tr>`;
    }).join('');

    tbody.innerHTML = rowsHtml;
}

function renderStrategyMatrix(strategies, matrix, timeframes) {
    const tbody = document.getElementById('strat-matrix-body');
    if (!tbody) return;

    const stratKeys = ["aggressor", "scalper", "supertrend", "ml", "swing", "adx_ema"];
    const rowsHtml = stratKeys.map(sk => {
        const sName = (strategies[sk]?.name || sk).toUpperCase();
        const tfCells = timeframes.map(tf => {
            const cell = matrix[sk] ? matrix[sk][tf] : null;
            if (!cell || !cell.active) {
                return `<td><div class="matrix-cell standby"><span class="matrix-cell-status val-neutral">STANDBY</span><span class="matrix-cell-stats">-</span></div></td>`;
            }

            let statText = `${cell.signals || 0} Sig`;
            if (cell.trades > 0) {
                const pnl = Number(cell.pnl || 0);
                statText += ` • ${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}`;
            }

            return `<td><div class="matrix-cell active"><span class="matrix-cell-status val-green">ACTIVE</span><span class="matrix-cell-stats">${statText}</span></div></td>`;
        }).join('');

        return `<tr><td class="td-strong">${sName}</td>${tfCells}</tr>`;
    }).join('');

    tbody.innerHTML = rowsHtml;
}

function inspectStrategy(stratKey) {
    const s = rawStrategyData[stratKey];
    if (!s) return;

    const sName = (s.name || stratKey).toUpperCase();
    const tfsStr = Array.isArray(s.timeframes) ? s.timeframes.join(', ') : '5m';

    const fmtStat = (v, isCurrency = false, isPct = false) => {
        if (v === null || v === undefined) return '<span class="tag-insufficient">INSUFFICIENT DATA</span>';
        if (isCurrency) {
            const num = Number(v);
            return `<span class="${num >= 0 ? 'val-green' : 'val-red'} td-strong">${num >= 0 ? '+' : ''}${formatCurrency(num)}</span>`;
        }
        if (isPct) {
            const num = Number(v);
            return `<span class="${num >= 50 ? 'val-green' : 'val-amber'} td-strong">${num.toFixed(1)}%</span>`;
        }
        return v;
    };

    // Signals table
    let signalsHtml = '<div class="empty-state">No recent signals recorded for this strategy</div>';
    if (s.recent_signals && s.recent_signals.length > 0) {
        signalsHtml = `
            <div class="table-container" style="max-height: 180px; overflow-y: auto;">
                <table>
                    <thead>
                        <tr><th>TIME</th><th>SYMBOL</th><th>TF</th><th>SIDE</th><th>PRICE</th><th>CONF</th><th>GATE</th></tr>
                    </thead>
                    <tbody>
                        ${s.recent_signals.map(sig => `
                            <tr>
                                <td>${sig.timestamp ? formatTime(sig.timestamp) : '-'}</td>
                                <td class="td-strong">${sig.symbol}</td>
                                <td>${sig.timeframe}</td>
                                <td><span class="tag ${sig.side === 'BUY' ? 'tag-long' : 'tag-short'}">${sig.side}</span></td>
                                <td>${Number(sig.entry || 0).toFixed(4)}</td>
                                <td>${sig.confidence ? (Number(sig.confidence) * 100).toFixed(1) + '%' : '-'}</td>
                                <td><span class="tag ${sig.final_decision === 'ACCEPTED' ? 'tag-qualified' : 'tag-rejected'}">${sig.final_decision || 'EVAL'}</span></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    // Trades table
    let tradesHtml = '<div class="empty-state">No executed trades yet for this strategy</div>';
    if (s.recent_trades && s.recent_trades.length > 0) {
        tradesHtml = `
            <div class="table-container" style="max-height: 180px; overflow-y: auto;">
                <table>
                    <thead>
                        <tr><th>TIME</th><th>SYMBOL</th><th>ENTRY</th><th>EXIT</th><th>PNL</th><th>REASON</th></tr>
                    </thead>
                    <tbody>
                        ${s.recent_trades.map(tr => `
                            <tr>
                                <td>${tr.timestamp ? formatTime(tr.timestamp) : '-'}</td>
                                <td class="td-strong">${tr.symbol}</td>
                                <td>${Number(tr.entry_price || 0).toFixed(4)}</td>
                                <td>${Number(tr.exit_price || 0).toFixed(4)}</td>
                                <td>${fmtStat(tr.net_pnl, true)}</td>
                                <td>${tr.close_reason || 'OCO'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    const drawerHtml = `
        <div class="inspector-card">
            <div class="inspector-card-header">
                <span class="td-strong" style="font-size: 13px;">⚡ ${sName} PERFORMANCE ATTRIBUTION</span>
                <span class="tag tag-qualified">${s.status || 'ACTIVE'}</span>
            </div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Configured Horiz.</span><span class="inspector-val td-strong">${tfsStr}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Total Evaluations</span><span class="inspector-val">${s.evaluations || 0}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Signals (BUY / SELL)</span><span class="inspector-val"><span class="val-green">${s.BUY || 0}</span> / <span class="val-red">${s.SELL || 0}</span></span></div>
                <div class="inspector-row"><span class="inspector-lbl">Orders & Fills</span><span class="inspector-val">${s.orders || 0} / ${s.fills || 0}</span></div>
            </div>
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>📊 Economic Performance & Attribution</span></div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Win Rate</span><span class="inspector-val">${fmtStat(s.win_rate, false, true)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Net PnL</span><span class="inspector-val">${s.trades > 0 ? fmtStat(s.net_pnl, true) : '<span class="tag-insufficient">INSUFFICIENT DATA</span>'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Average Trade</span><span class="inspector-val">${fmtStat(s.avg_trade, true)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Best Trade</span><span class="inspector-val">${fmtStat(s.best_trade, true)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Worst Trade</span><span class="inspector-val">${fmtStat(s.worst_trade, true)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Max Drawdown</span><span class="inspector-val">${s.trades > 0 ? formatCurrency(s.drawdown) : '<span class="tag-insufficient">INSUFFICIENT DATA</span>'}</span></div>
            </div>
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>🛡️ Multi-Gate Conversion Rates</span></div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Prof. Rejection Rate</span><span class="inspector-val">${fmtStat(s.profitability_rejection_rate, false, true)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Risk Rejection Rate</span><span class="inspector-val">${fmtStat(s.risk_rejection_rate, false, true)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Execution Success</span><span class="inspector-val">${fmtStat(s.execution_success_rate, false, true)}</span></div>
            </div>
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>📡 Recent Strategy Signals</span></div>
            ${signalsHtml}
        </div>

        <div class="inspector-card">
            <div class="inspector-card-header"><span>💼 Recent Strategy Executions</span></div>
            ${tradesHtml}
        </div>
    `;

    openInspectorDrawer(`STRATEGY INSPECTOR • ${sName}`, drawerHtml);
}


// ==========================================
// ==========================================
// 8. REAL-TIME EQUITY & BALANCE TIMELINE
// ==========================================
let equityChartInst = null;
let pnlHistChartInst = null;
let pnlDistChartInst = null;
let equityTimeframe = 'ALL';

function setEquityTimeframe(tf) {
    equityTimeframe = tf;
    document.querySelectorAll('#view-dashboard .btn-tf').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById(`tf-${tf.toLowerCase()}`);
    if (btn) btn.classList.add('active');
    initChart();
}

async function initChart() {
    try {
        const eqData = await apiClient.get(`/api/equity?timeframe=${equityTimeframe}`);
        if (!eqData || eqData.length === 0) return;
        rawEquityPoints = eqData;
        renderEquityChart();
    } catch (e) {
        console.error("Failed to load equity timeline:", e);
    }
}

function renderEquityChart() {
    const ctx = document.getElementById('equityTimelineChart') || document.getElementById('equityChart');
    if (!ctx || !rawEquityPoints || rawEquityPoints.length === 0) return;

    const points = rawEquityPoints;
    const labels = points.map(p => {
        const d = new Date(p.time);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    });
    const eqData = points.map(p => p.equity);
    const cashData = points.map(p => p.cash !== undefined ? p.cash : p.equity);

    if (equityChartInst) {
        equityChartInst.data.labels = labels;
        equityChartInst.data.datasets[0].data = eqData;
        if (equityChartInst.data.datasets[1]) {
            equityChartInst.data.datasets[1].data = cashData;
        }
        equityChartInst.update('none');
        return;
    }

    equityChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Managed Equity',
                    data: eqData,
                    borderColor: '#5B7FFF',
                    backgroundColor: 'rgba(91, 127, 255, 0.10)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.15,
                    pointRadius: 0,
                    pointHitRadius: 10
                },
                {
                    label: 'Liquid USDT Baseline',
                    data: cashData,
                    borderColor: '#22D3EE',
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    fill: false,
                    tension: 0.15,
                    pointRadius: 0,
                    pointHitRadius: 10
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#7C8AAD', font: { family: "'JetBrains Mono', monospace", size: 9 }, boxWidth: 10 }
                },
                tooltip: {
                    backgroundColor: '#111A2E',
                    titleColor: '#5B7FFF',
                    bodyColor: '#EAF0FF',
                    borderColor: '#223050',
                    borderWidth: 1,
                    callbacks: {
                        label: function(ctx) {
                            return `${ctx.dataset.label}: ${formatCurrency(ctx.raw)}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#7C8AAD', font: { family: "'JetBrains Mono', monospace", size: 8.5 } }
                },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        color: '#5B7FFF',
                        font: { family: "'JetBrains Mono', monospace", size: 8.5 },
                        callback: function(v) { return '$' + Number(v).toFixed(0); }
                    }
                }
            }
        }
    });
}


// ==========================================
// 9. AUDIO ALERTS & NOTIFICATION CENTER
// ==========================================
let isAudioEnabled = localStorage.getItem('trade_audio_enabled') === 'true';
let audioCtx = null;
let notificationHistory = [];
let seenNotificationIds = new Set();
let unreadNotifCount = 0;
let seenToastIds = new Set();
let isInitialLifecycleLoad = true;

function updateAudioUI() {
    const btn = document.getElementById('audio-toggle-btn');
    const txt = document.getElementById('audio-status-txt');
    if (btn) {
        if (isAudioEnabled) {
            btn.classList.add('active');
            btn.innerHTML = '🔊 <span id="audio-status-txt">Audio ON</span>';
        } else {
            btn.classList.remove('active');
            btn.innerHTML = '🔇 <span id="audio-status-txt">Audio OFF</span>';
        }
    }
}

function toggleAudioAlerts() {
    isAudioEnabled = !isAudioEnabled;
    localStorage.setItem('trade_audio_enabled', isAudioEnabled ? 'true' : 'false');
    updateAudioUI();
    if (isAudioEnabled) {
        playAudioAlert('trade_opened');
    }
}

function playAudioAlert(type) {
    if (!isAudioEnabled) return;
    try {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (audioCtx.state === 'suspended') {
            audioCtx.resume();
        }

        const now = audioCtx.currentTime;
        if (type === 'trade_opened') {
            [523.25, 659.25, 783.99].forEach((freq, i) => {
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(freq, now + i * 0.09);
                gain.gain.setValueAtTime(0.12, now + i * 0.09);
                gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.09 + 0.25);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start(now + i * 0.09);
                osc.stop(now + i * 0.09 + 0.26);
            });
        } else if (type === 'trade_closed_win') {
            [783.99, 1046.50].forEach((freq, i) => {
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(freq, now + i * 0.12);
                gain.gain.setValueAtTime(0.15, now + i * 0.12);
                gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.12 + 0.35);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start(now + i * 0.12);
                osc.stop(now + i * 0.12 + 0.36);
            });
        } else if (type === 'trade_closed_loss') {
            [440.0, 349.23].forEach((freq, i) => {
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(freq, now + i * 0.14);
                gain.gain.setValueAtTime(0.12, now + i * 0.14);
                gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.14 + 0.35);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start(now + i * 0.14);
                osc.stop(now + i * 0.14 + 0.36);
            });
        } else if (type === 'critical_engine_failure' || type === 'order_failed') {
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            osc.type = 'sawtooth';
            osc.frequency.setValueAtTime(220, now);
            gain.gain.setValueAtTime(0.15, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.start(now);
            osc.stop(now + 0.41);
        }
    } catch (err) {
        console.error("Audio playback error:", err);
    }
}

function pushNotification({ id, title, type, desc, payload, playSoundType }) {
    if (!id || seenNotificationIds.has(id)) return;
    seenNotificationIds.add(id);

    const notif = {
        id,
        title: title || 'SYSTEM EVENT',
        type: type || 'INFO',
        desc: desc || '',
        time: Date.now(),
        payload: payload || {}
    };

    notificationHistory.unshift(notif);
    if (notificationHistory.length > 50) notificationHistory.pop();

    unreadNotifCount++;
    updateNotifBadge();
    renderNotificationList();

    if (playSoundType) {
        playAudioAlert(playSoundType);
    }
}

function updateNotifBadge() {
    const badge = document.getElementById('notif-badge');
    if (badge) {
        if (unreadNotifCount > 0) {
            badge.innerText = unreadNotifCount > 99 ? '99+' : unreadNotifCount;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }
}

function toggleNotificationDropdown() {
    const dd = document.getElementById('notification-dropdown');
    if (dd) {
        dd.classList.toggle('show');
        if (dd.classList.contains('show')) {
            unreadNotifCount = 0;
            updateNotifBadge();
        }
    }
}

function clearNotifications() {
    notificationHistory = [];
    unreadNotifCount = 0;
    updateNotifBadge();
    renderNotificationList();
}

function renderNotificationList() {
    const body = document.getElementById('notif-dropdown-body');
    if (!body) return;

    if (notificationHistory.length === 0) {
        body.innerHTML = '<div class="notif-empty">No recent notifications</div>';
        return;
    }

    body.innerHTML = notificationHistory.map((n, idx) => {
        const timeStr = formatTime(n.time);
        const tagClass = n.type === 'TRADE_OPENED' ? 'tag tag-long' : (n.type === 'TRADE_CLOSED' ? 'tag tag-qualified' : (n.type === 'FAILED' ? 'tag tag-rejected' : 'tag tag-neutral'));
        return `<div class="notif-item" onclick="inspectNotification(${idx})">
            <div class="notif-item-top">
                <span class="${tagClass}">${n.title}</span>
                <span class="notif-time">${timeStr}</span>
            </div>
            <div class="notif-item-desc">${n.desc}</div>
        </div>`;
    }).join('');
}

function inspectNotification(idx) {
    if (notificationHistory[idx]) {
        const n = notificationHistory[idx];
        openInspectorDrawer(`EVENT • ${n.title}`, n.payload || { description: n.desc, time: formatDateTime(n.time) });
    }
}

// ─── TOAST NOTIFICATIONS ───
function showTradeOpenedToast(trade) {
    const toastId = `OPEN_${trade.trade_id || trade.symbol}_${trade.fill_timestamp || trade.timestamp || Date.now()}`;
    if (seenToastIds.has(toastId)) return;
    seenToastIds.add(toastId);

    const container = document.getElementById('trade-toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'trade-toast toast-open';
    const sym = trade.symbol || 'PAIR';
    const tf = trade.timeframe || '5m';
    const side = (trade.side || trade.action || 'BUY').toUpperCase();
    const entry = Number(trade.entry_price || trade.entry || trade.price || 0);
    const qty = trade.quantity || trade.orig_qty || '-';
    const timeStr = formatTime(trade.entry_timestamp || trade.timestamp || Date.now());

    toast.innerHTML = `
        <div class="toast-header">
            <div class="toast-badge-title">
                <span class="tag tag-qualified">⚡ OPENED</span>
                <span>${sym}</span>
            </div>
            <span class="toast-time">${timeStr}</span>
        </div>
        <div class="toast-grid">
            <div class="toast-row"><span class="toast-lbl">Strat:</span><span class="toast-val">${strat} (${tf})</span></div>
            <div class="toast-row"><span class="toast-lbl">Side:</span><span class="toast-val val-green">${side}</span></div>
            <div class="toast-row"><span class="toast-lbl">Entry:</span><span class="toast-val">$${entry.toFixed(4)}</span></div>
            <div class="toast-row"><span class="toast-lbl">Qty:</span><span class="toast-val">${qty}</span></div>
        </div>
        <div class="toast-actions">
            <button class="toast-btn-action" onclick="inspectTrade(${JSON.stringify(trade).replace(/"/g, '&quot;')})">VIEW</button>
            <button class="toast-btn-dismiss" onclick="this.closest('.trade-toast').remove()">✕</button>
        </div>
    `;

    container.appendChild(toast);
    setTimeout(() => { if (toast.parentElement) toast.remove(); }, 5000);

    pushNotification({
        id: toastId,
        title: `TRADE OPENED • ${sym}`,
        type: 'TRADE_OPENED',
        desc: `${side} ${qty} ${sym} @ $${entry.toFixed(4)} (${strat})`,
        payload: trade,
        playSoundType: 'trade_opened'
    });
}

function showTradeClosedToast(trade) {
    const toastId = `CLOSE_${trade.trade_id || trade.symbol}_${trade.close_timestamp || trade.timestamp || Date.now()}`;
    if (seenToastIds.has(toastId)) return;
    seenToastIds.add(toastId);

    const container = document.getElementById('trade-toast-container');
    if (!container) return;
    while (container.children.length >= 2) container.firstElementChild.remove();

    const toast = document.createElement('div');
    const netPnl = Number(trade.net_pnl !== undefined ? trade.net_pnl : (trade.pnl || 0));
    const isWin = netPnl >= 0;
    toast.className = `trade-toast ${isWin ? 'toast-close-win' : 'toast-close-loss'}`;

    const sym = trade.symbol || 'PAIR';
    const tf = trade.timeframe || '5m';
    const strat = trade.strategy || 'ADX_EMA';
    const entry = Number(trade.entry_price || 0);
    const exit = Number(trade.exit_price || 0);
    const closeReason = trade.close_reason || trade.exit_reason || 'OCO_EXIT';
    const timeStr = formatTime(trade.close_timestamp || trade.timestamp || Date.now());

    toast.innerHTML = `
        <div class="toast-header">
            <div class="toast-badge-title">
                <span class="tag ${isWin ? 'tag-qualified' : 'tag-rejected'}">${isWin ? '🏆 WIN' : '🔻 LOSS'}</span>
                <span>${sym}</span>
            </div>
            <span class="toast-time">${timeStr}</span>
        </div>
        <div class="toast-grid">
            <div class="toast-row"><span class="toast-lbl">PnL:</span><span class="toast-val ${isWin ? 'val-green' : 'val-red'} td-strong">${isWin ? '+' : ''}${formatCurrency(netPnl)}</span></div>
            <div class="toast-row"><span class="toast-lbl">Strat:</span><span class="toast-val">${strat} (${tf})</span></div>
            <div class="toast-row"><span class="toast-lbl">Exit:</span><span class="toast-val">$${exit.toFixed(4)}</span></div>
            <div class="toast-row"><span class="toast-lbl">Reason:</span><span class="toast-val">${closeReason}</span></div>
        </div>
        <div class="toast-actions">
            <button class="toast-btn-action" onclick="inspectTrade(${JSON.stringify(trade).replace(/"/g, '&quot;')})">VIEW</button>
            <button class="toast-btn-dismiss" onclick="this.closest('.trade-toast').remove()">✕</button>
        </div>
    `;

    container.appendChild(toast);
    setTimeout(() => { if (toast.parentElement) toast.remove(); }, 5000);

    pushNotification({
        id: toastId,
        title: `TRADE CLOSED • ${sym} (${isWin ? '+' : ''}${formatCurrency(netPnl)})`,
        type: 'TRADE_CLOSED',
        desc: `Closed ${sym} via ${closeReason} @ $${exit.toFixed(4)} (Net PnL: ${formatCurrency(netPnl)})`,
        payload: trade,
        playSoundType: isWin ? 'trade_closed_win' : 'trade_closed_loss'
    });
}

function showOrderFailedToast(event) {
    const toastId = `FAIL_${event.symbol || 'ORD'}_${event.timestamp || Date.now()}`;
    if (seenToastIds.has(toastId)) return;
    seenToastIds.add(toastId);

    const container = document.getElementById('trade-toast-container');
    if (!container) return;
    while (container.children.length >= 2) container.firstElementChild.remove();

    const toast = document.createElement('div');
    toast.className = 'trade-toast toast-failed';
    const sym = event.symbol || '-';
    const strat = event.strategy || 'ADX_EMA';
    const reason = event.reason || event.error_message || 'Exchange order rejected';
    const timeStr = formatTime(event.timestamp || Date.now());

    toast.innerHTML = `
        <div class="toast-header">
            <div class="toast-badge-title">
                <span class="tag tag-rejected">⚠️ FAILED</span>
                <span>${sym}</span>
            </div>
            <span class="toast-time">${timeStr}</span>
        </div>
        <div class="toast-grid">
            <div class="toast-row"><span class="toast-lbl">Strat:</span><span class="toast-val">${strat}</span></div>
            <div class="toast-row"><span class="toast-lbl">Reason:</span><span class="toast-val val-red">${reason}</span></div>
        </div>
        <div class="toast-actions">
            <button class="toast-btn-action" onclick="openInspectorDrawer('ORDER FAILURE AUDIT', ${JSON.stringify(event).replace(/"/g, '&quot;')})">VIEW</button>
            <button class="toast-btn-dismiss" onclick="this.closest('.trade-toast').remove()">✕</button>
        </div>
    `;

    container.appendChild(toast);
    setTimeout(() => { if (toast.parentElement) toast.remove(); }, 5000);

    pushNotification({
        id: toastId,
        title: `ORDER FAILED • ${sym}`,
        type: 'FAILED',
        desc: `Order failed on ${sym}: ${reason}`,
        payload: event,
        playSoundType: 'critical_engine_failure'
    });
}

function showEngineEventToast(title, msg, type = 'ENGINE_EVENT') {
    const toastId = `ENG_${title}_${Date.now()}`;
    const container = document.getElementById('trade-toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'trade-toast toast-engine';
    toast.innerHTML = `
        <div class="toast-header">
            <div class="toast-badge-title">
                <span class="tag ${title.includes('OFFLINE') || title.includes('HALT') ? 'tag-rejected' : 'tag-qualified'}">⚡ ${title}</span>
            </div>
            <span class="toast-time">${formatTime(Date.now())}</span>
        </div>
    `;
    container.appendChild(toast);
    setTimeout(() => { if (toast.parentElement) toast.remove(); }, 5000);

    pushNotification({
        id: toastId,
        title: title,
        type: type,
        desc: msg,
        payload: { title, msg, timestamp: new Date().toISOString() },
        playSoundType: (title.includes('OFFLINE') || title.includes('HALT')) ? 'critical_engine_failure' : null
    });
}


// ==========================================
// 10. ACCOUNT ACTIVITY TIMELINE
// ==========================================
let allRawActivity = [];
let currentActivityFilter = 'ALL';

function filterActivity(filterType) {
    currentActivityFilter = filterType;
    document.querySelectorAll('#view-activity .btn-tf').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById(`btn-act-${filterType.toLowerCase()}`);
    if (btn) btn.classList.add('active');
    renderActivityTable();
}

async function fetchActivity() {
    try {
        const res = await apiClient.get('/api/activity?limit=100');
        if (!res || !Array.isArray(res.activity)) return;

        allRawActivity = res.activity;
        const countEl = document.getElementById('activity-count');
        if (countEl) countEl.innerText = `${allRawActivity.length} Events Logged`;

        // Populate seen IDs on first boot to prevent toast flood
        if (isInitialLifecycleLoad) {
            allRawActivity.forEach(ev => {
                seenToastIds.add(`OPEN_${ev.trade_id}_${ev.timestamp}`);
                seenToastIds.add(`CLOSE_${ev.trade_id}_${ev.timestamp}`);
                seenToastIds.add(`FAIL_${ev.symbol}_${ev.timestamp}`);
            });
            isInitialLifecycleLoad = false;
        } else {
            allRawActivity.forEach(ev => {
                if (ev.type === 'TRADE OPENED' && !seenToastIds.has(`OPEN_${ev.trade_id}_${ev.timestamp}`)) {
                    showTradeOpenedToast(ev.raw || ev);
                } else if (ev.type === 'TRADE CLOSED' && !seenToastIds.has(`CLOSE_${ev.trade_id}_${ev.timestamp}`)) {
                    showTradeClosedToast(ev.raw || ev);
                } else if (ev.type === 'ORDER FAILED' && !seenToastIds.has(`FAIL_${ev.symbol}_${ev.timestamp}`)) {
                    showOrderFailedToast(ev.raw || ev);
                }
            });
        }

        renderActivityTable();
    } catch (e) {
        console.error("Failed to fetch account activity:", e);
    }
}

function renderActivityTable() {
    const tbody = document.getElementById('activity-logs-body');
    if (!tbody) return;

    let list = allRawActivity;
    if (currentActivityFilter === 'TRADES') {
        list = allRawActivity.filter(a => a.event && (a.event.includes('Trade') || a.type.includes('TRADE')));
    } else if (currentActivityFilter === 'BALANCE') {
        list = allRawActivity.filter(a => a.event && (a.event.includes('Balance') || a.event.includes('Fee') || a.event.includes('Reconciliation')));
    } else if (currentActivityFilter === 'SYSTEM') {
        list = allRawActivity.filter(a => a.event && (a.event.includes('Engine') || a.event.includes('Order failed') || a.event.includes('Safety')));
    } else if (currentActivityFilter === 'SIGNALS') {
        list = allRawActivity.filter(a => a.event && a.event.includes('signal'));
    }

    if (list.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="idle-state-row">Audit ledger initialized • Live trade executions, risk checks, and system events will stream here</td></tr>';
        return;
    }

    tbody.innerHTML = list.map((ev, idx) => {
        const timeStr = ev.timestamp ? formatDateTime(ev.timestamp) : '-';
        const evName = ev.event || ev.type || 'Event';
        const evTagClass = evName.includes('Trade opened') ? 'tag tag-long' : (evName.includes('Trade closed') ? 'tag tag-qualified' : (evName.includes('Order failed') ? 'tag tag-rejected' : (evName.includes('Fee') ? 'tag tag-short' : 'tag tag-neutral')));
        const sym = ev.symbol || '-';
        const strat = ev.strategy || '-';
        const balStr = ev.balance !== undefined ? formatCurrency(Number(ev.balance)) : '-';
        const eqStr = ev.equity !== undefined ? formatCurrency(Number(ev.equity)) : '-';
        const pnl = Number(ev.pnl || 0);
        const pnlStr = ev.value_pnl && ev.value_pnl !== '-' ? ev.value_pnl : (pnl !== 0 ? `<span class="${pnl >= 0 ? 'val-green' : 'val-red'}">${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}</span>` : '-');
        const tid = ev.trade_id || '-';
        const desc = ev.description || '-';

        return `<tr style="cursor: pointer;" onclick="inspectActivityByIndex(${idx})" title="Click to view full event audit payload">
            <td>${timeStr}</td>
            <td><span class="${evTagClass}">${evName}</span></td>
            <td class="td-strong">${sym}</td>
            <td>${strat}</td>
            <td>${balStr}</td>
            <td>${eqStr}</td>
            <td>${pnlStr}</td>
            <td>${tid}</td>
            <td title="${desc}">${desc.length > 40 ? desc.substring(0, 40) + '...' : desc}</td>
        </tr>`;
    }).join('');
}

function inspectActivityByIndex(idx) {
    if (allRawActivity[idx]) {
        const ev = allRawActivity[idx];
        openInspectorDrawer(`EVENT AUDIT • ${ev.event || ev.type}`, ev.raw || ev);
    }
}


// ==========================================
// 11. RISK TERMINAL & DECISION AUDIT
// ==========================================
let allRawRiskEvents = [];

async function fetchRiskData() {
    try {
        const [riskRes, eventsRes] = await Promise.all([
            apiClient.get('/api/risk'),
            apiClient.get('/api/risk-events?limit=200')
        ]);

        if (riskRes && riskRes.risk) {
            const r = riskRes.risk;
            const setV = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };

            setV('rk-top-equity', formatCurrency(r.total_equity));
            setV('rk-top-cash', formatCurrency(r.cash_usdt));
            setV('rk-top-managed', formatCurrency(r.managed_asset_value || r.deployed_capital));
            setV('rk-top-exposure', (r.risk_used_pct || 0).toFixed(2) + '%');
            setV('rk-top-used', (r.risk_used_pct || 0).toFixed(2) + '%');
            setV('rk-top-avail', (r.available_risk_pct || 0).toFixed(2) + '%');
            setV('rk-top-open-pos', r.current_open_positions || 0);
            setV('rk-top-max-pos', r.max_open_positions || 5);
            setV('rk-top-mdd', (r.max_drawdown_pct || 0).toFixed(2) + '%');

            const dailyEl = document.getElementById('rk-top-daily-pnl');
            if (dailyEl) {
                const dp = Number(r.daily_pnl || 0);
                dailyEl.innerText = (dp >= 0 ? '+' : '') + formatCurrency(dp);
                dailyEl.className = 'metric-value ' + (dp >= 0 ? 'val-green' : 'val-red');
            }
        }

        const logsBody = document.getElementById('risk-logs-body');
        if (logsBody && eventsRes && Array.isArray(eventsRes.events)) {
            allRawRiskEvents = eventsRes.events;
            if (allRawRiskEvents.length === 0) {
                logsBody.innerHTML = '<tr><td colspan="9" class="empty-state">No Risk Decisions or Gate Breaches Logged</td></tr>';
            } else {
                logsBody.innerHTML = allRawRiskEvents.map((e, idx) => {
                    const timeStr = e.timestamp ? formatDateTime(e.timestamp) : '-';
                    const sym = e.symbol || '-';
                    const tf = e.timeframe || '5m';
                    const strat = e.strategy || 'ADX_EMA';
                    const reqRisk = e.requested_risk || '1.00%';
                    const availRisk = e.available_risk || '18.06%';
                    const exp = e.exposure || '1.94%';
                    const dec = (e.decision || 'ACCEPTED').toUpperCase();
                    const decClass = dec === 'ACCEPTED' ? 'tag tag-qualified' : 'tag tag-rejected';
                    const reason = e.reason || '-';
                    const shortReason = reason.length > 32 ? reason.substring(0, 32) + '...' : reason;

                    return `<tr style="cursor: pointer;" onclick="inspectRiskEventByIndex(${idx})" title="Click to view risk decision audit">
                        <td>${timeStr}</td>
                        <td class="td-strong">${sym}</td>
                        <td>${tf}</td>
                        <td>${strat}</td>
                        <td>${reqRisk}</td>
                        <td class="val-green">${availRisk}</td>
                        <td>${exp}</td>
                        <td><span class="${decClass}">${dec}</span></td>
                        <td title="${reason}">${shortReason}</td>
                    </tr>`;
                }).join('');
            }
        }
    } catch (e) {
        console.error("Failed to fetch risk data:", e);
    }
}

function inspectRiskEventByIndex(idx) {
    if (allRawRiskEvents && allRawRiskEvents[idx]) {
        const e = allRawRiskEvents[idx];
        const drawerHtml = `
            <div class="inspector-card">
                <div class="inspector-card-header">
                    <span>🛡️ Risk Gate Decision Audit</span>
                    <span class="tag ${e.decision === 'ACCEPTED' ? 'tag-qualified' : 'tag-rejected'}">${e.decision}</span>
                </div>
                <div class="inspector-grid-2">
                    <div class="inspector-row"><span class="inspector-lbl">Event Time</span><span class="inspector-val">${e.timestamp ? formatDateTime(e.timestamp) : '-'}</span></div>
                    <div class="inspector-row"><span class="inspector-lbl">Symbol</span><span class="inspector-val td-strong">${e.symbol}</span></div>
                    <div class="inspector-row"><span class="inspector-lbl">Strategy</span><span class="inspector-val">${e.strategy || 'ADX_EMA'}</span></div>
                    <div class="inspector-row"><span class="inspector-lbl">Timeframe</span><span class="inspector-val">${e.timeframe || '5m'}</span></div>
                    <div class="inspector-row"><span class="inspector-lbl">Requested Risk</span><span class="inspector-val">${e.requested_risk || '1.00%'}</span></div>
                    <div class="inspector-row"><span class="inspector-lbl">Available Risk Buffer</span><span class="inspector-val val-green">${e.available_risk || '18.06%'}</span></div>
                    <div class="inspector-row"><span class="inspector-lbl">Portfolio Exposure</span><span class="inspector-val">${e.exposure || '1.94%'}</span></div>
                    <div class="inspector-row"><span class="inspector-lbl">Gate Decision</span><span class="inspector-val td-strong">${e.decision}</span></div>
                </div>
            </div>
            <div class="inspector-card">
                <div class="inspector-card-header"><span>📋 Trigger & Policy Reason</span></div>
                <div style="font-size: 11px; line-height: 1.6; color: var(--text-primary); margin-top: 4px;">${e.reason || 'All quantitative risk invariants respected.'}</div>
            </div>
        `;
        openInspectorDrawer(`RISK AUDIT • ${e.symbol || 'SYSTEM'}`, drawerHtml);
    }
}


// ==========================================
// 12. QUANTITATIVE ANALYTICS & DIAGNOSTICS
// ==========================================
let activeAnalyticsTimeframe = 'ALL';

function setAnalyticsTimeframe(tf) {
    activeAnalyticsTimeframe = tf;
    document.querySelectorAll('#view-analytics .btn-tf').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById(`btn-an-${tf.toLowerCase()}`);
    if (btn) btn.classList.add('active');
    fetchAnalyticsData();
}

async function fetchAnalyticsData() {
    try {
        const res = await apiClient.get(`/api/analytics?timeframe=${activeAnalyticsTimeframe}`);
        if (!res || !res.analytics) return;

        const a = res.analytics;
        const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
        const fmtVal = (v, isCur = false, isPct = false) => {
            if (v === null || v === undefined) return 'INSUFFICIENT DATA';
            if (isCur) return (Number(v) >= 0 ? '+' : '') + formatCurrency(Number(v));
            if (isPct) return Number(v).toFixed(2) + '%';
            return v;
        };

        setVal('an-total', a.total_trades || 0);
        setVal('an-winrate', a.win_rate !== null ? `${a.win_rate.toFixed(2)}%` : 'INSUFFICIENT DATA');
        setVal('an-net-pnl', formatCurrency(a.net_pnl || 0));
        setVal('an-realized-pnl', formatCurrency(a.realized_pnl || 0));
        setVal('an-unrealized-pnl', formatCurrency(a.unrealized_pnl || 0));
        setVal('an-pf', a.profit_factor !== null ? a.profit_factor : 'INSUFFICIENT DATA');
        setVal('an-avg-trade', fmtVal(a.avg_trade, true));
        setVal('an-largest-win', fmtVal(a.largest_win, true));
        setVal('an-largest-loss', fmtVal(a.largest_loss, true));
        setVal('an-fees', formatCurrency(a.total_fees || 0));
        setVal('an-mdd', (a.max_drawdown || 0).toFixed(2) + '%');

        // Color coding
        const netEl = document.getElementById('an-net-pnl');
        if (netEl) netEl.className = 'metric-value ' + (a.net_pnl > 0 ? 'val-green' : (a.net_pnl < 0 ? 'val-red' : 'val-neutral'));

        // Diagnostic Funnel "Why Didn't It Trade?"
        if (res.why_didnt_it_trade) {
            const d = res.why_didnt_it_trade;
            setVal('diag-candles', d.candles || d.evaluations || 0);
            setVal('diag-evals', d.evaluations || 0);
            setVal('diag-signals', d.signals || 0);
            setVal('diag-prof-acc', d.profitability_accepted || 0);
            setVal('diag-prof-rej', d.profitability_rejected || 0);
            setVal('diag-risk-acc', d.risk_accepted || 0);
            setVal('diag-risk-rej', d.risk_rejected || 0);
            setVal('diag-exec-elig', d.execution_eligible || 0);
            setVal('diag-orders-sub', d.orders_submitted || 0);
            setVal('diag-orders-fill', d.orders_filled || 0);

            // Also set an-* table IDs
            setVal('an-evals', d.evaluations || 0);
            setVal('an-signals', d.signals || 0);
            setVal('an-prof-acc', d.profitability_accepted || 0);
            setVal('an-prof-rej', d.profitability_rejected || 0);
            setVal('an-risk-acc', d.risk_accepted || 0);
            setVal('an-risk-rej', d.risk_rejected || 0);
            setVal('an-exec-elig', d.execution_eligible || 0);
            setVal('an-ord-sub', d.orders_submitted || 0);
            setVal('an-ord-fail', d.orders_failed || 0);
            setVal('an-ord-fill', d.orders_filled || 0);

            const reasonBody = document.getElementById('diag-dominant-reason');
            if (reasonBody) reasonBody.innerHTML = `<strong>Dominant Pipeline Bottleneck:</strong> ${d.dominant_reason || 'Insufficient Alpha / Spread Friction'}`;
        }

        // Daily PnL Chart
        if (res.daily_pnl) {
            renderDailyPnLChart(res.daily_pnl);
        }

        // Trade PnL Distribution Chart
        if (res.pnl_distribution) {
            renderPnLDistChart(res.pnl_distribution);
        }

        // Comparisons: Strategy, Timeframe, Symbol
        renderComparisonTable('an-strat-body', res.strategy_comparison);
        renderComparisonTable('an-tf-body', res.timeframe_comparison);
        renderComparisonTable('an-sym-body', res.symbol_comparison);

    } catch (e) {
        console.error("Failed to fetch analytics:", e);
    }
}

function renderDailyPnLChart(dailyMap) {
    const ctx = document.getElementById('pnlHistChart');
    if (!ctx) return;

    const labels = Object.keys(dailyMap).sort();
    const data = labels.map(k => dailyMap[k]);
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
                label: 'Daily Net PnL',
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
                    backgroundColor: '#0f172a',
                    titleColor: '#f8fafc',
                    bodyColor: '#94a3b8',
                    borderColor: '#334155',
                    borderWidth: 1,
                    callbacks: {
                        label: function(ctx) { return formatCurrency(ctx.raw); }
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#64748b', font: { family: "'JetBrains Mono', monospace", size: 9 } }
                },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { family: "'JetBrains Mono', monospace", size: 9 },
                        callback: function(v) { return '$' + Number(v).toLocaleString(); }
                    }
                }
            }
        }
    });
}

function renderPnLDistChart(distMap) {
    const ctx = document.getElementById('pnlDistChart');
    if (!ctx) return;

    const labels = Object.keys(distMap);
    const data = labels.map(k => distMap[k]);
    const bgColors = labels.map(l => l.startsWith('-') || l.startsWith('<') ? '#f43f5e' : '#10b981');

    if (pnlDistChartInst) {
        pnlDistChartInst.data.labels = labels;
        pnlDistChartInst.data.datasets[0].data = data;
        pnlDistChartInst.data.datasets[0].backgroundColor = bgColors;
        pnlDistChartInst.update('none');
        return;
    }

    pnlDistChartInst = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Trades in Bucket',
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
                    backgroundColor: '#0f172a',
                    titleColor: '#f8fafc',
                    bodyColor: '#94a3b8',
                    borderColor: '#334155',
                    borderWidth: 1
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#64748b', font: { family: "'JetBrains Mono', monospace", size: 8 } }
                },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { family: "'JetBrains Mono', monospace", size: 9 },
                        precision: 0
                    }
                }
            }
        }
    });
}

function renderComparisonTable(tbodyId, dataMap) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;

    if (!dataMap || Object.keys(dataMap).length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No Data</td></tr>';
        return;
    }

    tbody.innerHTML = Object.entries(dataMap).map(([k, v]) => {
        const wrStr = v.win_rate !== null ? `${v.win_rate.toFixed(1)}%` : '<span class="tag-insufficient">N/A</span>';
        const pnl = Number(v.pnl || 0);
        const pnlStr = `<span class="${pnl >= 0 ? 'val-green' : 'val-red'}">${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}</span>`;

        return `<tr>
            <td class="td-strong">${k}</td>
            <td>${v.trades}</td>
            <td>${wrStr}</td>
            <td>${pnlStr}</td>
        </tr>`;
    }).join('');
}


// ==========================================
// 13. GLOBAL POLLING & DRAWER CONTROLS
// ==========================================
function updateDashboard() {
    Promise.all([
        fetchDashboardData(),
        fetchSignals(),
        fetchPositions(),
        fetchTrades(),
        fetchMarketsData(),
        fetchStrategies(),
        fetchRiskData(),
        fetchAnalyticsData(),
        fetchActivity(),
        fetchOpenOrders(),
        initChart()
    ]).finally(() => {
        // finished iteration
    });
}

function openInspectorDrawer(title, payload) {
    const drawer = document.getElementById('inspector-drawer');
    const backdrop = document.getElementById('drawer-backdrop');
    const titleEl = document.getElementById('drawer-title');
    const bodyEl = document.getElementById('drawer-body');
    if (!drawer || !backdrop || !titleEl || !bodyEl) return;

    titleEl.innerHTML = title || "INSPECTOR • QUANT TELEMETRY";
    if (typeof payload === 'string') {
        bodyEl.innerHTML = payload;
    } else {
        bodyEl.innerHTML = `<div class="json-viewer">${JSON.stringify(payload, null, 2)}</div>`;
    }
    drawer.classList.add('open');
    drawer.classList.add('active');
    backdrop.classList.add('open');
    backdrop.classList.add('active');
}

function closeInspectorDrawer() {
    const drawer = document.getElementById('inspector-drawer');
    const backdrop = document.getElementById('drawer-backdrop');
    if (drawer) {
        drawer.classList.remove('open');
        drawer.classList.remove('active');
    }
    if (backdrop) {
        backdrop.classList.remove('open');
        backdrop.classList.remove('active');
    }
}

function exportTradesJSON() {
    fetch('/api/trades')
        .then(res => res.json())
        .then(data => {
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `binance_trade_ledger_${Date.now()}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        })
        .catch(err => console.error("Export JSON error:", err));
}

// ==========================================
// INITIALIZATION
// ==========================================
startClockLoop(); 
updateAudioUI();
initChart();
updateDashboard(); 
setInterval(updateDashboard, 2500);




