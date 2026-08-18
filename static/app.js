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
// AUDIO & TOAST NOTIFICATION ENGINE
// ==========================================
let audioAlertsEnabled = true;
let audioCtx = null;
const knownTradeOrderIds = new Set();
let rawEquityPoints = [];
let equityTimeframe = 'ALL';
let rawOpportunities = [];
let signalFilterState = 'ALL';

function getAudioContext() {
    if (!audioCtx) {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass) audioCtx = new AudioContextClass();
    }
    if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
    return audioCtx;
}

function playTradeChime() {
    if (!audioAlertsEnabled) return;
    try {
        const ctx = getAudioContext();
        if (!ctx) return;
        
        const now = ctx.currentTime;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        
        osc.type = 'sine';
        osc.frequency.setValueAtTime(587.33, now); // D5
        osc.frequency.exponentialRampToValueAtTime(880.00, now + 0.12); // A5
        
        gain.gain.setValueAtTime(0.15, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
        
        osc.connect(gain);
        gain.connect(ctx.destination);
        
        osc.start(now);
        osc.stop(now + 0.35);
    } catch (e) {
        console.warn("Audio chime error:", e);
    }
}

function toggleAudioAlerts() {
    audioAlertsEnabled = !audioAlertsEnabled;
    const btn = document.getElementById('audio-toggle-btn');
    const txt = document.getElementById('audio-status-txt');
    if (btn) {
        if (audioAlertsEnabled) {
            btn.classList.add('active');
            if (txt) txt.innerText = 'Audio ON';
            playTradeChime();
        } else {
            btn.classList.remove('active');
            if (txt) txt.innerText = 'Audio OFF';
        }
    }
}

function showTradeToast(trade) {
    const container = document.getElementById('trade-toast-container');
    if (!container) return;
    
    playTradeChime();
    
    const toast = document.createElement('div');
    toast.className = 'trade-toast';
    const isWin = (trade.pnl || 0) >= 0;
    const pnlTxt = trade.pnl !== undefined ? (isWin ? `+$${Number(trade.pnl).toFixed(2)}` : `-$${Math.abs(trade.pnl).toFixed(2)}`) : '';
    
    toast.innerHTML = `
        <div class="toast-header">
            <div class="toast-title">⚡ ${trade.status === 'CLOSED' ? 'TRADE CLOSED' : 'ORDER EXECUTED'}</div>
            <div class="toast-time">${new Date().toLocaleTimeString()}</div>
        </div>
        <div class="toast-body">
            <div class="toast-detail-row">
                <span>Symbol / Side</span>
                <span class="toast-detail-val">${trade.symbol} • ${trade.action || trade.side}</span>
            </div>
            <div class="toast-detail-row">
                <span>Price / Qty</span>
                <span class="toast-detail-val">${Number(trade.entry_price || trade.price || 0).toFixed(4)} (${trade.quantity})</span>
            </div>
            ${pnlTxt ? `<div class="toast-detail-row"><span>Realized PnL</span><span class="toast-detail-val ${isWin ? 'val-green' : 'val-red'}">${pnlTxt}</span></div>` : ''}
        </div>
    `;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        setTimeout(() => toast.remove(), 400);
    }, 5000);
}

