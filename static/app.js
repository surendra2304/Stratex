// ==============================================================================
// QUANT ENGINE PRO - ADVANCED SPA CONTROLLER & REAL-TIME DASHBOARD
// ==============================================================================

// ─── STATE MANAGEMENT ─────────────────────────────────────────────────────────
let appState = {
    status: null,
    trades: null,
    holdings: null,
    openOrders: null,
    scanner: null,
    equityHistory: [],
    charts: {
        equity: null,
        allocation: null,
        pnlHist: null
    }
};

let serverTimeOffset = 0;
let botStartTimeMs = null;

// ─── DOM INITIALIZATION ───────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initRefreshButton();
    startClock();
    initCharts();
    
    // Initial fetch and continuous poll
    fetchAllData();
    setInterval(fetchAllData, 3000);
});

// ─── NAVIGATION (SPA ROUTING) ─────────────────────────────────────────────────
function initNavigation() {
    const navItems = document.querySelectorAll(".nav-item");
    const views = document.querySelectorAll(".view-container");
    const pageTitle = document.getElementById("page-title");
    const pageIcon = document.getElementById("page-icon");

    const iconMap = {
        "overview": "📊",
        "holdings": "💰",
        "positions": "⚡",
        "trades": "📜",
        "signals": "📡",
        "markets": "🌐",
        "strategies": "🎯",
        "risk": "🛡️",
        "analytics": "📈",
        "diagnostics": "⚙️"
    };

    navItems.forEach(item => {
        item.addEventListener("click", (e) => {
            e.preventDefault();
            const targetView = item.getAttribute("data-view");
            activateView(targetView);
        });
    });

    // Handle deep links like #holdings
    window.addEventListener("hashchange", () => {
        const hash = window.location.hash.replace("#", "");
        if (hash) activateView(hash);
    });

    // Handle internal links within panels
    document.addEventListener("click", (e) => {
        const link = e.target.closest("a[data-view]");
        if (link) {
            e.preventDefault();
            activateView(link.getAttribute("data-view"));
        }
    });

    function activateView(targetView) {
        if (!targetView) return;
        navItems.forEach(nav => nav.classList.toggle("active", nav.getAttribute("data-view") === targetView));
        views.forEach(view => view.classList.toggle("active", view.id === `view-${targetView}`));
        
        const activeNav = document.querySelector(`.nav-item[data-view="${targetView}"]`);
        if (activeNav) {
            pageTitle.innerText = activeNav.querySelector(".nav-label").innerText;
            pageIcon.innerText = iconMap[targetView] || "⚡";
        }
        window.location.hash = targetView;
    }
}

function initRefreshButton() {
    const btn = document.getElementById("btn-manual-refresh");
    if (btn) {
        btn.addEventListener("click", () => {
            btn.innerHTML = "<span>↻</span> Updating...";
            btn.style.opacity = "0.7";
            fetchAllData().finally(() => {
                setTimeout(() => {
                    btn.innerHTML = "<span>↻</span> Refresh";
                    btn.style.opacity = "1";
                }, 400);
            });
        });
    }
}

// ─── UTILITIES & FORMATTERS ───────────────────────────────────────────────────
const formatUSD = (num) => {
    const val = Number(num);
    if (isNaN(val)) return "$0.00";
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(val);
};

const formatQty = (num) => {
    const val = Number(num);
    if (isNaN(val)) return "0";
    return val >= 1000 ? val.toLocaleString('en-US', { maximumFractionDigits: 2 }) : val.toFixed(4);
};

const formatPct = (num) => {
    const val = Number(num);
    if (isNaN(val)) return "0.00%";
    return val.toFixed(2) + "%";
};

const formatDateTimeIST = (ts) => {
    if (!ts) return "-";
    try {
        const d = new Date(ts);
        return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false });
    } catch {
        return ts;
    }
};

const formatTimeOnly = (ts) => {
    if (!ts) return "-";
    try {
        const d = new Date(ts);
        return d.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false });
    } catch {
        return ts;
    }
};

// ─── CLOCK & UPTIME LOOP ──────────────────────────────────────────────────────
function startClock() {
    function tick() {
        const nowMs = Date.now() + serverTimeOffset;
        const d = new Date(nowMs);
        const timeStr = d.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false });
        
        const navClock = document.getElementById("nav-clock");
        if (navClock) navClock.innerText = timeStr + " IST";

        if (botStartTimeMs) {
            const uptimeSec = Math.max(0, Math.floor((nowMs - botStartTimeMs) / 1000));
            const hrs = Math.floor(uptimeSec / 3600).toString().padStart(2, '0');
            const mins = Math.floor((uptimeSec % 3600) / 60).toString().padStart(2, '0');
            const secs = (uptimeSec % 60).toString().padStart(2, '0');
            const hdrUptime = document.getElementById("hdr-uptime");
            if (hdrUptime) hdrUptime.innerText = `${hrs}:${mins}:${secs}`;
        }
        requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
}