async function exportTradesJSON() {
    const data = await apiClient.get('/api/trades');
    if (!data || !data.positions) return;
    const blob = new Blob([JSON.stringify(data.positions, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'binance_trade_ledger.json';
    a.click();
    URL.revokeObjectURL(url);
}

function setEquityTimeframe(tf) {
    equityTimeframe = tf;
    document.querySelectorAll('.btn-tf').forEach(b => b.classList.remove('active'));
    const btn = document.getElementById('tf-' + tf.toLowerCase());
    if (btn) btn.classList.add('active');
    renderEquityChart();
}

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

        // Risk View
        const rkDaily = document.getElementById('rk-daily');
        if (rkDaily) {
            rkDaily.innerText = formatCurrency(data.realized_pnl + data.unrealized_pnl);
            rkDaily.className = 'r-val ' + ((data.realized_pnl + data.unrealized_pnl) >= 0 ? 'val-green' : 'val-red');
        }
        const rkMdd = document.getElementById('rk-mdd');
        if (rkMdd) rkMdd.innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        const rkPos = document.getElementById('rk-pos');
        if (rkPos) rkPos.innerText = data.open_positions || 0;

        // Full Risk Page
        const rviewUsed = document.getElementById('rview-used');
        if (rviewUsed) rviewUsed.innerText = (data.risk_used || 0).toFixed(2) + '%';
        const rkAvail = document.getElementById('rk-avail');
        if (rkAvail) rkAvail.innerText = (data.available_risk || 0).toFixed(2) + '%'; 
        const rviewMdd = document.getElementById('rview-mdd');
        if (rviewMdd) rviewMdd.innerText = (data.max_drawdown || 0).toFixed(2) + '%';
        const rviewPos = document.getElementById('rview-pos');
        if (rviewPos) rviewPos.innerText = data.open_positions || 0;

        // Capital Allocation Transparency Bar
        const cashVal = data.cash || 0;
        const cryptoVal = data.crypto_holdings_value || 0;
        const totalVal = (cashVal + cryptoVal) || data.equity || 1;
        const cashPct = Math.min(100, Math.max(0, (cashVal / totalVal) * 100));
        const cryptoPct = Math.min(100, Math.max(0, 100 - cashPct));

        const allocCashTxt = document.getElementById('alloc-cash-txt');
        const allocCryptoTxt = document.getElementById('alloc-crypto-txt');
        const barCash = document.getElementById('alloc-bar-cash');
        const barCrypto = document.getElementById('alloc-bar-crypto');

        if (allocCashTxt) allocCashTxt.innerHTML = `Liquid USDT: <strong>${formatCurrency(cashVal)} (${cashPct.toFixed(1)}%)</strong>`;
        if (allocCryptoTxt) allocCryptoTxt.innerHTML = `Active Spot Trades: <strong>${formatCurrency(cryptoVal)} (${cryptoPct.toFixed(1)}%)</strong>`;
        if (barCash) barCash.style.width = `${cashPct.toFixed(1)}%`;
        if (barCrypto) barCrypto.style.width = `${cryptoPct.toFixed(1)}%`;

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
    } catch (e) {
        console.error("Failed to fetch signals:", e);
    }
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
        tbody.innerHTML = '<tr><td colspan="15" class="empty-state">NO SIGNAL EVENTS YET</td></tr>';
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
        tbody.innerHTML = '<tr><td colspan="15" class="empty-state">NO SIGNALS MATCHING ACTIVE FILTERS</td></tr>';
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
        if (fullBody) fullBody.innerHTML = '<tr><td colspan="13" class="empty-state">NO OPEN POSITIONS</td></tr>';
        if (dashBody) dashBody.innerHTML = '<tr><td colspan="8" class="empty-state">NO OPEN POSITIONS</td></tr>';
        document.getElementById('pos-count').innerText = '0';
        document.getElementById('pos-exposure').innerText = '$0.00';
        document.getElementById('pos-upnl').innerText = '$0.00';
        document.getElementById('pos-upnl').className = 'metric-value val-neutral';
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
        const uPnlClass = uPnl > 0 ? 'val-green' : (uPnl < 0 ? 'val-red' : '');
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
            <td class="val-red">${slStr}</td>
            <td class="val-green">${tpStr}</td>
            <td>${durStr}</td>
            <td><span class="tag tag-qualified">OPEN</span></td>
        </tr>`;
    }).join('');

    const dashRows = positions.map(p => {
        const side = (p.side || p.action || 'BUY').toUpperCase();
        const sideClass = (side === 'BUY' || side === 'LONG') ? 'tag tag-long' : 'tag tag-short';
        const entryPx = Number(p.entry_price || 0);
        const currPx = Number(p.current_price || entryPx);
        const uPnl = Number(p.current_unrealized_pnl || p.pnl || 0);
        const uPnlStr = `<span class="${uPnl >= 0 ? 'val-green' : 'val-red'}">${uPnl >= 0 ? '+' : ''}${formatCurrency(uPnl)}</span>`;

        return `<tr>
            <td class="td-strong">${p.symbol}</td>
            <td><span class="${sideClass}">${side}</span></td>
            <td>${entryPx.toFixed(4)}</td>
            <td>${currPx.toFixed(4)}</td>
            <td>${p.quantity}</td>
            <td>${uPnlStr}</td>
            <td>${p.stop_loss || p.sl || '-'}</td>
            <td>${p.take_profit || p.tp || '-'}</td>
        </tr>`;
    }).join('');

    if (fullBody) fullBody.innerHTML = fullRows;
    if (dashBody) dashBody.innerHTML = dashRows;

    document.getElementById('pos-count').innerText = positions.length;
    document.getElementById('pos-exposure').innerText = formatCurrency(totalExposure);
    applyColor(document.getElementById('pos-upnl'), totalUnrealized);
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
// 5. HISTORICAL TRADES TERMINAL & LIFECYCLE DRAWER
// ==========================================
let allRawTrades = [];

async function fetchTrades() {
    try {
        let trades = [];
        // First try the 40-field canonical trade history
        const res = await apiClient.get('/api/trade-history');
        if (res && Array.isArray(res.trades)) {
            trades = res.trades;
        }

        // Fallback to /api/trades
        if (trades.length === 0) {
            const legRes = await apiClient.get('/api/trades');
            if (legRes && Array.isArray(legRes.positions)) {
                trades = legRes.positions.filter(p => p.status === 'CLOSED');
            }
        }

        allRawTrades = trades;
        renderTradesTable(trades);
        updateTradeSummaryKPIs(trades);

        // Fetch analytics endpoint if available for profit factor and average win/loss
        const analytics = await apiClient.get('/api/telemetry/analytics');
        if (analytics && analytics.analytics) {
            const an = analytics.analytics;
            if (an.profit_factor !== undefined) document.getElementById('tr-pf').innerText = an.profit_factor;
            if (an.winning_trades !== undefined) document.getElementById('tr-wins').innerText = an.winning_trades;
            if (an.losing_trades !== undefined) document.getElementById('tr-loss').innerText = an.losing_trades;
        }
    } catch (e) {
        console.error("Failed to fetch trades:", e);
    }
}

function updateTradeSummaryKPIs(trades) {
    const total = trades.length;
    let wins = 0, losses = 0;
    let grossWin = 0, grossLoss = 0;
    let totalFees = 0;
    let netPnL = 0;

    trades.forEach(t => {
        const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl !== undefined ? t.pnl : 0));
        const fee = Number(t.fees || t.total_fees || 0);
        const gross = Number(t.gross_pnl !== undefined ? t.gross_pnl : (net + fee));

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

    const winRate = total > 0 ? (wins / total) * 100 : 0;
    const avgWin = wins > 0 ? grossWin / wins : 0;
    const avgLoss = losses > 0 ? grossLoss / losses : 0;
    const pf = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? 999.0 : 0);

    const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (el) el.innerText = v;
    };

    setVal('tr-total', total);
    setVal('tr-rate', winRate.toFixed(2) + '%');
    setVal('tr-wins', wins);
    setVal('tr-loss', losses);
    setVal('tr-fees', formatCurrency(totalFees));
    setVal('tr-avg-win', formatCurrency(avgWin));
    setVal('tr-avg-loss', formatCurrency(avgLoss));
    setVal('tr-pf', pf.toFixed(2));

    const netEl = document.getElementById('tr-net');
    if (netEl) {
        netEl.innerText = formatCurrency(netPnL);
        netEl.className = 'metric-value ' + (netPnL > 0 ? 'val-green' : (netPnL < 0 ? 'val-red' : 'val-neutral'));
    }

    const realEl = document.getElementById('tr-realized');
    if (realEl) {
        realEl.innerText = formatCurrency(netPnL + totalFees);
        realEl.className = 'metric-value ' + ((netPnL + totalFees) > 0 ? 'val-green' : ((netPnL + totalFees) < 0 ? 'val-red' : 'val-neutral'));
    }
}

function renderTradesTable(trades) {
    const fullBody = document.getElementById('trades-full-body');
    const dashBody = document.getElementById('recent-trades-body');

    if (!trades || trades.length === 0) {
        if (fullBody) fullBody.innerHTML = '<tr><td colspan="13" class="empty-state">NO CLOSED TRADES YET</td></tr>';
        if (dashBody) dashBody.innerHTML = '<tr><td colspan="9" class="empty-state">NO CLOSED TRADES YET</td></tr>';
        return;
    }

    const sorted = [...trades].sort((a, b) => {
        const tsA = new Date(a.close_time || a.timestamp || 0).getTime();
        const tsB = new Date(b.close_time || b.timestamp || 0).getTime();
        return tsB - tsA;
    });

    const fullRows = sorted.map((t, idx) => {
        const tradeId = t.trade_id || t.order_id || `TRD-${idx}`;
        const opened = t.fill_time || t.signal_time || t.entry_timestamp || t.timestamp;
        const closed = t.close_time || t.exit_timestamp || t.timestamp;

        const openStr = opened ? formatTime(opened) : '-';
        const closeStr = closed ? formatTime(closed) : '-';

        const sym = t.symbol || '-';
        const tf = t.timeframe || '5m';
        const strat = t.strategy || 'ADX_EMA';
        const side = (t.side || t.action || 'BUY').toUpperCase();
        const sideClass = (side === 'BUY' || side === 'LONG') ? 'tag tag-long' : 'tag tag-short';

        const entryPx = Number(t.entry_price || 0).toFixed(4);
        const exitPx = Number(t.exit_price || 0).toFixed(4);
        const qty = t.quantity || '-';
        const feeStr = formatCurrency(t.fees || t.total_fees || 0);

        const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
        const netClass = net > 0 ? 'val-green' : (net < 0 ? 'val-red' : '');
        const netStr = `<span class="${netClass}">${net >= 0 ? '+' : ''}${formatCurrency(net)}</span>`;

        const isWin = net >= 0;
        const statusTag = `<span class="tag ${isWin ? 'tag-win' : 'tag-loss'}">${isWin ? 'WIN' : 'LOSS'}</span>`;

        return `<tr style="cursor: pointer;" onclick="inspectTradeByIndex(${idx})" title="Click to view Complete Visual Lifecycle">
            <td class="td-strong" style="font-family: var(--font-mono);">${tradeId}</td>
            <td>${openStr}</td>
            <td>${closeStr}</td>
            <td class="td-strong">${sym}</td>
            <td>${tf}</td>
            <td>${strat}</td>
            <td><span class="${sideClass}">${side}</span></td>
            <td>${entryPx}</td>
            <td>${exitPx}</td>
            <td>${qty}</td>
            <td>${feeStr}</td>
            <td>${netStr}</td>
            <td>${statusTag}</td>
        </tr>`;
    }).join('');

    const dashRows = sorted.slice(0, 10).map(t => {
        const closed = t.close_time || t.exit_timestamp || t.timestamp;
        const closeStr = closed ? formatTime(closed) : '-';
        const side = (t.side || t.action || 'BUY').toUpperCase();
        const sideClass = (side === 'BUY' || side === 'LONG') ? 'tag tag-long' : 'tag tag-short';
        const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
        const netStr = `<span class="${net >= 0 ? 'val-green' : 'val-red'}">${net >= 0 ? '+' : ''}${formatCurrency(net)}</span>`;

        return `<tr>
            <td>${closeStr}</td>
            <td class="td-strong">${t.symbol}</td>
            <td>${t.timeframe || '5m'}</td>
            <td>${t.strategy || 'ADX_EMA'}</td>
            <td><span class="${sideClass}">${side}</span></td>
            <td>${Number(t.entry_price || 0).toFixed(4)}</td>
            <td>${Number(t.exit_price || 0).toFixed(4)}</td>
            <td>${netStr}</td>
            <td><span class="tag ${net >= 0 ? 'tag-win' : 'tag-loss'}">${net >= 0 ? 'WIN' : 'LOSS'}</span></td>
        </tr>`;
    }).join('');

    if (fullBody) fullBody.innerHTML = fullRows;
    if (dashBody) dashBody.innerHTML = dashRows;
}

function inspectTradeByIndex(idx) {
    if (allRawTrades && allRawTrades[idx]) {
        inspectTrade(allRawTrades[idx]);
    }
}

function inspectTrade(trade) {
    const sym = trade.symbol || '-';
    const tradeId = trade.trade_id || trade.order_id || 'TRD';
    const strat = trade.strategy || 'ADX_EMA';
    const tf = trade.timeframe || '5m';
    const side = (trade.side || trade.action || 'BUY').toUpperCase();

    const net = Number(trade.net_pnl !== undefined ? trade.net_pnl : (trade.pnl || 0));
    const gross = Number(trade.gross_pnl !== undefined ? trade.gross_pnl : (net + Number(trade.fees || 0)));
    const fees = Number(trade.fees || trade.total_fees || 0);

    const entryPx = Number(trade.entry_price || 0);
    const exitPx = Number(trade.exit_price || 0);
    const slPx = Number(trade.stop_loss || trade.sl || 0);
    const tpPx = Number(trade.take_profit || trade.tp || 0);

    const balOpen = Number(trade.balance_before_entry || 0);
    const eqOpen = Number(trade.equity_before_entry || balOpen || 0);
    const balClose = Number(trade.balance_after_exit || (balOpen + net) || 0);
    const eqClose = Number(trade.equity_after_exit || balClose || 0);

    const closeReason = trade.close_reason || trade.exit_reason || (net >= 0 ? 'TAKE_PROFIT_HIT' : 'STOP_LOSS_HIT');
    const durStr = trade.duration || (trade.duration_seconds ? `${Math.round(trade.duration_seconds)}s` : '-');

    const drawerHtml = `
        <!-- VISUAL TRADE LIFECYCLE STEPPER -->
        <div class="inspector-card">
            <div class="inspector-card-header">
                <span>🔄 Visual Lifecycle Flow</span>
                <span class="tag ${net >= 0 ? 'tag-win' : 'tag-loss'}">${net >= 0 ? 'WIN' : 'LOSS'}</span>
            </div>
            <div class="lifecycle-stepper">
                <div class="lifecycle-node completed">
                    <div class="lifecycle-icon-wrap">1</div>
                    <div class="lifecycle-content">
                        <div class="lifecycle-step-title"><span>SIGNAL GENERATED</span><span class="badge badge-mono">${trade.signal_time ? formatTime(trade.signal_time) : '-'}</span></div>
                        <div class="lifecycle-step-desc">Strategy: ${strat} • ${tf} • Side: ${side}</div>
                    </div>
                </div>

                <div class="lifecycle-node completed">
                    <div class="lifecycle-icon-wrap">2</div>
                    <div class="lifecycle-content">
                        <div class="lifecycle-step-title"><span>PROFITABILITY GATE</span><span class="tag tag-qualified">ACCEPTED</span></div>
                        <div class="lifecycle-step-desc">Expected net edge evaluated above fees & slippage hurdle</div>
                    </div>
                </div>

                <div class="lifecycle-node completed">
                    <div class="lifecycle-icon-wrap">3</div>
                    <div class="lifecycle-content">
                        <div class="lifecycle-step-title"><span>RISK ENGINE GATE</span><span class="tag tag-qualified">ACCEPTED</span></div>
                        <div class="lifecycle-step-desc">Exposure and max drawdown limits verified</div>
                    </div>
                </div>

                <div class="lifecycle-node completed">
                    <div class="lifecycle-icon-wrap">4</div>
                    <div class="lifecycle-content">
                        <div class="lifecycle-step-title"><span>ORDER SUBMITTED</span><span class="badge badge-mono">${trade.order_submit_time ? formatTime(trade.order_submit_time) : '-'}</span></div>
                        <div class="lifecycle-step-desc">Entry Order: ${trade.entry_order_id || tradeId}</div>
                    </div>
                </div>

                <div class="lifecycle-node completed">
                    <div class="lifecycle-icon-wrap">5</div>
                    <div class="lifecycle-content">
                        <div class="lifecycle-step-title"><span>ORDER FILLED</span><span class="badge badge-mono">${trade.fill_time ? formatTime(trade.fill_time) : '-'}</span></div>
                        <div class="lifecycle-step-desc">Price: ${entryPx.toFixed(4)} • Qty: ${trade.quantity}</div>
                    </div>
                </div>

                <div class="lifecycle-node completed">
                    <div class="lifecycle-icon-wrap">6</div>
                    <div class="lifecycle-content">
                        <div class="lifecycle-step-title"><span>POSITION OPENED</span><span class="tag tag-qualified">SPOT HELD</span></div>
                        <div class="lifecycle-step-desc">Notional: ${formatCurrency(entryPx * Number(trade.quantity || 0))}</div>
                    </div>
                </div>

                <div class="lifecycle-node completed">
                    <div class="lifecycle-icon-wrap">7</div>
                    <div class="lifecycle-content">
                        <div class="lifecycle-step-title"><span>PROTECTION ACTIVE</span><span class="tag tag-qualified">OCO PLACED</span></div>
                        <div class="lifecycle-step-desc">SL: ${slPx > 0 ? slPx.toFixed(4) : '-'} | TP: ${tpPx > 0 ? tpPx.toFixed(4) : '-'}</div>
                    </div>
                </div>

                <div class="lifecycle-node completed">
                    <div class="lifecycle-icon-wrap">8</div>
                    <div class="lifecycle-content">
                        <div class="lifecycle-step-title"><span>POSITION CLOSED</span><span class="badge badge-mono">${trade.close_time ? formatTime(trade.close_time) : '-'}</span></div>
                        <div class="lifecycle-step-desc">Exit Price: ${exitPx.toFixed(4)} • Reason: ${closeReason}</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- BALANCE & EQUITY AT ENTRY & EXIT -->
        <div class="inspector-card">
            <div class="inspector-card-header"><span>💼 Balance & Equity Milestones</span></div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Balance at Open</span><span class="inspector-val">${balOpen > 0 ? formatCurrency(balOpen) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Equity at Open</span><span class="inspector-val">${eqOpen > 0 ? formatCurrency(eqOpen) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Balance at Close</span><span class="inspector-val">${balClose > 0 ? formatCurrency(balClose) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Equity at Close</span><span class="inspector-val">${eqClose > 0 ? formatCurrency(eqClose) : '-'}</span></div>
            </div>
        </div>

        <!-- EXECUTION SPECS & PERFORMANCE -->
        <div class="inspector-card">
            <div class="inspector-card-header"><span>📊 Execution & Returns</span></div>
            <div class="inspector-grid-2">
                <div class="inspector-row"><span class="inspector-lbl">Entry Price</span><span class="inspector-val">${entryPx.toFixed(4)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Exit Price</span><span class="inspector-val">${exitPx.toFixed(4)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Stop Loss</span><span class="inspector-val val-red">${slPx > 0 ? slPx.toFixed(4) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Take Profit</span><span class="inspector-val val-green">${tpPx > 0 ? tpPx.toFixed(4) : '-'}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Gross PnL</span><span class="inspector-val ${gross >= 0 ? 'val-green' : 'val-red'}">${gross >= 0 ? '+' : ''}${formatCurrency(gross)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Total Fees</span><span class="inspector-val val-amber">${formatCurrency(fees)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Net Return</span><span class="inspector-val ${net >= 0 ? 'val-green' : 'val-red'} td-strong">${net >= 0 ? '+' : ''}${formatCurrency(net)}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Trade Duration</span><span class="inspector-val">${durStr}</span></div>
                <div class="inspector-row"><span class="inspector-lbl">Close Reason</span><span class="inspector-val td-strong">${closeReason}</span></div>
            </div>
        </div>
    `;

    openInspectorDrawer(`TRADE DETAIL • ${sym} (${tradeId})`, drawerHtml);
}


// ==========================================
// 6. OVERVIEW & SCANNER DATA POLLING
// ==========================================


async function fetchScanner() {
    try {
        const data = await apiClient.get('/api/scanner');
        if (!data) return;
        
        // Funnel Pipeline
        const fnMrk = document.getElementById('fn-mrk');
        if (fnMrk) fnMrk.innerText = data.TOTAL_CANDLES || 0;
        const fnSig = document.getElementById('fn-signals');
        if (fnSig) fnSig.innerText = data.TOTAL_SIGNALS || 0;
        
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
        
        // Markets Data (Matrix)
        let dataReceivingCount = 0;
        let evaluatedCount = 0;
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
        if (marketBody) {
            if (marketRows.length > 0) marketBody.innerHTML = marketRows.join('');
            else marketBody.innerHTML = '<tr><td colspan="6" class="empty-state">No active symbols scanned</td></tr>';
        }
        
        const marketFullBody = document.getElementById('market-full-body');
        if (marketFullBody) {
            if (marketFullRows.length > 0) marketFullBody.innerHTML = marketFullRows.join('');
            else marketFullBody.innerHTML = '<tr><td colspan="9" class="empty-state">AWAITING MARKET DATA</td></tr>';
        }

        if (data.last_evaluation) {
            for (const sym of Object.keys(data.last_evaluation)) {
                evaluatedCount++;
            }
        }
        
        const scEval = document.getElementById('sc-eval-ratio');
        if (scEval) scEval.innerText = `${evaluatedCount} Symbols`;
        if (fnMrk) fnMrk.innerText = dataReceivingCount || 0;
        
        // Ticker
        const tickerContent = document.getElementById('bottom-ticker-content');
        if (data.market_data && Object.keys(data.market_data).length > 0 && tickerContent) {
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
        
        // Strategy Metrics
        const stratBody = document.getElementById('strategy-metrics-body');
        const stratFullBody = document.getElementById('strat-full-body');
        if (data.strategy_metrics && Object.keys(data.strategy_metrics).length > 0) {
            let stratRows = [];
            let stratFullRows = [];
            for (const [strat, m] of Object.entries(data.strategy_metrics)) {
                const winRate = m.fills > 0 ? (m.wins || 0) / m.fills : 0;
                const pnlStr = m.PnL ? (m.PnL > 0 ? `<span class="val-green">+${formatCurrency(m.PnL)}</span>` : `<span class="val-red">${formatCurrency(m.PnL)}</span>`) : '-';
                
                const row = `<tr>
                    <td class="td-strong">${strat}</td>
                    <td>${m.evaluations || (m.signals || 0) + (m.HOLD || 0)}</td>
                    <td>${m.BUY || 0}</td>
                    <td>${m.SELL || 0}</td>
                    <td>${m.HOLD || 0}</td>
                    <td>${m.qualified || 0}</td>
                    <td>${m.rejected || 0}</td>
                    <td>${m.orders || 0}</td>
                    <td>${m.fills || 0}</td>
                    <td>${formatPct(winRate)}</td>
                    <td>${pnlStr}</td>
                </tr>`;
                stratRows.push(row);
                stratFullRows.push(row);
            }
            if (stratBody) stratBody.innerHTML = stratRows.join('');
            if (stratFullBody) stratFullBody.innerHTML = stratFullRows.join('');
        } else {
            if (stratBody) stratBody.innerHTML = '<tr><td colspan="6" class="empty-state">NO STRATEGY DATA</td></tr>';
            if (stratFullBody) stratFullBody.innerHTML = '<tr><td colspan="11" class="empty-state">NO STRATEGY DATA</td></tr>';
        }
        
        // Timeframe Metrics
        const tfBody = document.getElementById('timeframe-metrics-body');
        if (data.timeframe_metrics && Object.keys(data.timeframe_metrics).length > 0) {
            let tfRows = [];
            for (const [tf, m] of Object.entries(data.timeframe_metrics)) {
                tfRows.push(`<tr>
                    <td>${tf}</td>
                    <td>${data.TOTAL_CANDLES || 0}</td>
                    <td>${m.evaluations || (m.signals || 0) + (m.HOLD || 0)}</td>
                    <td>${m.BUY || 0}</td>
                    <td>${m.orders || 0}</td>
                    <td>${m.fills || 0}</td>
                </tr>`);
            }
            if (tfBody) tfBody.innerHTML = tfRows.join('');
        } else {
            if (tfBody) tfBody.innerHTML = '<tr><td colspan="6" class="empty-state">NO METRICS AVAILABLE</td></tr>';
        }
        
        // Best current opportunities table on Overview
        const oppBody = document.getElementById('opp-short-body');
        const rawOpps = data.top_opportunities || [];
        if (oppBody) {
            if (rawOpps.length === 0) {
                oppBody.innerHTML = '<tr><td colspan="11" class="empty-state">No Qualifying Signals Yet</td></tr>';
            } else {
                oppBody.innerHTML = rawOpps.slice(0, 5).map(o => {
                    const side = (o.side || 'BUY').toUpperCase();
                    const sideClass = (side === 'BUY' || side === 'LONG') ? 'tag tag-long' : 'tag tag-short';
                    const net = Number(o.expected_net_return || 0);
                    const netStr = net !== 0 ? `<span class="${net > 0 ? 'val-green' : 'val-red'}">${net > 0 ? '+' : ''}${(net * 100).toFixed(2)}%</span>` : '-';
                    const decClass = o.decision === 'ACCEPTED' ? 'tag tag-qualified' : 'tag tag-rejected';
                    const shTs = o.timestamp ? String(o.timestamp).substring(11, 19) : '-';

                    return `<tr>
                        <td>${shTs}</td>
                        <td class="td-strong">${o.symbol}</td>
                        <td>${o.timeframe || '5m'}</td>
                        <td>${o.strategy || 'ADX_EMA'}</td>
                        <td><span class="${sideClass}">${side}</span></td>
                        <td>${Number(o.current_price || 0).toFixed(4)}</td>
                        <td>${Number(o.sl || 0) > 0 ? Number(o.sl).toFixed(4) : '-'}</td>
                        <td>${Number(o.tp || 0) > 0 ? Number(o.tp).toFixed(4) : '-'}</td>
                        <td>${Number(o.confidence || 0) > 0 ? (Number(o.confidence) * 100).toFixed(1) + '%' : '-'}</td>
                        <td>${netStr}</td>
                        <td><span class="${decClass}">${o.decision || '-'}</span></td>
                    </tr>`;
                }).join('');
            }
        }

    } catch (e) {
        console.error("Failed to fetch scanner stats:", e);
    }
}


// ==========================================
// 7. CHARTS & HISTOGRAMS
// ==========================================
let equityChartInst = null;
let pnlHistChartInst = null;
let rejPieChartInst = null;

function renderEquityChart() {
    const ctx = document.getElementById('equityChart');
    if (!ctx || !rawEquityPoints || rawEquityPoints.length === 0) return;

    let points = rawEquityPoints;
    const now = Date.now();
    if (equityTimeframe === '1D') {
        points = rawEquityPoints.filter(p => now - p.time <= 86400000);
    } else if (equityTimeframe === '7D') {
        points = rawEquityPoints.filter(p => now - p.time <= 7 * 86400000);
    }
    if (points.length === 0) points = rawEquityPoints;

    const labels = points.map(p => {
        const d = new Date(p.time);
        return d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
    });
    const data = points.map(p => p.equity);

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
                borderColor: '#2563eb',
                backgroundColor: 'rgba(37, 99, 235, 0.1)',
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
                    backgroundColor: '#1e293b',
                    titleColor: '#f8fafc',
                    bodyColor: '#94a3b8',
                    borderColor: '#334155',
                    borderWidth: 1
                }
            },
            scales: {
                x: { display: false },
                y: { 
                    display: true, 
                    position: 'right',
                    grid: { color: '#1e293b' },
                    ticks: {
                        color: '#64748b',
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

async function initChart() {
    const eqData = await apiClient.get('/api/equity');
    if (!eqData || eqData.length === 0) return;
    rawEquityPoints = eqData;
    renderEquityChart();
}


// ==========================================
// 8. GLOBAL POLLING & DRAWER CONTROLS
// ==========================================
function updateDashboard() {
    Promise.all([
        fetchDashboardData(),
        fetchSignals(),
        fetchPositions(),
        fetchTrades(),
        fetchOpenOrders(),
        fetchScanner(),
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

// ==========================================
// INITIALIZATION
// ==========================================
startClockLoop(); 
initChart();
updateDashboard(); 
setInterval(updateDashboard, 2500);