// ─── API DATA FETCHING ────────────────────────────────────────────────────────
async function apiGet(endpoint) {
    try {
        const res = await fetch(endpoint, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (e) {
        console.warn(`[API] Error on ${endpoint}:`, e);
        return null;
    }
}

async function fetchAllData() {
    const [statusData, tradesData, holdingsData, openOrdersData, scannerData, equityData] = await Promise.all([
        apiGet('/api/status'),
        apiGet('/api/trades'),
        apiGet('/api/holdings'),
        apiGet('/api/open-orders'),
        apiGet('/api/scanner'),
        apiGet('/api/equity')
    ]);

    if (statusData) {
        appState.status = statusData;
        if (statusData.server_time) {
            const serverMs = new Date(statusData.server_time).getTime();
            serverTimeOffset = serverMs - Date.now();
        }
        if (statusData.bot_start_time) {
            botStartTimeMs = new Date(statusData.bot_start_time).getTime();
        }
        renderStatus(statusData);
    }

    if (tradesData) {
        appState.trades = tradesData;
        renderTrades(tradesData);
    }

    if (holdingsData) {
        appState.holdings = holdingsData;
        renderHoldings(holdingsData);
    }

    if (openOrdersData) {
        appState.openOrders = openOrdersData;
        renderOpenOrders(openOrdersData);
    }

    if (scannerData) {
        appState.scanner = scannerData;
        renderScanner(scannerData);
    }

    if (equityData && Array.isArray(equityData)) {
        appState.equityHistory = equityData;
        renderEquityChart(equityData);
    }

    renderDiagnostics();
}

// ─── 1. RENDER STATUS & PORTFOLIO KPI CARDS ───────────────────────────────────
function renderStatus(data) {
    const totalEquity = data.equity || 0;
    const cash = data.cash || 0;
    const cryptoVal = data.crypto_holdings_value || 0;
    const realized = data.realized_pnl || 0;
    const unrealized = data.unrealized_pnl || 0;
    const openPosCount = data.open_positions || 0;

    // Header Quick Info
    const hdrEquity = document.getElementById("hdr-equity");
    const hdrCash = document.getElementById("hdr-cash");
    const hdrCrypto = document.getElementById("hdr-crypto");
    if (hdrEquity) hdrEquity.innerText = formatUSD(totalEquity);
    if (hdrCash) hdrCash.innerText = `Cash: ${formatUSD(cash)}`;
    if (hdrCrypto) hdrCrypto.innerText = `Crypto: ${formatUSD(cryptoVal)}`;

    // Engine Badge
    const engineDot = document.getElementById("hdr-engine-dot");
    const engineText = document.getElementById("hdr-engine-text");
    const engineBadge = document.getElementById("hdr-engine-badge");
    if (engineBadge) {
        if (data.safety_halt) {
            engineBadge.className = "engine-badge offline";
            if (engineDot) engineDot.className = "dot dot-red";
            if (engineText) engineText.innerText = "SAFETY HALT";
        } else if (data.engine_status === "ONLINE" || data.engine_healthy) {
            engineBadge.className = "engine-badge online";
            if (engineDot) engineDot.className = "dot dot-green";
            if (engineText) engineText.innerText = "ENGINE ONLINE (TESTNET)";
        } else {
            engineBadge.className = "engine-badge offline";
            if (engineDot) engineDot.className = "dot dot-red";
            if (engineText) engineText.innerText = "ENGINE OFFLINE";
        }
    }

    // Top KPI Cards
    const kpiEquity = document.getElementById("kpi-equity");
    const kpiCash = document.getElementById("kpi-cash");
    const kpiCrypto = document.getElementById("kpi-crypto");
    const kpiRealized = document.getElementById("kpi-realized");
    const kpiUnrealized = document.getElementById("kpi-unrealized");
    const kpiOpenCount = document.getElementById("kpi-open-count");

    if (kpiEquity) kpiEquity.innerText = formatUSD(totalEquity);
    if (kpiCash) kpiCash.innerText = formatUSD(cash);
    if (kpiCrypto) kpiCrypto.innerText = formatUSD(cryptoVal);
    
    if (kpiRealized) {
        kpiRealized.innerText = (realized >= 0 ? "+" : "") + formatUSD(realized);
        kpiRealized.className = "kpi-value mono " + (realized >= 0 ? "val-green" : "val-red");
    }

    if (kpiUnrealized) {
        kpiUnrealized.innerText = (unrealized >= 0 ? "+" : "") + formatUSD(unrealized);
        kpiUnrealized.className = "kpi-value mono " + (unrealized >= 0 ? "val-green" : "val-red");
    }

    if (kpiOpenCount) kpiOpenCount.innerText = openPosCount;

    // Capital Allocation Progress Bar
    const totalCap = cash + cryptoVal;
    if (totalCap > 0) {
        const cashPct = (cash / totalCap) * 100;
        const cryptoPct = (cryptoVal / totalCap) * 100;
        const barCash = document.getElementById("bar-cash");
        const barCrypto = document.getElementById("bar-crypto");
        if (barCash) barCash.style.width = `${cashPct.toFixed(1)}%`;
        if (barCrypto) barCrypto.style.width = `${cryptoPct.toFixed(1)}%`;

        const bannerStats = document.getElementById("banner-stats-text");
        if (bannerStats) {
            bannerStats.innerHTML = `<span>Liquid USDT: <strong>${formatUSD(cash)} (${cashPct.toFixed(1)}%)</strong></span> • <span>Active Holdings: <strong>${formatUSD(cryptoVal)} (${cryptoPct.toFixed(1)}%)</strong></span>`;
        }

        const kpiCashRatio = document.getElementById("kpi-cash-ratio");
        if (kpiCashRatio) kpiCashRatio.innerHTML = `<span>${cashPct.toFixed(1)}% of total portfolio</span>`;

        const kpiCryptoRatio = document.getElementById("kpi-crypto-ratio");
        if (kpiCryptoRatio) kpiCryptoRatio.innerHTML = `<span>${cryptoPct.toFixed(1)}% deployed in active trades</span>`;
    }

    // Sidebar System Health Dots
    const comp = data.components || {};
    const setDot = (id, ok) => {
        const el = document.getElementById(id);
        if (el) el.className = `dot ${ok ? 'dot-green' : 'dot-red'}`;
    };
    setDot("h-bn", comp.binance === "OK");
    setDot("h-ws", comp.data === "OK");
    setDot("h-md", comp.data === "OK");
    setDot("h-ex", comp.execution === "OK");
    setDot("h-st", comp.strategy === "OK");
    setDot("h-rs", true);

    // Active Positions Table on Dashboard
    renderActivePositions(data.open_positions_data || []);
}

// ─── 2. RENDER ACTIVE POSITIONS ───────────────────────────────────────────────
function renderActivePositions(positions) {
    const dashBody = document.getElementById("dash-active-pos-body");
    const fullBody = document.getElementById("positions-full-body");
    const navPosCount = document.getElementById("nav-pos-count");
    const activeBadge = document.getElementById("active-positions-badge");

    if (navPosCount) navPosCount.innerText = positions.length;
    if (activeBadge) activeBadge.innerText = `${positions.length} Active`;

    if (!positions || positions.length === 0) {
        const emptyRow = '<tr><td colspan="11" class="empty-state">No open positions currently active</td></tr>';
        if (dashBody) dashBody.innerHTML = '<tr><td colspan="8" class="empty-state">No open positions currently active</td></tr>';
        if (fullBody) fullBody.innerHTML = emptyRow;
        return;
    }

    let totalExposure = 0;
    let totalUpnl = 0;

    const dashRows = positions.map(p => {
        const uPnl = p.unrealized_pnl || 0;
        const uPnlStr = uPnl >= 0 ? `<span class="val-green">+${formatUSD(uPnl)}</span>` : `<span class="val-red">${formatUSD(uPnl)}</span>`;
        const sideClass = (p.side === "BUY" || p.side === "LONG") ? "tag tag-buy" : "tag tag-sell";
        const deployedVal = p.quantity * p.entry_price;
        totalExposure += deployedVal;
        totalUpnl += uPnl;

        const slStr = p.sl ? Number(p.sl).toFixed(4) : "-";
        const tpStr = p.tp ? Number(p.tp).toFixed(4) : "-";

        return `<tr>
            <td class="td-strong"><strong style="color: #fff;">${p.symbol}</strong></td>
            <td><span class="${sideClass}">${p.side}</span></td>
            <td class="mono">${Number(p.entry_price).toFixed(4)}</td>
            <td class="mono val-cyan">${Number(p.current_price || p.entry_price).toFixed(4)}</td>
            <td class="mono">${formatQty(p.quantity)}</td>
            <td class="mono">${formatUSD(deployedVal)}</td>
            <td class="mono">${uPnlStr}</td>
            <td class="mono"><span class="tag tag-oco">SL: ${slStr} | TP: ${tpStr}</span></td>
        </tr>`;
    }).join('');

    const fullRows = positions.map(p => {
        const uPnl = p.unrealized_pnl || 0;
        const uPnlStr = uPnl >= 0 ? `<span class="val-green">+${formatUSD(uPnl)}</span>` : `<span class="val-red">${formatUSD(uPnl)}</span>`;
        const sideClass = (p.side === "BUY" || p.side === "LONG") ? "tag tag-buy" : "tag tag-sell";
        const deployedVal = p.quantity * p.entry_price;

        return `<tr>
            <td class="td-strong"><strong style="color: #fff;">${p.symbol}</strong></td>
            <td><span class="tag" style="background: rgba(59,130,246,0.15); color: #60a5fa;">${p.strategy || 'aggressor'}</span></td>
            <td><span class="${sideClass}">${p.side}</span></td>
            <td class="mono">${Number(p.entry_price).toFixed(4)}</td>
            <td class="mono val-cyan">${Number(p.current_price || p.entry_price).toFixed(4)}</td>
            <td class="mono">${formatQty(p.quantity)}</td>
            <td class="mono">${formatUSD(deployedVal)}</td>
            <td class="mono">${uPnlStr}</td>
            <td class="mono val-red">${p.sl || '-'}</td>
            <td class="mono val-green">${p.tp || '-'}</td>
            <td class="mono">${formatDateTimeIST(p.timestamp)}</td>
        </tr>`;
    }).join('');

    if (dashBody) dashBody.innerHTML = dashRows;
    if (fullBody) fullBody.innerHTML = fullRows;

    const posCountEl = document.getElementById("pos-count");
    const posExpEl = document.getElementById("pos-exposure");
    const posUpnlEl = document.getElementById("pos-upnl");
    const posAvailEl = document.getElementById("pos-avail");

    if (posCountEl) posCountEl.innerText = positions.length;
    if (posExpEl) posExpEl.innerText = formatUSD(totalExposure);
    if (posUpnlEl) {
        posUpnlEl.innerText = (totalUpnl >= 0 ? "+" : "") + formatUSD(totalUpnl);
        posUpnlEl.className = "kpi-value mono " + (totalUpnl >= 0 ? "val-green" : "val-red");
    }
    if (posAvailEl && appState.status) {
        posAvailEl.innerText = formatUSD(appState.status.cash || 0);
    }
}

// ─── 3. RENDER HOLDINGS & CAPITAL AUDIT ────────────────────────────────────────
function renderHoldings(data) {
    const holdings = data.holdings || [];
    const fullTableBody = document.getElementById("holdings-full-table-body");
    const quickList = document.getElementById("allocation-quick-list");
    const navCount = document.getElementById("nav-holdings-count");

    if (navCount) navCount.innerText = holdings.length;

    const auditTotal = document.getElementById("audit-total-equity");
    const auditCash = document.getElementById("audit-usdt-cash");
    const auditCrypto = document.getElementById("audit-crypto-val");

    const totalPortfolioVal = (data.usdt_total_cash || 0) + (data.active_trade_holdings_value || 0);
    if (auditTotal) auditTotal.innerText = formatUSD(appState.status?.equity || totalPortfolioVal);
    if (auditCash) auditCash.innerText = formatUSD(data.usdt_total_cash || 0);
    if (auditCrypto) auditCrypto.innerText = formatUSD(data.active_trade_holdings_value || 0);

    if (holdings.length === 0) {
        if (fullTableBody) fullTableBody.innerHTML = '<tr><td colspan="8" class="empty-state">No balances found</td></tr>';
        if (quickList) quickList.innerHTML = '<div class="empty-state">No crypto holdings</div>';
        return;
    }

    // Populate full audit table
    let rowsHtml = `
        <tr style="background: rgba(6, 182, 212, 0.06);">
            <td><strong style="color: #fff; font-size: 13px;">USDT</strong> <span class="tag" style="background: rgba(6,182,212,0.2); color:#38bdf8;">LIQUID CASH</span></td>
            <td><span class="tag-positive">READY TO DEPLOY</span></td>
            <td class="mono">${formatQty(data.usdt_free)}</td>
            <td class="mono">${formatQty(data.usdt_locked)}</td>
            <td class="mono font-bold">${formatQty(data.usdt_total_cash)}</td>
            <td class="mono">$1.0000</td>
            <td class="mono val-cyan"><strong>${formatUSD(data.usdt_total_cash)}</strong></td>
            <td class="mono">${totalPortfolioVal > 0 ? ((data.usdt_total_cash / totalPortfolioVal) * 100).toFixed(1) : 0}%</td>
        </tr>
    `;

    rowsHtml += holdings.map(h => {
        const isBot = h.is_bot_trade;
        const tag = isBot ? '<span class="tag tag-oco">BOT TRADED</span>' : '<span class="tag" style="background: rgba(255,255,255,0.05); color:#94a3b8;">FAUCET/SPOT</span>';
        const share = totalPortfolioVal > 0 ? ((h.usd_value / totalPortfolioVal) * 100).toFixed(1) : "0.0";

        return `<tr>
            <td><strong style="color: #fff;">${h.asset}</strong></td>
            <td>${tag}</td>
            <td class="mono">${formatQty(h.free)}</td>
            <td class="mono">${formatQty(h.locked)}</td>
            <td class="mono font-bold">${formatQty(h.total_quantity)}</td>
            <td class="mono">$${h.price >= 1 ? h.price.toFixed(4) : h.price.toFixed(6)}</td>
            <td class="mono val-purple"><strong>${formatUSD(h.usd_value)}</strong></td>
            <td class="mono">${share}%</td>
        </tr>`;
    }).join('');

    if (fullTableBody) fullTableBody.innerHTML = rowsHtml;

    // Populate Overview Quick Allocation List
    const top5Holdings = holdings.slice(0, 5);
    let quickHtml = `
        <div class="alloc-item">
            <span class="alloc-coin">💵 USDT (Liquid Cash)</span>
            <span class="alloc-val">${formatUSD(data.usdt_total_cash)}</span>
        </div>
    `;
    quickHtml += top5Holdings.map(h => `
        <div class="alloc-item">
            <span class="alloc-coin">🪙 ${h.asset}</span>
            <span class="alloc-val">${formatUSD(h.usd_value)}</span>
        </div>
    `).join('');

    if (quickList) quickList.innerHTML = quickHtml;

    renderAllocationChart(data);
}

// ─── 4. RENDER TRADES HISTORY & KPI SUMMARY ───────────────────────────────────
function renderTrades(data) {
    const positions = data.positions || [];
    const dashBody = document.getElementById("dash-recent-trades-body");
    const fullBody = document.getElementById("trades-full-body");
    const navCount = document.getElementById("nav-trades-count");

    if (navCount) navCount.innerText = data.total_trades || positions.length;

    // Update Summary KPI Cards
    const kpiWinrate = document.getElementById("kpi-winrate");
    const kpiClosedCount = document.getElementById("kpi-closed-count");
    const kpiTargetProgress = document.getElementById("kpi-target-progress");

    const trTotal = document.getElementById("tr-total");
    const trRate = document.getElementById("tr-rate");
    const trWins = document.getElementById("tr-wins");
    const trLoss = document.getElementById("tr-loss");
    const trNet = document.getElementById("tr-net");
    const trFees = document.getElementById("tr-fees");

    const totalTrades = data.total_trades || positions.length;
    const winRate = data.win_rate || 0;
    const wins = data.wins || 0;
    const losses = data.losses || 0;
    const netPnl = data.net_pnl || 0;

    if (kpiWinrate) kpiWinrate.innerText = formatPct(winRate);
    if (kpiClosedCount) kpiClosedCount.innerText = totalTrades;
    if (kpiTargetProgress) kpiTargetProgress.innerText = `Target: ${totalTrades}/100 Trades`;

    if (trTotal) trTotal.innerText = totalTrades;
    if (trRate) trRate.innerText = formatPct(winRate);
    if (trWins) trWins.innerText = wins;
    if (trLoss) trLoss.innerText = losses;
    if (trNet) {
        trNet.innerText = (netPnl >= 0 ? "+" : "") + formatUSD(netPnl);
        trNet.className = "kpi-value mono " + (netPnl >= 0 ? "val-green" : "val-red");
    }

    if (positions.length === 0) {
        if (dashBody) dashBody.innerHTML = '<tr><td colspan="8" class="empty-state">No executed trades yet</td></tr>';
        if (fullBody) fullBody.innerHTML = '<tr><td colspan="12" class="empty-state">No trade history found</td></tr>';
        return;
    }

    let totalFees = 0;

    // Dashboard Recent Trades (Last 8)
    const dashRows = positions.slice(0, 8).map(p => {
        const pnl = p.pnl || 0;
        const pnlStr = pnl >= 0 ? `<span class="val-green">+${formatUSD(pnl)}</span>` : `<span class="val-red">${formatUSD(pnl)}</span>`;
        const sideClass = (p.action === "BUY" || p.action === "LONG") ? "tag tag-buy" : "tag tag-sell";
        const statusTag = pnl >= 0 ? '<span class="tag tag-win">WIN</span>' : '<span class="tag tag-loss">LOSS</span>';

        return `<tr>
            <td class="mono">${formatTimeOnly(p.timestamp)}</td>
            <td><strong style="color: #fff;">${p.symbol}</strong></td>
            <td><span class="${sideClass}">${p.action}</span></td>
            <td class="mono">${formatQty(p.quantity)}</td>
            <td class="mono">${Number(p.entry_price).toFixed(4)}</td>
            <td class="mono">${Number(p.exit_price || p.entry_price).toFixed(4)}</td>
            <td class="mono">${pnlStr}</td>
            <td>${statusTag}</td>
        </tr>`;
    }).join('');

    // Full Trades Table
    const fullRows = positions.map(p => {
        const pnl = p.pnl || 0;
        const fee = p.fees || 0;
        totalFees += fee;
        const pnlStr = pnl >= 0 ? `<span class="val-green">+${formatUSD(pnl)}</span>` : `<span class="val-red">${formatUSD(pnl)}</span>`;
        const grossStr = p.gross_pnl >= 0 ? `<span class="val-green">+${formatUSD(p.gross_pnl)}</span>` : `<span class="val-red">${formatUSD(p.gross_pnl)}</span>`;
        const sideClass = (p.action === "BUY" || p.action === "LONG") ? "tag tag-buy" : "tag tag-sell";
        const statusTag = pnl >= 0 ? '<span class="tag tag-win">WIN</span>' : '<span class="tag tag-loss">LOSS</span>';
        const oid = p.order_id ? String(p.order_id).substring(0, 10) + '...' : '-';

        return `<tr>
            <td class="mono">${formatDateTimeIST(p.timestamp)}</td>
            <td><strong style="color: #fff;">${p.symbol}</strong></td>
            <td><span class="tag" style="background: rgba(59,130,246,0.15); color: #60a5fa;">${p.strategy || 'aggressor'}</span></td>
            <td><span class="${sideClass}">${p.action}</span></td>
            <td class="mono">${formatQty(p.quantity)}</td>
            <td class="mono">${Number(p.entry_price).toFixed(4)}</td>
            <td class="mono">${Number(p.exit_price || p.entry_price).toFixed(4)}</td>
            <td class="mono">${grossStr}</td>
            <td class="mono">${formatUSD(fee)}</td>
            <td class="mono">${pnlStr}</td>
            <td>${statusTag}</td>
            <td class="mono" title="${p.order_id}">${oid}</td>
        </tr>`;
    }).join('');

    if (dashBody) dashBody.innerHTML = dashRows;
    if (fullBody) fullBody.innerHTML = fullRows;
    if (trFees) trFees.innerText = formatUSD(totalFees);

    // Update Analytics Tab
    const anPf = document.getElementById("an-pf");
    const anAvgWin = document.getElementById("an-avg-win");
    const anAvgLoss = document.getElementById("an-avg-loss");
    const anMaxWin = document.getElementById("an-max-win");

    if (anPf) anPf.innerText = data.profit_factor || "0.00";
    if (anAvgWin) anAvgWin.innerText = formatUSD(wins > 0 ? (data.gross_profit / wins) : 0);
    if (anAvgLoss) anAvgLoss.innerText = formatUSD(losses > 0 ? (data.gross_loss / losses) : 0);
}

// ─── 5. RENDER OPEN ORDERS (BINANCE OCO / LIMITS) ─────────────────────────────
function renderOpenOrders(orders) {
    const ordersBody = document.getElementById("open-orders-body");
    if (!ordersBody) return;

    if (!orders || orders.length === 0) {
        ordersBody.innerHTML = '<tr><td colspan="9" class="empty-state">No open orders on Binance Testnet</td></tr>';
        return;
    }

    ordersBody.innerHTML = orders.map(o => {
        const sideClass = o.side === "BUY" ? "tag tag-buy" : "tag tag-sell";
        const stopStr = o.stop_price > 0 ? Number(o.stop_price).toFixed(4) : "-";

        return `<tr>
            <td class="mono">${o.order_id}</td>
            <td><strong style="color: #fff;">${o.symbol}</strong></td>
            <td><span class="${sideClass}">${o.side}</span></td>
            <td><span class="tag tag-oco">${o.type}</span></td>
            <td class="mono">${Number(o.price).toFixed(4)}</td>
            <td class="mono val-amber">${stopStr}</td>
            <td class="mono">${formatQty(o.orig_qty)}</td>
            <td><span class="tag-positive">${o.status}</span></td>
            <td class="mono">${o.time ? formatDateTimeIST(o.time) : '-'}</td>
        </tr>`;
    }).join('');
}

// ─── 6. RENDER SCANNER & 5-STAGE PIPELINE ─────────────────────────────────────
function renderScanner(data) {
    // 5-Stage Pipeline Flow
    const plCandles = document.getElementById("pl-candles");
    const plSignals = document.getElementById("pl-signals");
    const plProfitAcc = document.getElementById("pl-profit-acc");
    const plProfitRej = document.getElementById("pl-profit-rej");
    const plRiskAcc = document.getElementById("pl-risk-acc");
    const plRiskRej = document.getElementById("pl-risk-rej");
    const plFilled = document.getElementById("pl-filled");

    if (plCandles) plCandles.innerText = data.TOTAL_CANDLES || data.symbols_scanned || 0;
    if (plSignals) plSignals.innerText = data.TOTAL_SIGNALS || 0;
    if (plProfitAcc) plProfitAcc.innerText = data.PROFITABILITY_ACCEPTED || 0;
    if (plProfitRej) plProfitRej.innerText = data.PROFITABILITY_REJECTED || 0;
    if (plRiskAcc) plRiskAcc.innerText = data.RISK_ACCEPTED || 0;
    if (plRiskRej) plRiskRej.innerText = (data.RISK_REJECTED || 0) + (data.COOLDOWN_REJECTED || 0);
    if (plFilled) plFilled.innerText = data.ORDERS_FILLED || 0;

    // Top Movers Table on Dashboard
    const moversBody = document.getElementById("dash-top-movers-body");
    const marketsBody = document.getElementById("markets-full-body");
    const tickerStream = document.getElementById("ticker-stream-content");

    const marketData = data.market_data || {};
    const symList = Object.keys(marketData);

    if (symList.length > 0) {
        const sorted = symList.map(sym => ({
            symbol: sym,
            ...marketData[sym]
        })).sort((a, b) => Math.abs(b.change_24h || 0) - Math.abs(a.change_24h || 0));

        // Top 5 Movers
        if (moversBody) {
            moversBody.innerHTML = sorted.slice(0, 5).map(m => {
                const chg = m.change_24h || 0;
                const chgStr = chg >= 0 ? `<span class="val-green">+${chg.toFixed(2)}%</span>` : `<span class="val-red">${chg.toFixed(2)}%</span>`;
                const trend = chg >= 0 ? '<span class="tag tag-buy">BULLISH</span>' : '<span class="tag tag-sell">BEARISH</span>';

                return `<tr>
                    <td><strong style="color: #fff;">${m.symbol}</strong></td>
                    <td class="mono">${formatUSD(m.close)}</td>
                    <td class="mono">${chgStr}</td>
                    <td class="mono">${formatQty(m.volume)}</td>
                    <td>${trend}</td>
                </tr>`;
            }).join('');
        }

        // Full Markets Table
        if (marketsBody) {
            marketsBody.innerHTML = sorted.map(m => {
                const chg = m.change_24h || 0;
                const chgStr = chg >= 0 ? `<span class="val-green">+${chg.toFixed(2)}%</span>` : `<span class="val-red">${chg.toFixed(2)}%</span>`;

                return `<tr>
                    <td><strong style="color: #fff;">${m.symbol}</strong></td>
                    <td class="mono">${formatUSD(m.close)}</td>
                    <td class="mono">${chgStr}</td>
                    <td class="mono">${formatUSD(m.high || m.close)}</td>
                    <td class="mono">${formatUSD(m.low || m.close)}</td>
                    <td class="mono">${formatQty(m.volume)}</td>
                    <td><span class="tag-positive">CONNECTED</span></td>
                    <td class="mono">${formatTimeOnly(new Date())}</td>
                </tr>`;
            }).join('');
        }

        // Bottom Ticker Stream
        if (tickerStream) {
            tickerStream.innerHTML = sorted.slice(0, 8).map(m => {
                const chg = m.change_24h || 0;
                const col = chg >= 0 ? "val-green" : "val-red";
                return `<span class="ticker-item"><strong class="ticker-sym">${m.symbol}</strong>: ${formatUSD(m.close)} (<span class="${col}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%</span>)</span>`;
            }).join(' • ');
        }
    }

    // Signals Full Table
    const signalsBody = document.getElementById("signals-full-body");
    const opps = data.top_opportunities || [];
    if (signalsBody) {
        if (opps.length === 0) {
            signalsBody.innerHTML = '<tr><td colspan="11" class="empty-state">No recent signal evaluations logged</td></tr>';
        } else {
            signalsBody.innerHTML = opps.map(op => {
                const decTag = op.decision === "QUALIFIED" ? '<span class="tag tag-win">QUALIFIED</span>' : '<span class="tag tag-loss">REJECTED</span>';
                return `<tr>
                    <td class="mono">${formatDateTimeIST(op.timestamp)}</td>
                    <td><strong style="color: #fff;">${op.symbol}</strong></td>
                    <td><span class="${op.side === 'BUY' ? 'tag tag-buy' : 'tag tag-sell'}">${op.side}</span></td>
                    <td class="mono">${formatUSD(op.current_price)}</td>
                    <td class="mono">${op.confidence ? (op.confidence * 100).toFixed(1) + '%' : '-'}</td>
                    <td class="mono">${op.predicted_move ? (op.predicted_move * 100).toFixed(2) + '%' : '-'}</td>
                    <td class="mono">${op.expected_gross_return ? (op.expected_gross_return * 100).toFixed(3) + '%' : '-'}</td>
                    <td class="mono val-green">${op.expected_net_return ? (op.expected_net_return * 100).toFixed(3) + '%' : '-'}</td>
                    <td class="mono">${formatUSD(op.estimated_fees || 0)}</td>
                    <td>${decTag}</td>
                    <td class="mono" style="color: #94a3b8;">${op.reason || '-'}</td>
                </tr>`;
            }).join('');
        }
    }

    // Strategies Full Table
    const stratBody = document.getElementById("strategies-full-body");
    const stratMetrics = data.strategy_metrics || {};
    if (stratBody) {
        const stratKeys = Object.keys(stratMetrics);
        if (stratKeys.length === 0) {
            stratBody.innerHTML = '<tr><td colspan="10" class="empty-state">Awaiting multi-strategy evaluations</td></tr>';
        } else {
            stratBody.innerHTML = stratKeys.map(k => {
                const sm = stratMetrics[k] || {};
                const pnl = sm.PnL || 0;
                const pnlStr = pnl >= 0 ? `<span class="val-green">+${formatUSD(pnl)}</span>` : `<span class="val-red">${formatUSD(pnl)}</span>`;
                const wr = sm.fills > 0 ? ((sm.wins || 0) / sm.fills * 100).toFixed(1) + '%' : '0.0%';

                return `<tr>
                    <td><strong style="color: #fff;">${k.toUpperCase()}</strong></td>
                    <td class="mono">${sm.signals || 0}</td>
                    <td class="mono val-green">${sm.BUY || 0}</td>
                    <td class="mono val-red">${sm.SELL || 0}</td>
                    <td class="mono">${sm.HOLD || 0}</td>
                    <td class="mono">${sm.qualified || 0}</td>
                    <td class="mono val-red">${sm.rejected || 0}</td>
                    <td class="mono val-cyan">${sm.executed || sm.fills || 0}</td>
                    <td class="mono val-green">${wr}</td>
                    <td class="mono">${pnlStr}</td>
                </tr>`;
            }).join('');
        }
    }
}

// ─── 7. INTERACTIVE CHARTS (CHART.JS) ─────────────────────────────────────────
function initCharts() {
    // Equity Chart
    const eqCtx = document.getElementById("equityChart")?.getContext("2d");
    if (eqCtx) {
        const grad = eqCtx.createLinearGradient(0, 0, 0, 220);
        grad.addColorStop(0, "rgba(6, 182, 212, 0.35)");
        grad.addColorStop(1, "rgba(6, 182, 212, 0.0)");

        appState.charts.equity = new Chart(eqCtx, {
            type: "line",
            data: {
                labels: ["Start"],
                datasets: [{
                    label: "Portfolio Valuation (USDT)",
                    data: [10000],
                    borderColor: "#06b6d4",
                    borderWidth: 2.5,
                    backgroundColor: grad,
                    fill: true,
                    tension: 0.35,
                    pointRadius: 0,
                    pointHoverRadius: 5
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
                        backgroundColor: '#11192d',
                        borderColor: '#2c3e66',
                        borderWidth: 1,
                        callbacks: {
                            label: (ctx) => `Equity: ${formatUSD(ctx.raw)}`
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: "rgba(255,255,255,0.03)" },
                        ticks: { color: "#64748b", font: { family: "JetBrains Mono", size: 10 } }
                    },
                    y: {
                        grid: { color: "rgba(255,255,255,0.05)" },
                        ticks: {
                            color: "#64748b",
                            font: { family: "JetBrains Mono", size: 10 },
                            callback: (v) => `$${v.toLocaleString()}`
                        }
                    }
                }
            }
        });
    }

    // Asset Allocation Doughnut
    const allocCtx = document.getElementById("allocationChart")?.getContext("2d");
    if (allocCtx) {
        appState.charts.allocation = new Chart(allocCtx, {
            type: "doughnut",
            data: {
                labels: ["Liquid USDT Cash", "PORTAL", "LINK", "Other Crypto"],
                datasets: [{
                    data: [8762, 2580, 220, 100],
                    backgroundColor: ["#06b6d4", "#8b5cf6", "#3b82f6", "#ec4899"],
                    borderWidth: 2,
                    borderColor: "#11192d"
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "70%",
                plugins: {
                    legend: { display: false }
                }
            }
        });
    }
}

function renderEquityChart(points) {
    if (!appState.charts.equity || !points || points.length === 0) return;
    const labels = points.map(p => formatTimeOnly(p.time));
    const data = points.map(p => p.equity);

    appState.charts.equity.data.labels = labels;
    appState.charts.equity.data.datasets[0].data = data;
    appState.charts.equity.update('none');
}

function renderAllocationChart(holdingsData) {
    if (!appState.charts.allocation || !holdingsData) return;
    const cash = holdingsData.usdt_total_cash || 0;
    const holdings = holdingsData.holdings || [];

    const labels = ["Liquid USDT"];
    const values = [cash];
    const colors = ["#06b6d4", "#8b5cf6", "#3b82f6", "#ec4899", "#10b981", "#f59e0b"];

    holdings.slice(0, 5).forEach(h => {
        labels.push(h.asset);
        values.push(h.usd_value);
    });

    appState.charts.allocation.data.labels = labels;
    appState.charts.allocation.data.datasets[0].data = values;
    appState.charts.allocation.data.datasets[0].backgroundColor = colors.slice(0, values.length);
    appState.charts.allocation.update('none');
}

function renderDiagnostics() {
    const dump = document.getElementById("raw-diagnostics-dump");
    if (dump) {
        dump.innerText = JSON.stringify({
            status: appState.status,
            holdings_summary: {
                cash: appState.holdings?.usdt_total_cash,
                crypto_value: appState.holdings?.active_trade_holdings_value,
                count: appState.holdings?.holdings?.length
            },
            open_orders: appState.openOrders,
            scanner_summary: {
                signals: appState.scanner?.TOTAL_SIGNALS,
                filled: appState.scanner?.ORDERS_FILLED,
                symbols: appState.scanner?.symbols
            }
        }, null, 2);
    }
}
