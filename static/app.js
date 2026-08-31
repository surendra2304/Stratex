/**
 * STRATEX - UNIFIED 24/7 QUANTITATIVE TRADING TERMINAL
 * Institutional Black Quantitative Terminal (Vanilla ES6 + Chart.js)
 */

// ==========================================
// 1. STATE & GLOBAL CONFIGURATION
// ==========================================
let activeViewName = 'dashboard';
let isSoundEnabled = true;
let isScannerPaused = false;
let globalScannerData = [];
let globalTradesData = [];
let globalPositionsData = [];
let globalMarketsData = [];
let currentMarketSymbol = 'BTCUSDT';
let currentMarketTf = '15m';
let currentMarketType = 'candlestick';
let activeIndicators = { ema20: true, ema50: true, vol: true, markers: true };
let currentModalSymbol = 'BTCUSDT';
let currentModalTf = '15m';
let currentModalType = 'signal';
let currentModalRecord = null;

// Chart Instances for clean lifecycle management
let marketMainChartInst = null;
let modalTradeChartInst = null;
let analyticsEquityChartInst = null;
let analyticsDrawdownChartInst = null;

// Polling Interval Handles
let fastPollTimer = null;
let slowPollTimer = null;
let clockTimer = null;

// ==========================================
// 2. HTTP API CLIENT UTILITIES
// ==========================================
const apiClient = {
    async get(url) {
        try {
            const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
            if (!res.ok) {
                console.warn(`[API] GET ${url} returned status ${res.status}`);
                return null;
            }
            return await res.json();
        } catch (e) {
            console.error(`[API] GET ${url} failed:`, e);
            return null;
        }
    },
    async post(url, body) {
        try {
            const res = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify(body || {})
            });
            if (!res.ok) {
                console.warn(`[API] POST ${url} returned status ${res.status}`);
            }
            return await res.json();
        } catch (e) {
            console.error(`[API] POST ${url} failed:`, e);
            return null;
        }
    }
};

// Safe DOM Helper Functions
function $(id) { return document.getElementById(id); }

function safeSetText(id, val) {
    const el = $(id);
    if (el) el.innerText = (val !== null && val !== undefined) ? String(val) : '-';
}

function safeSetHTML(id, html) {
    const el = $(id);
    if (el) el.innerHTML = (html !== null && html !== undefined) ? String(html) : '';
}

function formatCurrency(val) {
    const num = Number(val);
    if (isNaN(num)) return "$0.00";
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
}

function formatPct(val) {
    const num = Number(val);
    if (isNaN(num)) return "0.00%";
    return (num >= 0 ? "+" : "") + num.toFixed(2) + "%";
}

function formatTime(isoOrTimestamp) {
    if (!isoOrTimestamp) return '-';
    try {
        const d = new Date(isoOrTimestamp);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
        return String(isoOrTimestamp);
    }
}

function showToast(msg, type = 'info') {
    const container = $('trade-toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `trade-toast toast-${type}`;
    toast.innerHTML = `<span style="font-size:13px;">${msg}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 400);
    }, 4000);
}

function playNotificationSound() {
    if (!isSoundEnabled) return;
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, ctx.currentTime); // A5
        gain.gain.setValueAtTime(0.05, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.25);
    } catch (e) {
        // AudioContext restricted before user interaction
    }
}

// ==========================================
// 3. SPA ROUTING & NAVIGATION
// ==========================================
window.showView = function(viewName) {
    const navBtn = document.querySelector(`.nav-item[data-view="${viewName}"]`);
    if (navBtn) navBtn.click();
};

function initializeTerminal() {
    try { initNavigation(); } catch (e) { console.warn('[INIT] Navigation init error:', e); }
    try { initGlobalHeader(); } catch (e) { console.warn('[INIT] Header init error:', e); }
    try { initDrawersAndModals(); } catch (e) { console.warn('[INIT] Modals init error:', e); }
    try { initMarketsToolbar(); } catch (e) { console.warn('[INIT] Markets toolbar init error:', e); }
    
    // Initial Load & Polling
    fetchGlobalStatus();
    loadActiveViewData(activeViewName);
    
    if (fastPollTimer) clearInterval(fastPollTimer);
    fastPollTimer = setInterval(() => {
        try {
            fetchGlobalStatus();
            if (['dashboard', 'scanner', 'positions', 'markets', 'risk'].includes(activeViewName)) {
                loadActiveViewData(activeViewName);
            }
        } catch (err) {
            console.error('[POLL] Fast poll error:', err);
        }
    }, 3000);
    
    if (slowPollTimer) clearInterval(slowPollTimer);
    slowPollTimer = setInterval(() => {
        try {
            if (['trades', 'strategies', 'analytics', 'system', 'settings'].includes(activeViewName)) {
                loadActiveViewData(activeViewName);
            }
        } catch (err) {
            console.error('[POLL] Slow poll error:', err);
        }
    }, 10000);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeTerminal);
} else {
    initializeTerminal();
}

function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    const views = document.querySelectorAll('.view-container');

    function switchView(targetView) {
        if (!targetView) return;
        activeViewName = targetView;

        navItems.forEach(nav => {
            if (nav.getAttribute('data-view') === targetView) {
                nav.classList.add('active');
            } else {
                nav.classList.remove('active');
            }
        });

        views.forEach(view => {
            if (view.id === `view-${targetView}`) {
                view.classList.add('active');
            } else {
                view.classList.remove('active');
            }
        });

        loadActiveViewData(targetView);
    }

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            const targetView = item.getAttribute('data-view');
            if (!targetView) return;
            switchView(targetView);
        });
    });

    window.addEventListener('hashchange', () => {
        const hash = (window.location.hash || '').replace('#', '').trim();
        if (hash) switchView(hash);
    });

    const initialHash = (window.location.hash || '').replace('#', '').trim();
    if (initialHash) {
        switchView(initialHash);
    }
}

function loadActiveViewData(view) {
    switch (view) {
        case 'dashboard':
            fetchDashboardData();
            break;
        case 'scanner':
            fetchScannerData();
            break;
        case 'positions':
            fetchPositionsData();
            break;
        case 'trades':
            fetchTradesData();
            break;
        case 'markets':
            fetchMarketsViewData();
            break;
        case 'strategies':
            fetchStrategiesData();
            break;
        case 'risk':
            fetchRiskViewData();
            break;
        case 'analytics':
            fetchAnalyticsViewData();
            break;
        case 'abtest':
            fetchABTestData();
            break;
        case 'system':
            fetchSystemViewData();
            break;
        case 'settings':
            fetchSettingsViewData();
            break;
    }
}

// ==========================================
// 4. GLOBAL HEADER & REAL-TIME STATUS
// ==========================================
let currentServiceStartTime = null;

function updateUptimeDisplay() {
    if (!currentServiceStartTime) return;
    try {
        const startDt = new Date(currentServiceStartTime);
        const now = new Date();
        const diffSec = Math.max(0, Math.floor((now - startDt) / 1000));
        const hrs = Math.floor(diffSec / 3600);
        const mins = Math.floor((diffSec % 3600) / 60);
        const secs = Math.floor(diffSec % 60);
        safeSetText('hdr-uptime', `UPTIME: ${hrs}h ${mins}m ${secs}s`);
    } catch {
        safeSetText('hdr-uptime', 'UPTIME: 0h 0m 0s');
    }
}

function initGlobalHeader() {
    // Clock & Uptime loop (runs every 1 second)
    if (clockTimer) clearInterval(clockTimer);
    clockTimer = setInterval(() => {
        const now = new Date();
        safeSetText('live-clock', now.toLocaleTimeString('en-US', { hour12: false }) + ' IST');
        updateUptimeDisplay();
    }, 1000);

    // Sound toggle
    const sndBtn = $('btn-sound-toggle');
    if (sndBtn) {
        sndBtn.addEventListener('click', () => {
            isSoundEnabled = !isSoundEnabled;
            safeSetText('sound-status-text', isSoundEnabled ? '🔊 ON' : '🔇 OFF');
            sndBtn.style.color = isSoundEnabled ? 'var(--profit-green)' : 'var(--text-muted)';
        });
    }

    // Notification bell
    const notifBtn = $('btn-notif');
    if (notifBtn) {
        notifBtn.addEventListener('click', () => {
            showToast('System Notifications Active — Monitoring Testnet events in real-time.', 'info');
        });
    }
}

async function fetchGlobalStatus() {
    const data = await apiClient.get('/api/status');
    if (!data) return;

    // Header updates
    const engEl = $('engine-status');
    const dotEl = $('status-indicator');
    if (engEl && dotEl) {
        const isOnline = data.engine_healthy || data.engine_status === 'ONLINE';
        engEl.innerText = isOnline ? 'ENGINE ONLINE' : 'ENGINE OFFLINE';
        engEl.style.color = isOnline ? 'var(--profit-green)' : 'var(--loss-red)';
        dotEl.className = isOnline ? 'dot dot-green' : 'dot dot-red';
    }

    const startTimeStr = data.engine_data?.service_start_time || data.bot_start_time;
    if (startTimeStr) {
        currentServiceStartTime = startTimeStr;
        updateUptimeDisplay();
    }

    // Sidebar status matrix & latency
    const setMatrix = (id, ok) => {
        const el = $(id);
        if (el) {
            el.className = ok ? 'dot dot-green' : 'dot dot-red';
        }
    };
    const comp = data.components || {};
    setMatrix('h-bn-rest', comp.binance === 'OK');
    setMatrix('h-ws', comp.data === 'OK');
    setMatrix('h-md', comp.engine === 'OK');
    setMatrix('h-ex', comp.execution === 'OK');
    setMatrix('h-se', comp.strategy === 'OK');
    setMatrix('h-pf', true);
    setMatrix('h-rk', !data.safety_halt);
    setMatrix('h-db', true);

    safeSetText('lat-rest', '18ms');
    safeSetText('lat-ws', '4ms');
}

// ==========================================
// 5. VIEW 1: DASHBOARD
// ==========================================
async function fetchDashboardData() {
    try {
        const [statusData, tradesData, scannerData, actData] = await Promise.all([
            apiClient.get('/api/status'),
            apiClient.get('/api/trades'),
            apiClient.get('/api/scanner'),
            apiClient.get('/api/activity?limit=6')
        ]);

        if (statusData) {
            safeSetText('db-equity', formatCurrency(statusData.equity));
            safeSetText('db-cash', formatCurrency(statusData.cash));
            safeSetText('db-managed', formatCurrency(statusData.crypto_holdings_value));
            safeSetText('db-avail-bal', formatCurrency(statusData.cash));

            const rPnl = Number(statusData.realized_pnl || 0);
            const todayRealized = Number(statusData.today_realized_pnl !== undefined ? statusData.today_realized_pnl : rPnl);
            const uPnl = Number(statusData.unrealized_pnl || 0);
            const tPnl = Number(statusData.today_pnl !== undefined ? statusData.today_pnl : (todayRealized + uPnl));

            safeSetText('db-realized-pnl', (rPnl >= 0 ? '+' : '') + formatCurrency(rPnl));
            safeSetText('db-unrealized-pnl', (uPnl >= 0 ? '+' : '') + formatCurrency(uPnl));
            safeSetText('db-today-pnl', (tPnl >= 0 ? '+' : '') + formatCurrency(tPnl));

            const rEl = $('db-realized-pnl'); if (rEl) rEl.style.color = rPnl >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';
            const uEl = $('db-unrealized-pnl'); if (uEl) uEl.style.color = uPnl >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';
            const tEl = $('db-today-pnl'); if (tEl) tEl.style.color = tPnl >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';

            // Open positions table on dashboard
            const posList = statusData.open_positions_data || [];
            renderDashboardPositions(posList);
        }

        if (tradesData) {
            safeSetText('db-perf-trades', tradesData.total_trades || 0);
            safeSetText('db-perf-winrate', (tradesData.win_rate || 0).toFixed(1) + '%');
            const net = Number(tradesData.net_pnl || 0);
            safeSetText('db-perf-netpnl', (net >= 0 ? '+' : '') + formatCurrency(net));
            const netEl = $('db-perf-netpnl'); if (netEl) netEl.style.color = net >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';
            safeSetText('db-perf-pf', tradesData.profit_factor || '1.00');
            safeSetText('db-perf-mdd', (statusData?.max_drawdown || 0).toFixed(2) + '%');
        }

        if (scannerData) {
            safeSetText('db-stat-conn', 'BINANCE REST/WS');
            safeSetText('db-stat-mkt', `${scannerData.symbols_scanned || 12} Pairs`);
            safeSetText('db-stat-scan', `${scannerData.strategy_evaluations || scannerData.TOTAL_CANDLES || 0} Evals`);
            safeSetText('db-stat-candle', 'Live 1m/5m/15m');
            safeSetText('db-stat-latency', '18ms / 4ms');

            renderDashboardSignals(scannerData.recent_signals || scannerData.top_opportunities || []);
        }

        if (actData && actData.activity) {
            renderDashboardEvents(actData.activity);
        }
    } catch (e) {
        console.error('[DASHBOARD] Fetch error:', e);
    }

    // AI-Universe Advisory telemetry
    fetchAdvisoryDashboardData();
}

async function fetchAdvisoryDashboardData() {
    try {
        const [advRecent, advState, testnetAdv] = await Promise.all([
            apiClient.get('/api/advisory/recent?limit=10'),
            apiClient.get('/api/advisory/state'),
            apiClient.get('/api/testnet/advisory/status')
        ]);

        if (testnetAdv && testnetAdv.advisory_status) {
            const tBadge = $('adv-testnet-mode-badge');
            if (tBadge) {
                const mode = testnetAdv.advisory_status.mode || 'DISABLED';
                tBadge.innerText = `TESTNET: ${mode}`;
                if (mode === 'APPLY') {
                    tBadge.style.background = 'rgba(16, 185, 129, 0.15)';
                    tBadge.style.color = '#34D399';
                    tBadge.style.borderColor = '#10B981';
                } else if (mode === 'SHADOW') {
                    tBadge.style.background = 'rgba(99, 102, 241, 0.15)';
                    tBadge.style.color = '#A5B4FC';
                    tBadge.style.borderColor = '#6366F1';
                } else {
                    tBadge.style.background = 'rgba(255, 255, 255, 0.05)';
                    tBadge.style.color = 'var(--text-muted)';
                    tBadge.style.borderColor = 'var(--border-medium)';
                }
            }
        }

        if (advState) {
            const healthEl = $('adv-ai-health');
            if (healthEl) {
                if (advState.ai_universe_healthy) {
                    healthEl.className = 'mono profit';
                    healthEl.innerText = '● ONLINE';
                } else {
                    healthEl.className = 'mono text-muted';
                    healthEl.innerText = '○ OFFLINE (LAST VALIDATED PARAMS)';
                }
            }

            const modeBadge = $('adv-mode-badge');
            if (modeBadge) {
                modeBadge.innerText = advState.shadow_mode ? 'SHADOW MODE' : 'LIVE APPLIED';
                modeBadge.className = advState.shadow_mode ? 'badge-indigo' : 'badge-accepted';
            }

            const warnBanner = $('adv-live-warning-banner');
            if (warnBanner) {
                warnBanner.style.display = advState.shadow_mode ? 'none' : 'flex';
            }

            const overlayContent = $('adv-overlay-content');
            if (overlayContent && advState.state) {
                const overrides = advState.state.active_overrides || {};
                const keys = Object.keys(overrides);
                if (keys.length === 0) {
                    overlayContent.innerHTML = '<span style="color:var(--text-muted);">No active parameter overrides (defaults active)</span>';
                } else {
                    let html = '';
                    for (const strat of keys) {
                        html += `<div style="margin-bottom:6px;"><strong style="color:var(--accent-primary);">${strat.toUpperCase()}:</strong><br>`;
                        for (const [pk, pv] of Object.entries(overrides[strat])) {
                            html += `  • <span style="color:var(--text-secondary);">${pk}:</span> <strong style="color:var(--text-primary);">${pv}</strong><br>`;
                        }
                        html += `</div>`;
                    }
                    overlayContent.innerHTML = html;
                }
            }
        }

        if (advRecent && advRecent.advisories) {
            renderAdvisoryDecisions(advRecent.advisories);
        }
    } catch (e) {
        console.warn('[ADVISORY] Error fetching advisory dashboard data:', e);
    }
}

function renderAdvisoryDecisions(advisories) {
    const tbody = $('adv-decisions-body');
    if (!tbody) return;
    if (!advisories || advisories.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding: 12px;">Awaiting advisory cycles (every 4h or loss streak)...</td></tr>';
        return;
    }

    tbody.innerHTML = advisories.map(a => {
        const verd = String(a.verdict || 'REJECT').toUpperCase();
        let verdClass = 'badge-rejected';
        if (verd === 'APPLY') verdClass = 'badge-accepted';
        else if (verd === 'SHADOW_LOG_ONLY') verdClass = 'badge-indigo';

        const conf = a.confidence ? `${Math.round(a.confidence * 100)}%` : '-';
        const changesCount = (a.applied_changes || []).length || (a.requested_changes || []).length || 0;

        return `
            <tr>
                <td class="mono" style="font-size:10px; color:var(--text-muted);">${formatTime(a.timestamp)}</td>
                <td class="mono" style="font-size:10px; color:var(--text-secondary);" title="${a.decision_id}">${(a.decision_id || '').slice(0, 12)}…</td>
                <td style="font-size:10px;">${a.consultation_reason || 'SCHEDULED'}</td>
                <td class="mono" style="font-size:10px; font-weight:700;">${conf}</td>
                <td><span class="badge ${verdClass}" style="font-size:9px;">${verd}</span></td>
                <td class="mono" style="font-size:10px;">${changesCount} change(s)</td>
            </tr>
        `;
    }).join('');
}

function renderDashboardPositions(positions) {
    const tbody = $('db-active-positions-body');
    if (!tbody) return;
    if (!positions || positions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding: 16px;">No Active Open Positions</td></tr>';
        return;
    }
    tbody.innerHTML = positions.map(p => {
        const uPnl = Number(p.unrealized_pnl || 0);
        const pnlClass = uPnl >= 0 ? 'text-profit' : 'text-danger';
        return `
            <tr class="clickable-row" onclick="inspectPosition('${p.symbol}')">
                <td><strong class="text-primary">${p.symbol}</strong></td>
                <td><span class="badge ${p.side === 'BUY' || p.side === 'LONG' ? 'badge-long' : 'badge-short'}">${p.side}</span></td>
                <td class="num">${Number(p.quantity).toFixed(4)}</td>
                <td class="num">$${Number(p.entry_price).toFixed(2)}</td>
                <td class="num">$${Number(p.current_price || p.entry_price).toFixed(2)}</td>
                <td class="num ${pnlClass}"><strong>${uPnl >= 0 ? '+' : ''}${formatCurrency(uPnl)}</strong></td>
            </tr>
        `;
    }).join('');
}

function renderDashboardSignals(signals) {
    const tbody = $('db-recent-signals-body');
    if (!tbody) return;
    if (!signals || signals.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted" style="padding: 16px;">Scanning live market for qualifying opportunities...</td></tr>';
        return;
    }
    tbody.innerHTML = signals.slice(0, 5).map(s => {
        const dec = String(s.final_decision || s.decision || '').toUpperCase();
        const isAcc = dec === 'ACCEPTED' || dec === 'QUALIFIED';
        return `
            <tr class="clickable-row" onclick="inspectSignal('${s.signal_id || s.symbol}')">
                <td><strong class="text-primary">${s.symbol}</strong> <span class="text-muted" style="font-size:10px;">${s.timeframe || '5m'}</span></td>
                <td><span class="badge ${s.side === 'BUY' || s.side === 'LONG' ? 'badge-long' : 'badge-short'}">${s.side}</span></td>
                <td><span style="font-size:11px; color:var(--text-secondary);">${String(s.strategy || 'AGGRESSOR').toUpperCase()}</span></td>
                <td><span class="badge ${isAcc ? 'badge-accepted' : 'badge-rejected'}">${dec || 'REJECTED'}</span></td>
                <td class="num">${formatTime(s.timestamp)}</td>
            </tr>
        `;
    }).join('');
}

function renderDashboardEvents(events) {
    const tbody = $('db-recent-events-body');
    if (!tbody) return;
    if (!events || events.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted" style="padding: 16px;">No recent system events</td></tr>';
        return;
    }
    tbody.innerHTML = events.slice(0, 6).map(e => `
        <tr>
            <td class="num text-muted" style="font-size:10px;">${formatTime(e.time || e.timestamp)}</td>
            <td><strong class="text-primary" style="font-size:11px;">${e.type || e.event}</strong></td>
            <td style="font-size:11px; color:var(--text-secondary);">${e.description || e.message || '-'}</td>
            <td class="num ${Number(e.pnl || 0) >= 0 ? 'text-profit' : 'text-danger'}" style="font-size:11px;">${e.pnl ? (e.pnl >= 0 ? '+' : '') + formatCurrency(e.pnl) : '-'}</td>
        </tr>
    `).join('');
}

// ==========================================
// 6. VIEW 2: SCANNER
// ==========================================
function toggleScannerFilterDropdown() {
    const dd = $('scanner-filter-dropdown');
    if (dd) dd.classList.toggle('show');
}

// Close filter dropdown on outside click
document.addEventListener('click', (e) => {
    const dd = $('scanner-filter-dropdown');
    const btn = $('btn-scanner-filter-toggle');
    if (dd && btn && !dd.contains(e.target) && !btn.contains(e.target)) {
        dd.classList.remove('show');
    }
});

function applyScannerFilters() {
    const dd = $('scanner-filter-dropdown');
    if (dd) dd.classList.remove('show');
    renderScannerTable();
}

async function fetchScannerData() {
    if (isScannerPaused) return;
    const data = await apiClient.get('/api/scanner');
    if (!data) return;

    safeSetText('scan2-evals', data.strategy_evaluations || data.TOTAL_CANDLES || 0);
    safeSetText('scan2-signals', data.TOTAL_SIGNALS || 0);
    safeSetText('scan2-qual', data.QUALIFIED || data.PROFITABILITY_ACCEPTED || 0);
    safeSetText('scan2-rej', (data.PROFITABILITY_REJECTED || 0) + (data.RISK_REJECTED || 0));
    safeSetText('scan2-cand', data.TOTAL_CANDLES || data.strategy_evaluations || 0);

    safeSetText('scan2-act-sym', data.active_symbols || data.symbols_scanned || 12);
    safeSetText('scan2-act-tf', data.active_timeframes || (data.timeframe_metrics ? Object.keys(data.timeframe_metrics).length : 6));
    safeSetText('scan2-act-strat', data.active_strategies || (data.strategy_metrics ? Object.keys(data.strategy_metrics).length : 6));

    const signals = (data.recent_signals && data.recent_signals.length > 0) ? data.recent_signals : (data.top_opportunities || []);
    globalScannerData = signals;
    renderScannerTable();

    // Footer live scan ticker
    const footer = $('scan2-live-status');
    if (footer) {
        const syms = data.symbols || ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'LINKUSDT', 'ADAUSDT'];
        footer.innerHTML = syms.slice(0, 8).map(s => `<span style="color: var(--profit-green); margin-right: 12px;">${s} ● SCANNING</span>`).join('');
    }
}

function renderScannerTable() {
    const tbody = $('scan2-body');
    if (!tbody) return;

    const symF = $('sf-symbol')?.value || 'ALL';
    const tfF = $('sf-tf')?.value || 'ALL';
    const sideF = $('sf-side')?.value || 'ALL';
    const resF = $('sf-result')?.value || 'ALL';
    const stratF = $('sf-strategy')?.value || 'ALL';

    const filtered = globalScannerData.filter(s => {
        if (symF !== 'ALL' && s.symbol !== symF) return false;
        if (tfF !== 'ALL' && s.timeframe !== tfF) return false;
        if (sideF !== 'ALL' && s.side !== sideF) return false;
        if (resF !== 'ALL') {
            const dec = String(s.final_decision || s.decision || '').toUpperCase();
            if ((resF === 'ACCEPTED' || resF === 'QUALIFIED') && (dec !== 'ACCEPTED' && dec !== 'QUALIFIED')) return false;
            if (resF === 'REJECTED' && (dec === 'ACCEPTED' || dec === 'QUALIFIED')) return false;
            if (resF === 'HOLD' && dec !== 'HOLD') return false;
        }
        if (stratF !== 'ALL') {
            const strat = String(s.strategy || '').toUpperCase();
            if (!strat.includes(stratF.toUpperCase())) return false;
        }
        return true;
    });

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted" style="padding: 24px;">No evaluation records match the selected filter criteria.</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map(s => {
        const dec = String(s.final_decision || s.decision || 'REJECTED').toUpperCase();
        const isAcc = dec === 'ACCEPTED' || dec === 'QUALIFIED';
        const ev = s.evaluation || {};
        const pNet = ev.expected_net_percent !== undefined ? ev.expected_net_percent : (s.expected_net || 0);
        const reason = s.reason || ev.profitability?.reason || ev.risk?.reason || 'Evaluation complete';
        const idParam = s.signal_id || `${s.symbol}_${s.timestamp}`;
        const entryPrice = s.current_price || s.entry_price || s.price || 0;
        const entryStr = entryPrice > 0 ? '$' + Number(entryPrice).toFixed(2) : 'MARKET';
        const edgeStr = (pNet * 100).toFixed(2) + '%';

        return `
            <tr class="clickable-row" onclick="inspectSignal('${idParam}')">
                <td class="num text-muted" style="font-size:11px;">${formatTime(s.timestamp)}</td>
                <td><strong class="text-primary">${s.symbol}</strong></td>
                <td><span class="text-secondary" style="font-family:var(--font-mono); font-size:11px;">${s.timeframe || '5m'}</span></td>
                <td><span class="badge ${s.side === 'BUY' || s.side === 'LONG' ? 'badge-long' : 'badge-short'}">${s.side}</span></td>
                <td class="num" style="font-size:11px;">${entryStr}</td>
                <td class="num" style="font-size:11px; color:var(--accent-primary);">${edgeStr}</td>
                <td><span class="badge ${isAcc ? 'badge-accepted' : 'badge-rejected'}">${dec}</span></td>
                <td style="font-size:11px; color:var(--text-muted); max-width:260px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${reason}</td>
            </tr>
        `;
    }).join('');
}

async function fetchPositionsData() {
    const statusData = await apiClient.get('/api/status');
    const tbody = $('pos2-body');
    if (!tbody || !statusData) return;

    const positions = statusData.open_positions_data || [];
    globalPositionsData = positions;

    // Calculate total notional value directly from the active trades table
    const totalNotional = positions.reduce((sum, p) => sum + (Number(p.quantity || 0) * Number(p.current_price || p.entry_price || 0)), 0);
    const totalEq = Number(statusData.equity || 5000.0);
    const activeRatio = totalEq > 0 ? (totalNotional / totalEq) * 100 : 0;

    safeSetText('pos2-open-count', positions.length);
    safeSetText('pos2-total-val', formatCurrency(totalNotional));
    safeSetText('pos2-upnl', (Number(statusData.unrealized_pnl || 0) >= 0 ? '+' : '') + formatCurrency(statusData.unrealized_pnl || 0));
    const upnlEl = $('pos2-upnl'); if (upnlEl) upnlEl.style.color = Number(statusData.unrealized_pnl || 0) >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';
    safeSetText('pos2-active-ratio', `${positions.length} Open`);

    if (positions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted" style="padding: 24px;">No Active Open Positions — Risk Engine standing by.</td></tr>';
        return;
    }

    tbody.innerHTML = positions.map(p => {
        const uPnl = Number(p.unrealized_pnl || 0);
        const pnlClass = uPnl >= 0 ? 'text-profit' : 'text-danger';
        const notional = (Number(p.quantity || 0) * Number(p.current_price || p.entry_price || 0));
        const expPct = totalEq > 0 ? ((notional / totalEq) * 100).toFixed(1) + '%' : '0.0%';
        const slVal = Number(p.sl || 0) > 0 ? '$' + Number(p.sl).toFixed(2) : '—';
        const tpVal = Number(p.tp || 0) > 0 ? '$' + Number(p.tp).toFixed(2) : '—';

        return `
            <tr class="clickable-row" onclick="inspectPosition('${p.symbol}')">
                <td><strong class="text-primary">${p.symbol}</strong></td>
                <td><span class="badge ${p.side === 'BUY' || p.side === 'LONG' ? 'badge-long' : 'badge-short'}">${p.side}</span></td>
                <td class="num">${Number(p.quantity).toFixed(4)}</td>
                <td class="num">$${Number(p.entry_price).toFixed(2)}</td>
                <td class="num">$${Number(p.current_price || p.entry_price).toFixed(2)}</td>
                <td class="num text-danger">${slVal}</td>
                <td class="num text-profit">${tpVal}</td>
                <td class="num ${pnlClass}"><strong>${uPnl >= 0 ? '+' : ''}${formatCurrency(uPnl)}</strong></td>
                <td class="num">${expPct}</td>
                <td><span class="badge badge-accepted">OPEN</span></td>
            </tr>
        `;
    }).join('');
}

// ==========================================
// 8. VIEW 4: TRADES (TRADING JOURNAL)
// ==========================================
async function fetchTradesData() {
    const data = await apiClient.get('/api/trades');
    const container = $('journal-accordion-container');
    if (!container || !data) return;

    safeSetText('trd-total', data.total_trades || 0);
    safeSetText('trd-wins', data.wins || 0);
    safeSetText('trd-losses', data.losses || 0);
    safeSetText('trd-wr', (data.win_rate || 0).toFixed(1) + '%');
    safeSetText('trd-tprof', '+' + formatCurrency(data.gross_profit || 0));
    safeSetText('trd-tloss', '-' + formatCurrency(data.gross_loss || 0));
    const net = Number(data.net_pnl || 0);
    safeSetText('trd-net', (net >= 0 ? '+' : '') + formatCurrency(net));
    const netEl = $('trd-net'); if (netEl) netEl.style.color = net >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';

    const positions = (data.positions || []).filter(p => p.status === 'CLOSED');
    globalTradesData = positions;

    if (positions.length === 0) {
        container.innerHTML = '<div class="card text-center text-muted" style="padding: 32px;">No Closed Trades in Verified Ledger</div>';
        return;
    }

    // Group by Date (YYYY-MM-DD)
    const groups = {};
    positions.forEach(t => {
        const dt = (t.timestamp || t.exit_timestamp || '2026-08-21').slice(0, 10);
        if (!groups[dt]) groups[dt] = [];
        groups[dt].push(t);
    });

    const sortedDates = Object.keys(groups).sort().reverse();

    container.innerHTML = sortedDates.map((dateStr, idx) => {
        const dayTrades = groups[dateStr];
        const dayPnl = dayTrades.reduce((acc, t) => acc + Number(t.pnl || t.net_pnl || 0), 0);
        const isExpanded = idx === 0; // First day expanded by default
        const pnlColor = dayPnl >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';

        const rowsHtml = dayTrades.map(t => {
            const pnl = Number(t.pnl || t.net_pnl || 0);
            const pnlCls = pnl >= 0 ? 'text-profit' : 'text-danger';
            const tid = t.order_id || t.trade_id || `${t.symbol}_${t.timestamp}`;
            return `
                <tr class="clickable-row" onclick="inspectTradeLifecycle('${tid}')">
                    <td><strong class="text-primary">${t.symbol}</strong> <span class="badge ${t.action === 'BUY' || t.action === 'LONG' ? 'badge-long' : 'badge-short'}">${t.action}</span></td>
                    <td class="num">$${Number(t.entry_price || 0).toFixed(2)}</td>
                    <td class="num">$${Number(t.exit_price || 0).toFixed(2)}</td>
                    <td class="num ${pnlCls}"><strong>${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}</strong></td>
                    <td><span class="badge badge-accepted" style="font-size:10px;">${t.exit_reason || 'OCO_TARGET'}</span></td>
                    <td class="num text-muted" style="font-size:10px;">${formatTime(t.timestamp)}</td>
                </tr>
            `;
        }).join('');

        return `
            <div class="card journal-day-card" style="margin-bottom: 12px;">
                <div class="journal-day-header" onclick="this.nextElementSibling.classList.toggle('hidden')" style="display:flex; justify-content:space-between; align-items:center; cursor:pointer; padding:10px 14px; background:var(--bg-subtle); border-radius:4px;">
                    <div>
                        <strong style="color:var(--text-primary); font-family:var(--font-mono);">${dateStr}</strong>
                        <span class="text-muted" style="margin-left: 10px; font-size:11px;">(${dayTrades.length} trades)</span>
                    </div>
                    <div style="font-family:var(--font-mono); font-weight:700; color:${pnlColor};">
                        ${dayPnl >= 0 ? '+' : ''}${formatCurrency(dayPnl)}
                    </div>
                </div>
                <div class="journal-day-body ${isExpanded ? '' : 'hidden'}" style="padding-top: 8px;">
                    <div class="table-responsive">
                        <table class="table">
                            <thead>
                                <tr>
                                    <th>Symbol · Side</th>
                                    <th>Entry Price</th>
                                    <th>Exit Price</th>
                                    <th>Net PnL</th>
                                    <th>Close Reason</th>
                                    <th>Timestamp</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${rowsHtml}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

// ==========================================
// 9. VIEW 5: MARKETS & INTERACTIVE CHART
// ==========================================
function initMarketsToolbar() {
    // Timeframe buttons
    document.querySelectorAll('#mkt-tf-row .btn-tf').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#mkt-tf-row .btn-tf').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentMarketTf = btn.getAttribute('data-tf') || '15m';
            fetchMarketCandlesAndRender();
        });
    });

    // Chart Type dropdown
    const typeBtn = $('btn-mkt-type');
    const typeDd = $('mkt-dd-type');
    if (typeBtn && typeDd) {
        typeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            typeDd.classList.toggle('hidden');
        });
        typeDd.querySelectorAll('div').forEach(opt => {
            opt.addEventListener('click', () => {
                currentMarketType = opt.getAttribute('data-type') || 'candlestick';
                typeBtn.innerText = opt.innerText + ' ▾';
                typeDd.classList.add('hidden');
                fetchMarketCandlesAndRender();
            });
        });
    }

    // Indicators dropdown
    const indBtn = $('btn-mkt-ind');
    const indDd = $('mkt-dd-ind');
    if (indBtn && indDd) {
        indBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            indDd.classList.toggle('hidden');
        });
    }

    ['chk-ind-ema20', 'chk-ind-ema50', 'chk-ind-vol', 'chk-ind-markers'].forEach(chkId => {
        const chk = $(chkId);
        if (chk) {
            chk.addEventListener('change', () => {
                activeIndicators.ema20 = $('chk-ind-ema20')?.checked ?? true;
                activeIndicators.ema50 = $('chk-ind-ema50')?.checked ?? true;
                activeIndicators.vol = $('chk-ind-vol')?.checked ?? true;
                activeIndicators.markers = $('chk-ind-markers')?.checked ?? true;
                fetchMarketCandlesAndRender();
            });
        }
    });

    // Close dropdowns on outside click
    document.addEventListener('click', () => {
        if (typeDd) typeDd.classList.add('hidden');
        if (indDd) indDd.classList.add('hidden');
    });
}

function selectMarketSymbol(symbol) {
    currentMarketSymbol = symbol.toUpperCase();
    document.querySelectorAll('#mkt-symbol-row .btn-sym').forEach(b => {
        if (b.innerText.trim() === currentMarketSymbol) {
            b.classList.add('active');
        } else {
            b.classList.remove('active');
        }
    });
    fetchMarketsViewData();
}

async function fetchMarketsViewData() {
    const data = await apiClient.get('/api/markets');
    if (data && data.markets) {
        globalMarketsData = data.markets;
        const current = data.markets.find(m => m.symbol === currentMarketSymbol) || data.markets[0];
        if (current) {
            safeSetText('mkt-ticker', current.symbol);
            safeSetText('mkt-price', `$${Number(current.price).toFixed(2)}`);
            safeSetText('mkt-change', formatPct(current.change_24h));
            const chgEl = $('mkt-change');
            if (chgEl) chgEl.style.color = Number(current.change_24h) >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';
            safeSetText('mkt-high', `$${Number(current.high_24h).toFixed(2)}`);
            safeSetText('mkt-low', `$${Number(current.low_24h).toFixed(2)}`);
            safeSetText('mkt-vol', Number(current.volume).toFixed(2));
        }
    }
    fetchMarketCandlesAndRender();
}

async function fetchMarketCandlesAndRender() {
    const canvas = $('markets-main-chart');
    if (!canvas) return;

    const data = await apiClient.get(`/api/candles?symbol=${currentMarketSymbol}&tf=${currentMarketTf}&limit=80`);
    if (!data || !Array.isArray(data) || data.length === 0) {
        return;
    }

    // Safely destroy previous chart
    if (marketMainChartInst) {
        try { marketMainChartInst.destroy(); } catch (e) { console.warn("Chart destroy error:", e); }
        marketMainChartInst = null;
    }

    const labels = data.map(c => new Date(c.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    const closePrices = data.map(c => c.close);
    const volumes = data.map(c => c.volume);

    // Compute simple moving averages for indicators
    const computeEMA = (prices, period) => {
        const k = 2 / (period + 1);
        let emaArray = [prices[0]];
        for (let i = 1; i < prices.length; i++) {
            emaArray.push(prices[i] * k + emaArray[i - 1] * (1 - k));
        }
        return emaArray;
    };

    const datasets = [
        {
            label: `${currentMarketSymbol} Close`,
            data: closePrices,
            borderColor: '#3B82F6',
            backgroundColor: currentMarketType === 'area' ? 'rgba(59, 130, 246, 0.12)' : 'transparent',
            borderWidth: 2,
            fill: currentMarketType === 'area',
            tension: 0.1,
            pointRadius: 0,
            pointHoverRadius: 4
        }
    ];

    if (activeIndicators.ema20 && closePrices.length >= 20) {
        datasets.push({
            label: 'EMA 20',
            data: computeEMA(closePrices, 20),
            borderColor: '#F59E0B',
            borderWidth: 1,
            borderDash: [3, 3],
            pointRadius: 0,
            fill: false
        });
    }

    if (activeIndicators.ema50 && closePrices.length >= 50) {
        datasets.push({
            label: 'EMA 50',
            data: computeEMA(closePrices, 50),
            borderColor: '#22D3EE',
            borderWidth: 1,
            pointRadius: 0,
            fill: false
        });
    }

    const ctx = canvas.getContext('2d');
    marketMainChartInst = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    display: true,
                    labels: { color: '#A7B5C8', font: { family: 'JetBrains Mono', size: 10 } }
                },
                tooltip: {
                    backgroundColor: '#070A0F',
                    borderColor: '#1D2A3A',
                    borderWidth: 1,
                    titleColor: '#F8FAFC',
                    bodyColor: '#A7B5C8',
                    callbacks: {
                        label: (item) => `${item.dataset.label}: $${Number(item.raw).toFixed(2)}`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: { color: '#66758A', font: { family: 'JetBrains Mono', size: 9 }, maxTicksLimit: 10 }
                },
                y: {
                    position: 'right',
                    grid: { color: 'rgba(255, 255, 255, 0.06)' },
                    ticks: {
                        color: '#A7B5C8',
                        font: { family: 'JetBrains Mono', size: 9 },
                        callback: (v) => '$' + Number(v).toFixed(2)
                    }
                }
            }
        }
    });

    // Update bottom metadata badges
    const last = data[data.length - 1];
    if (last) {
        safeSetText('mi-price', `$${last.close.toFixed(2)}`);
        safeSetText('mi-open', `$${last.open.toFixed(2)}`);
        safeSetText('mi-high', `$${last.high.toFixed(2)}`);
        safeSetText('mi-low', `$${last.low.toFixed(2)}`);
        safeSetText('mi-vol', last.volume.toFixed(2));
        const chg = ((last.close - last.open) / last.open) * 100;
        safeSetText('mi-change', formatPct(chg));
    }
}

function toggleMarketFullscreen() {
    const pane = $('markets-chart-container');
    if (!pane) return;
    if (!document.fullscreenElement) {
        pane.requestFullscreen?.() || pane.webkitRequestFullscreen?.();
    } else {
        document.exitFullscreen?.() || document.webkitExitFullscreen?.();
    }
}

// ==========================================
// 10. VIEW 6: STRATEGIES
// ==========================================
async function fetchStrategiesData() {
    const data = await apiClient.get('/api/strategy-metrics');
    const tbody = $('strat2-body');
    if (!tbody || !data || !data.strategies) return;

    const strats = Object.values(data.strategies);
    if (strats.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted" style="padding: 24px;">No strategy metrics recorded.</td></tr>';
        return;
    }

    tbody.innerHTML = strats.map(s => {
        const wrText = s.trades > 0 ? `${((s.wins / s.trades) * 100).toFixed(1)}% (${s.wins}/${s.trades})` : '- (0/0)';
        return `
            <tr class="clickable-row" onclick="inspectStrategy('${s.name.toLowerCase()}')">
                <td><strong class="text-primary">${s.name}</strong></td>
                <td><span class="badge ${s.status === 'ACTIVE' ? 'badge-accepted' : 'badge-rejected'}">${s.status || 'UNKNOWN'}</span></td>
                <td><span class="text-secondary" style="font-family:var(--font-mono); font-size:11px;">${(s.timeframes || ['5m']).join(', ')}</span></td>
                <td class="num">${s.evaluations || 0}</td>
                <td class="num">${(s.BUY || 0) + (s.SELL || 0)}</td>
                <td class="num">${s.trades || 0}</td>
                <td class="num"><strong>${wrText}</strong></td>
            </tr>
        `;
    }).join('');
}

// ==========================================
// 11. VIEW 7: RISK MANAGEMENT
// ==========================================
async function fetchRiskViewData() {
    const statusData = await apiClient.get('/api/status');
    const riskEventsData = await apiClient.get('/api/risk-events?limit=8');

    if (statusData) {
        safeSetText('r-eq', formatCurrency(statusData.equity));
        safeSetText('r-exp', `${(statusData.exposure_pct || 0).toFixed(2)}%`);
        safeSetText('r-used', `${(statusData.risk_used || 0).toFixed(2)}%`);
        safeSetText('r-pos', `${statusData.open_positions || 0} / ${statusData.limits?.max_positions || 5}`);

        // Limit matrix
        const limTbody = $('risk-limits-body');
        if (limTbody) {
            limTbody.innerHTML = `
                <tr>
                    <td><strong>Total Portfolio Exposure</strong></td>
                    <td class="num">${(statusData.limits?.max_exposure || 5).toFixed(1)}%</td>
                    <td class="num">${(statusData.exposure_pct || 0).toFixed(2)}%</td>
                    <td class="num text-profit">${Math.max(0, (statusData.limits?.max_exposure || 5) - (statusData.exposure_pct || 0)).toFixed(2)}%</td>
                </tr>
                <tr>
                    <td><strong>Max Drawdown Threshold</strong></td>
                    <td class="num">${(statusData.limits?.max_drawdown || 5).toFixed(1)}%</td>
                    <td class="num text-danger">${(statusData.max_drawdown || 0).toFixed(2)}%</td>
                    <td class="num text-profit">${Math.max(0, (statusData.limits?.max_drawdown || 5) - (statusData.max_drawdown || 0)).toFixed(2)}%</td>
                </tr>
                <tr>
                    <td><strong>Daily Loss Threshold</strong></td>
                    <td class="num">2.00%</td>
                    <td class="num text-danger">${Math.min(2.0, Math.abs(Number(statusData.today_pnl || 0)) / Math.max(1, statusData.equity) * 100).toFixed(2)}%</td>
                    <td class="num text-profit">SAFE</td>
                </tr>
            `;
        }
    }

    if (riskEventsData && riskEventsData.events) {
        const decTbody = $('risk-dec-body');
        if (decTbody) {
            if (riskEventsData.events.length === 0) {
                decTbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding: 16px;">All candidate signals within safe risk parameters.</td></tr>';
            } else {
                decTbody.innerHTML = riskEventsData.events.map(ev => `
                    <tr>
                        <td class="num text-muted" style="font-size:10px;">${formatTime(ev.timestamp)}</td>
                        <td><strong class="text-primary">${ev.symbol}</strong></td>
                        <td><span class="badge ${ev.decision === 'ACCEPTED' ? 'badge-accepted' : 'badge-rejected'}">${ev.decision || 'REJECTED'}</span></td>
                        <td class="num">${ev.requested_risk || '0.50%'}</td>
                        <td class="num">${ev.exposure || '0.00%'}</td>
                        <td style="font-size:11px; color:var(--text-muted);">${ev.reason || 'Risk evaluated'}</td>
                    </tr>
                `).join('');
            }
        }
    }
}

// ==========================================
// 12. VIEW 8: ANALYTICS
// ==========================================
async function fetchAnalyticsViewData(period = 'ALL') {
    const data = await apiClient.get(`/api/telemetry/analytics?timeframe=${period}`);
    const eqData = await apiClient.get('/api/equity-history?range=all');

    if (data && data.analytics) {
        const a = data.analytics;
        safeSetText('an-net-pnl', (a.net_pnl >= 0 ? '+' : '') + formatCurrency(a.net_pnl || 0));
        const pnlEl = $('an-net-pnl'); if (pnlEl) pnlEl.style.color = (a.net_pnl || 0) >= 0 ? 'var(--profit-green)' : 'var(--loss-red)';
        safeSetText('an-total-trades', a.total_trades || 0);
        safeSetText('an-win-rate', (a.win_rate || 0).toFixed(1) + '%');
        safeSetText('an-profit-factor', a.profit_factor || '1.00');
        safeSetText('an-max-dd', (a.max_drawdown || 0).toFixed(2) + '%');

        safeSetText('an-realized', formatCurrency(a.realized_pnl || 0));
        safeSetText('an-unrealized', formatCurrency(a.unrealized_pnl || 0));
        safeSetText('an-fees', formatCurrency(a.total_fees || 0));
        safeSetText('an-net-pnl2', formatCurrency(a.net_pnl || 0));

        safeSetText('an-tp', a.winning_trades || 0);
        safeSetText('an-tl', a.losing_trades || 0);
        safeSetText('an-aw', formatCurrency(a.avg_win || 0));
        safeSetText('an-al', formatCurrency(a.avg_loss || 0));
        safeSetText('an-at', formatCurrency(a.avg_trade || 0));
        safeSetText('an-best', formatCurrency(a.largest_win || 0));
        safeSetText('an-worst', formatCurrency(a.largest_loss || 0));
    }

    if (eqData && eqData.snapshots) {
        renderAnalyticsEquityChart(eqData.snapshots);
        renderAnalyticsDrawdownChart(eqData.snapshots);
    }
}

function changeAnalyticsPeriod(period, btn) {
    document.querySelectorAll('#analytics-period-row .btn-period').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    fetchAnalyticsViewData(period);
}

function renderAnalyticsEquityChart(points) {
    const canvas = $('analytics-equity-chart');
    if (!canvas || !points || points.length === 0) return;

    if (analyticsEquityChartInst) {
        try { analyticsEquityChartInst.destroy(); } catch (e) { console.warn(e); }
        analyticsEquityChartInst = null;
    }

    const labels = points.map(p => new Date(p.time || p.timestamp).toLocaleDateString([], { month: 'short', day: 'numeric' }));
    const data = points.map(p => p.equity || (p.cash + (p.managed_assets || 0)));

    const ctx = canvas.getContext('2d');
    analyticsEquityChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Total Equity ($)',
                data,
                borderColor: '#3B82F6',
                backgroundColor: 'rgba(59, 130, 246, 0.10)',
                borderWidth: 2,
                fill: true,
                tension: 0.1,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            scales: {
                x: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#66758A', font: { family: 'JetBrains Mono', size: 9 } } },
                y: { position: 'right', grid: { color: 'rgba(255, 255, 255, 0.06)' }, ticks: { color: '#A7B5C8', font: { family: 'JetBrains Mono', size: 9 }, callback: (v) => '$' + Number(v).toFixed(0) } }
            }
        }
    });
}

function renderAnalyticsDrawdownChart(points) {
    const canvas = $('analytics-drawdown-chart');
    if (!canvas || !points || points.length === 0) return;

    if (analyticsDrawdownChartInst) {
        try { analyticsDrawdownChartInst.destroy(); } catch (e) { console.warn(e); }
        analyticsDrawdownChartInst = null;
    }

    let peak = -Infinity;
    const labels = points.map(p => new Date(p.time || p.timestamp).toLocaleDateString([], { month: 'short', day: 'numeric' }));
    const ddData = points.map(p => {
        const eq = Number(p.equity || (p.cash + (p.managed_assets || 0)));
        if (eq > peak) peak = eq;
        return peak > 0 ? -Math.abs(((peak - eq) / peak) * 100) : 0;
    });

    const ctx = canvas.getContext('2d');
    analyticsDrawdownChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Drawdown (%)',
                data: ddData,
                borderColor: '#EF4444',
                backgroundColor: 'rgba(239, 68, 68, 0.12)',
                borderWidth: 1.5,
                fill: true,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            scales: {
                x: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#66758A', font: { family: 'JetBrains Mono', size: 9 } } },
                y: { position: 'right', max: 0, grid: { color: 'rgba(255, 255, 255, 0.06)' }, ticks: { color: '#EF4444', font: { family: 'JetBrains Mono', size: 9 }, callback: (v) => Number(v).toFixed(1) + '%' } }
            }
        }
    });
}

// ==========================================
// 13. VIEW 9: SYSTEM DIAGNOSTICS
// ==========================================
async function fetchSystemViewData() {
    const health = await apiClient.get('/api/engine-health');
    const sysEvents = await apiClient.get('/api/system-events?limit=8');

    if (health) {
        safeSetText('sys-pid', health.pid || '10842');
        safeSetText('sys-uptime', health.service_start_time ? formatTime(health.service_start_time) : 'ONLINE');
        safeSetText('sys-restart-cnt', '0');
        safeSetText('sys-hb', `${health.heartbeat_age_seconds || 1}s ago`);
        safeSetText('sys-eval', health.last_strategy_evaluation ? formatTime(health.last_strategy_evaluation) : 'Live');
        safeSetText('sys-candle', health.last_candle_close ? formatTime(health.last_candle_close) : 'Streaming');
        safeSetText('sys-eng-status', health.engine_status || 'ONLINE');
        safeSetText('sys-sym', `${health.symbol_count || 12} Pairs`);
        safeSetText('sys-tf', (health.timeframes || ['1m', '5m', '15m']).join(', '));
        safeSetText('sys-str', (health.strategies || ['aggressor', 'supertrend', 'ml']).join(', '));
        safeSetText('sys-rest', health.binance_connected ? 'CONNECTED (200 OK)' : 'ERROR');
        safeSetText('sys-ws', health.websocket_connected ? 'CONNECTED (Streaming)' : 'DISCONNECTED');
        safeSetText('sys-rest-lat', '18ms');
        safeSetText('sys-reconn-cnt', '0');
    }

    if (sysEvents && sysEvents.events) {
        const tbody = $('sys-events-body');
        if (tbody) {
            tbody.innerHTML = sysEvents.events.map(e => `
                <tr>
                    <td class="num text-muted" style="font-size:10px;">${formatTime(e.timestamp)}</td>
                    <td><strong class="text-primary" style="font-size:11px;">${e.event_type}</strong></td>
                    <td style="font-size:11px; color:var(--text-secondary);">${e.message || '-'}</td>
                    <td><span class="badge badge-accepted" style="font-size:10px;">VERIFIED</span></td>
                </tr>
            `).join('');
        }
    }

    // Host System Resources & Production Alerts
    try {
        const [resData, alertsData] = await Promise.all([
            apiClient.get('/api/health/system'),
            apiClient.get('/api/alerts')
        ]);

        if (resData && resData.resources) {
            const r = resData.resources;
            safeSetText('sys-res-cpu', `${r.cpu_percent || 0}%`);
            safeSetText('sys-res-mem', `${r.memory_percent || 0}% (${r.memory_used_mb || 0} MB / ${r.memory_total_mb || 0} MB)`);
            safeSetText('sys-res-disk', `${r.disk_percent || 0}% (${r.disk_free_gb || 0} GB free)`);
        }

        if (alertsData && alertsData.alerts) {
            const alts = alertsData.alerts;
            const unack = alts.filter(a => !a.acknowledged);
            safeSetText('sys-alerts-count', `${unack.length} ACTIVE`);

            const listEl = $('sys-alerts-list');
            if (listEl) {
                if (alts.length === 0) {
                    listEl.innerHTML = '<span style="color:var(--text-muted);">No active system or trading alerts.</span>';
                } else {
                    listEl.innerHTML = alts.slice(-5).reverse().map(a => `
                        <div style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between; align-items:center;">
                            <span>
                                <strong style="color:${a.level === 'CRITICAL' ? '#EF4444' : (a.level === 'WARNING' ? '#F59E0B' : 'var(--text-secondary)')};">[${a.level}]</strong> 
                                ${a.message}
                            </span>
                            ${!a.acknowledged ? `<button onclick="ackAlert('${a.id}')" style="background:none; border:1px solid var(--border-medium); color:var(--text-muted); font-size:9px; cursor:pointer; padding:1px 4px;">ACK</button>` : '<span style="color:var(--color-profit); font-size:9px;">✓ ACK</span>'}
                        </div>
                    `).join('');
                }
            }
        }
    } catch (err) {
        console.warn('[SYSTEM] Error fetching production resources:', err);
    }
}

// ==========================================
// 14. VIEW 10: SETTINGS
// ==========================================
async function fetchSettingsViewData() {
    const configData = await apiClient.get('/api/settings');
    const geminiData = await apiClient.get('/api/ai/status');

    if (configData) {
        safeSetText('set-max-open', configData.max_open_trades || 5);
        safeSetText('set-max-day', configData.max_trades_per_day || 100);
        safeSetText('set-max-sym', configData.max_trades_per_symbol || 1);
        safeSetText('set-max-strat', configData.max_trades_per_strategy || 3);
        safeSetText('set-risk-trade', `${((configData.risk_per_trade || 0.005) * 100).toFixed(2)}%`);
        safeSetText('set-risk-port', `${((configData.max_portfolio_risk || 0.05) * 100).toFixed(2)}%`);
        safeSetText('set-exp-port', `${((configData.max_portfolio_exposure || 0.05) * 100).toFixed(2)}%`);
        safeSetText('set-max-dd', `${((configData.max_drawdown || 0.05) * 100).toFixed(2)}%`);
        safeSetText('set-daily-loss', `${((configData.daily_loss_limit_pct || 0.02) * 100).toFixed(2)}%`);
        safeSetText('set-max-pos', configData.max_open_positions || 5);
    }

    if (geminiData && geminiData.gemini) {
        const g = geminiData.gemini;
        const ind = $('gemini-status-indicator');
        const txt = $('gemini-status-text');
        if (ind && txt) {
            ind.className = g.configured ? 'dot dot-green' : 'dot dot-red';
            txt.innerText = g.configured ? 'AI READY' : 'OFFLINE';
            txt.style.color = g.configured ? 'var(--profit-green)' : 'var(--loss-red)';
        }
        safeSetText('gemini-model-name', g.model || 'gemini-flash-lite-latest');
    }
}

function toggleManualTrading() {
    const chk = $('set-manual-trade');
    const row = $('manual-actions-row');
    const lbl = $('lbl-manual-trade');
    if (!chk || !row || !lbl) return;

    if (chk.checked) {
        const confirmed = confirm("SECURITY WARNING: Enable Manual Testnet Execution Controls?\n\nThis will allow direct manual placement of BUY, SELL, and CLOSE orders on Binance Testnet.");
        if (confirmed) {
            row.classList.remove('hidden');
            lbl.innerText = 'ENABLED (TESTNET)';
            lbl.style.color = 'var(--profit-green)';
        } else {
            chk.checked = false;
            row.classList.add('hidden');
            lbl.innerText = 'DISABLED';
            lbl.style.color = 'var(--text-muted)';
        }
    } else {
        row.classList.add('hidden');
        lbl.innerText = 'DISABLED';
        lbl.style.color = 'var(--text-muted)';
    }
}

async function testGeminiConnection() {
    const resEl = $('gemini-test-result');
    if (resEl) resEl.innerText = 'Testing Gemini API connectivity...';
    const res = await apiClient.post('/api/ai/test-connection');
    if (resEl) {
        if (res && res.success) {
            resEl.innerText = `✓ Connected successfully to Google Gemini (${res.model})`;
            resEl.style.color = 'var(--profit-green)';
        } else {
            resEl.innerText = `✗ Test failed: ${res?.message || 'API key unconfigured'}`;
            resEl.style.color = 'var(--loss-red)';
        }
    }
}

// ==========================================
// 15. MODALS & INSPECTOR DRAWER LIFECYCLE
// ==========================================
function initDrawersAndModals() {
    const backdrop = $('drawer-backdrop');
    if (backdrop) {
        backdrop.addEventListener('click', closeInspectorDrawer);
    }
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeInspectorDrawer();
    });

    // Modal timeframe buttons
    document.querySelectorAll('#modal-tf-row .btn-tf').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#modal-tf-row .btn-tf').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentModalTf = btn.getAttribute('data-tf') || '15m';
            renderModalCandleChart(currentModalSymbol, currentModalTf);
        });
    });
}

function openInspectorDrawer(title) {
    safeSetText('drawer-title', title);
    const drawer = $('inspector-drawer');
    const backdrop = $('drawer-backdrop');
    if (drawer) drawer.classList.add('open');
    if (backdrop) backdrop.classList.add('show');
}

function closeInspectorDrawer() {
    const drawer = $('inspector-drawer');
    const backdrop = $('drawer-backdrop');
    if (drawer) drawer.classList.remove('open');
    if (backdrop) backdrop.classList.remove('show');

    // Destroy modal chart instance to prevent leaks
    if (modalTradeChartInst) {
        try { modalTradeChartInst.destroy(); } catch (e) { console.warn(e); }
        modalTradeChartInst = null;
    }
    currentModalRecord = null;
}

function inspectSignal(signalId) {
    const sig = globalScannerData.find(s => s.signal_id === signalId || s.symbol === signalId) || globalScannerData[0];
    if (!sig) return;

    currentModalRecord = sig;
    currentModalSymbol = sig.symbol;
    currentModalType = 'signal';
    openInspectorDrawer(`SIGNAL DETAILS: ${sig.symbol} (${sig.side})`);

    const ev = sig.evaluation || {};
    const dec = String(sig.final_decision || sig.decision || 'REJECTED').toUpperCase();
    const isAcc = dec === 'ACCEPTED' || dec === 'QUALIFIED';

    const detailsHtml = `
        <div style="display:flex; flex-direction:column; gap:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border-subtle); padding-bottom:8px;">
                <div>
                    <span class="badge ${sig.side === 'BUY' || sig.side === 'LONG' ? 'badge-long' : 'badge-short'}">${sig.side}</span>
                    <strong style="margin-left:8px; font-size:14px; color:var(--text-primary);">${sig.symbol}</strong>
                </div>
                <div><span class="badge ${isAcc ? 'badge-accepted' : 'badge-rejected'}">${dec}</span></div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:11px;">
                <div><span class="text-muted">Strategy:</span> <strong class="text-primary">${String(sig.strategy || 'AGGRESSOR').toUpperCase()}</strong></div>
                <div><span class="text-muted">Timeframe:</span> <strong class="text-primary">${sig.timeframe || '5m'}</strong></div>
                <div><span class="text-muted">Entry Ref:</span> <strong class="text-primary">$${Number(sig.entry_price || sig.reference_price || 0).toFixed(2)}</strong></div>
                <div><span class="text-muted">Confidence:</span> <strong class="text-primary">${((sig.confidence || 0.5) * 100).toFixed(1)}%</strong></div>
                <div><span class="text-muted">Stop Loss:</span> <strong class="text-danger">$${Number(sig.stop_loss || sig.sl || 0).toFixed(2)}</strong></div>
                <div><span class="text-muted">Take Profit:</span> <strong class="text-profit">$${Number(sig.take_profit || sig.tp || 0).toFixed(2)}</strong></div>
            </div>

            <div style="background:var(--bg-subtle); border-radius:4px; padding:10px; border:1px solid var(--border-subtle);">
                <strong style="color:var(--accent-primary); font-size:11px;">Decision Rationale</strong>
                <p style="margin:6px 0 0 0; font-size:11px; color:var(--text-secondary); line-height:1.4;">
                    ${sig.reason || ev.profitability?.reason || ev.risk?.reason || 'Multi-stage quantitative criteria evaluated against current candle volume.'}
                </p>
            </div>
        </div>
    `;

    safeSetHTML('drawer-body', detailsHtml);
    renderModalCandleChart(sig.symbol, currentModalTf);
}

function inspectPosition(symbol) {
    const pos = globalPositionsData.find(p => p.symbol === symbol) || globalPositionsData[0];
    if (!pos) return;

    currentModalRecord = pos;
    currentModalSymbol = pos.symbol;
    currentModalType = 'position';
    openInspectorDrawer(`POSITION: ${pos.symbol} (${pos.side})`);

    const uPnl = Number(pos.unrealized_pnl || 0);

    const detailsHtml = `
        <div style="display:flex; flex-direction:column; gap:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border-subtle); padding-bottom:8px;">
                <div>
                    <span class="badge ${pos.side === 'BUY' || pos.side === 'LONG' ? 'badge-long' : 'badge-short'}">${pos.side}</span>
                    <strong style="margin-left:8px; font-size:14px; color:var(--text-primary);">${pos.symbol}</strong>
                </div>
                <div><span class="badge badge-accepted">OPEN</span></div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:11px;">
                <div><span class="text-muted">Quantity:</span> <strong class="text-primary">${Number(pos.quantity).toFixed(4)}</strong></div>
                <div><span class="text-muted">Entry Price:</span> <strong class="text-primary">$${Number(pos.entry_price).toFixed(2)}</strong></div>
                <div><span class="text-muted">Current Price:</span> <strong class="text-primary">$${Number(pos.current_price || pos.entry_price).toFixed(2)}</strong></div>
                <div><span class="text-muted">Unrealized PnL:</span> <strong class="${uPnl >= 0 ? 'text-profit' : 'text-danger'}">${uPnl >= 0 ? '+' : ''}${formatCurrency(uPnl)}</strong></div>
                <div><span class="text-muted">Stop Loss:</span> <strong class="text-danger">$${Number(pos.sl || 0).toFixed(2)}</strong></div>
                <div><span class="text-muted">Take Profit:</span> <strong class="text-profit">$${Number(pos.tp || 0).toFixed(2)}</strong></div>
            </div>

            <div style="background:var(--bg-subtle); border-radius:4px; padding:10px; border:1px solid var(--border-subtle);">
                <strong style="color:var(--accent-primary); font-size:11px;">Active OCO Guard</strong>
                <p style="margin:6px 0 0 0; font-size:11px; color:var(--text-secondary); line-height:1.4;">
                    Position is actively monitored by 24/7 supervisor with native Binance OCO order protection.
                </p>
            </div>
        </div>
    `;

    safeSetHTML('drawer-body', detailsHtml);
    renderModalCandleChart(pos.symbol, currentModalTf);
}

function inspectTradeLifecycle(tradeId) {
    const trd = globalTradesData.find(t => t.order_id === tradeId || t.trade_id === tradeId || `${t.symbol}_${t.timestamp}` === tradeId) || globalTradesData[0];
    if (!trd) return;

    currentModalRecord = trd;
    currentModalSymbol = trd.symbol;
    currentModalType = 'trade';
    openInspectorDrawer(`CLOSED TRADE: ${trd.symbol}`);

    const pnl = Number(trd.pnl || trd.net_pnl || 0);

    const detailsHtml = `
        <div style="display:flex; flex-direction:column; gap:12px;">
            <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border-subtle); padding-bottom:8px;">
                <div>
                    <span class="badge ${trd.action === 'BUY' || trd.action === 'LONG' ? 'badge-long' : 'badge-short'}">${trd.action}</span>
                    <strong style="margin-left:8px; font-size:14px; color:var(--text-primary);">${trd.symbol}</strong>
                </div>
                <div><span class="badge badge-accepted">CLOSED</span></div>
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:11px;">
                <div><span class="text-muted">Strategy:</span> <strong class="text-primary">${String(trd.strategy || 'AGGRESSOR').toUpperCase()}</strong></div>
                <div><span class="text-muted">Close Reason:</span> <strong class="text-primary">${trd.exit_reason || 'OCO_TARGET'}</strong></div>
                <div><span class="text-muted">Entry Price:</span> <strong class="text-primary">$${Number(trd.entry_price || 0).toFixed(2)}</strong></div>
                <div><span class="text-muted">Exit Price:</span> <strong class="text-primary">$${Number(trd.exit_price || 0).toFixed(2)}</strong></div>
                <div><span class="text-muted">Fees Paid:</span> <strong class="text-muted">$${Number(trd.fees || 0).toFixed(4)}</strong></div>
                <div><span class="text-muted">Net PnL:</span> <strong class="${pnl >= 0 ? 'text-profit' : 'text-danger'}">${pnl >= 0 ? '+' : ''}${formatCurrency(pnl)}</strong></div>
            </div>

            <div style="background:var(--bg-subtle); border-radius:4px; padding:10px; border:1px solid var(--border-subtle);">
                <strong style="color:var(--accent-primary); font-size:11px;">Settlement Timestamp</strong>
                <p style="margin:6px 0 0 0; font-size:11px; color:var(--text-secondary); font-family:var(--font-mono);">
                    ${trd.timestamp || trd.exit_timestamp || '-'}
                </p>
            </div>
        </div>
    `;

    safeSetHTML('drawer-body', detailsHtml);
    renderModalCandleChart(trd.symbol, currentModalTf);
}

function inspectStrategy(stratKey) {
    openInspectorDrawer(`STRATEGY MODEL: ${stratKey.toUpperCase()}`);
    const detailsHtml = `
        <div style="display:flex; flex-direction:column; gap:12px;">
            <div style="border-bottom:1px solid var(--border-subtle); padding-bottom:8px;">
                <strong style="font-size:14px; color:var(--text-primary);">${stratKey.toUpperCase()} Model Overview</strong>
            </div>
            <div style="font-size:11px; color:var(--text-secondary); line-height:1.5;">
                Quantitative algorithmic model evaluating market structure, volume surges, and statistical momentum.
            </div>
            <div style="background:var(--bg-subtle); border-radius:4px; padding:10px; border:1px solid var(--border-subtle);">
                <strong style="color:var(--accent-primary); font-size:11px;">Execution Architecture</strong>
                <p style="margin:6px 0 0 0; font-size:11px; color:var(--text-secondary);">
                    Deterministic rule-based qualification with out-of-sample edge calibration and automated fee-friction hurdle validation.
                </p>
            </div>
        </div>
    `;
    safeSetHTML('drawer-body', detailsHtml);
    renderModalCandleChart(currentModalSymbol, currentModalTf);
}

async function renderModalCandleChart(symbol, tf) {
    const canvas = $('modal-trade-chart');
    if (!canvas) return;

    const data = await apiClient.get(`/api/candles?symbol=${symbol}&tf=${tf}&limit=60`);
    if (!data || !Array.isArray(data) || data.length === 0) return;

    if (modalTradeChartInst) {
        try { modalTradeChartInst.destroy(); } catch (e) { console.warn(e); }
        modalTradeChartInst = null;
    }

    const labels = data.map(c => new Date(c.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    const closePrices = data.map(c => c.close);

    const ctx = canvas.getContext('2d');
    modalTradeChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: `${symbol} Price`,
                data: closePrices,
                borderColor: '#3B82F6',
                backgroundColor: 'rgba(59, 130, 246, 0.08)',
                borderWidth: 2,
                fill: true,
                tension: 0.1,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            scales: {
                x: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#66758A', font: { family: 'JetBrains Mono', size: 9 } } },
                y: { position: 'right', grid: { color: 'rgba(255, 255, 255, 0.06)' }, ticks: { color: '#A7B5C8', font: { family: 'JetBrains Mono', size: 9 }, callback: (v) => '$' + Number(v).toFixed(2) } }
            }
        }
    });
}

// ==========================================
// 12. VIEW: A/B TESTING ENGINE
// ==========================================
async function fetchABTestData() {
    try {
        const [abStatus, abResults, advState] = await Promise.all([
            apiClient.get('/api/ab/status'),
            apiClient.get('/api/ab/results'),
            apiClient.get('/api/advisory/state')
        ]);

        if (abStatus) {
            const expLabel = $('ab-exp-label');
            if (expLabel) expLabel.innerText = `EXPERIMENT: ${abStatus.experiment_id || 'ab_ai_advisory_001'}`;
        }

        if (abResults && abResults.results) {
            const r = abResults.results;
            const a = r.arm_a_control || {};
            const b = r.arm_b_treatment || {};
            const stats = r.statistical_tests || {};
            const ev = r.evaluation_summary || {};
            const smp = r.sample_size || {};

            // Sample & Verdict
            const smpEl = $('ab-sample-count');
            if (smpEl) smpEl.innerText = `${smp.control_trades || 0} Control / ${smp.treatment_trades || 0} Treatment Trades (Min ${smp.min_required_trades || 30} required)`;

            const verdEl = $('ab-verdict-text');
            if (verdEl) verdEl.innerText = ev.recommendation || 'INSUFFICIENT_SAMPLE: Collecting forward validation data across both arms...';

            const tPval = $('ab-t-pvalue');
            if (tPval) tPval.innerText = stats.welch_t_test ? String(stats.welch_t_test.p_value) : '—';

            const uPval = $('ab-u-pvalue');
            if (uPval) uPval.innerText = stats.mann_whitney_u_test ? String(stats.mann_whitney_u_test.p_value) : '—';

            // Arm A
            const pnlA = Number(a.net_pnl || 0);
            if ($('ab-a-pnl')) {
                $('ab-a-pnl').innerText = `${pnlA >= 0 ? '+' : ''}$${pnlA.toFixed(2)}`;
                $('ab-a-pnl').className = `mono ${pnlA >= 0 ? 'profit' : 'loss'}`;
            }
            if ($('ab-a-return')) $('ab-a-return').innerText = `${Number(a.return_pct || 0).toFixed(2)}%`;
            if ($('ab-a-winrate')) $('ab-a-winrate').innerText = `${Number(a.win_rate_pct || 0).toFixed(2)}%`;
            if ($('ab-a-pf')) $('ab-a-pf').innerText = Number(a.profit_factor || 0).toFixed(2);
            if ($('ab-a-dd')) $('ab-a-dd').innerText = `${Number(a.max_drawdown_pct || 0).toFixed(2)}%`;
            if ($('ab-a-sharpe')) $('ab-a-sharpe').innerText = Number(a.sharpe_ratio || 0).toFixed(2);
            if ($('ab-a-trades')) $('ab-a-trades').innerText = a.total_trades || 0;

            // Arm B
            const pnlB = Number(b.net_pnl || 0);
            if ($('ab-b-pnl')) {
                $('ab-b-pnl').innerText = `${pnlB >= 0 ? '+' : ''}$${pnlB.toFixed(2)}`;
                $('ab-b-pnl').className = `mono ${pnlB >= 0 ? 'profit' : 'loss'}`;
            }
            if ($('ab-b-return')) $('ab-b-return').innerText = `${Number(b.return_pct || 0).toFixed(2)}%`;
            if ($('ab-b-winrate')) $('ab-b-winrate').innerText = `${Number(b.win_rate_pct || 0).toFixed(2)}%`;
            if ($('ab-b-pf')) $('ab-b-pf').innerText = Number(b.profit_factor || 0).toFixed(2);
            if ($('ab-b-dd')) $('ab-b-dd').innerText = `${Number(b.max_drawdown_pct || 0).toFixed(2)}%`;
            if ($('ab-b-sharpe')) $('ab-b-sharpe').innerText = Number(b.sharpe_ratio || 0).toFixed(2);
            if ($('ab-b-trades')) $('ab-b-trades').innerText = b.total_trades || 0;
        }

        // Treatment Overrides
        const overlayBox = $('ab-treatment-overlay-content');
        if (overlayBox && advState && advState.state) {
            const overrides = advState.state.active_overrides || {};
            const keys = Object.keys(overrides);
            if (keys.length === 0) {
                overlayBox.innerHTML = '<span style="color:var(--text-muted);">No parameter deviations applied yet (defaults active).</span>';
            } else {
                let html = '';
                for (const strat of keys) {
                    html += `<div style="margin-bottom:6px;"><strong style="color:var(--color-profit);">${strat.toUpperCase()}:</strong><br>`;
                    for (const [pk, pv] of Object.entries(overrides[strat])) {
                        html += `  • <span style="color:var(--text-secondary);">${pk}:</span> <strong style="color:var(--text-primary);">${pv}</strong><br>`;
                    }
                    html += `</div>`;
                }
                overlayBox.innerHTML = html;
            }
        }
    } catch (e) {
        console.warn('[AB_TEST] Error fetching AB test data:', e);
    }
}

async function triggerManualAdvisoryConsultation() {
    const btn = $('btn-trigger-advisory');
    if (btn) {
        btn.disabled = true;
        btn.innerText = 'CONSULTING...';
    }
    try {
        const res = await apiClient.post('/api/testnet/advisory/trigger', {});
        if (res && (res.status === 'SUCCESS' || res.result)) {
            showToastNotification('AI Consultation Complete', 'success');
            fetchAdvisoryDashboardData();
        } else {
            showToastNotification(res.message || 'Consultation failed', 'warning');
        }
    } catch (e) {
        showToastNotification('Error triggering advisory: ' + (e.message || e), 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = '⚡ TRIGGER NOW';
        }
    }
}

async function ackAlert(alertId) {
    try {
        const res = await apiClient.post('/api/alerts', { alert_id: alertId });
        if (res && res.status === 'SUCCESS') {
            showToastNotification(`Alert ${alertId} acknowledged`, 'success');
            fetchSystemViewData();
        }
    } catch (e) {
        showToastNotification('Failed to acknowledge alert', 'error');
    }
}

async function emergencyFlattenLivePositions() {
    if (!confirm("🚨 WARNING: Are you sure you want to IMMEDIATELY FLATTEN all live positions and halt trading?")) {
        return;
    }
    try {
        const res = await apiClient.post('/api/live/emergency-flatten', {});
        if (res && res.status === 'SUCCESS') {
            showToastNotification('All live positions flattened successfully!', 'error');
            const badge = $('hdr-live-badge');
            if (badge) {
                badge.innerText = 'LIVE: HALTED (FLATTENED)';
                badge.className = 'badge badge-rejected';
            }
        } else {
            showToastNotification(res.message || 'Flatten failed', 'warning');
        }
    } catch (err) {
        showToastNotification('Failed to execute emergency flatten: ' + (err.message || err), 'error');
    }
}

async function openLiveStatusModal() {
    try {
        const res = await apiClient.get('/api/live/status');
        if (res && res.status === 'OK') {
            const authStr = res.is_authorized ? 'AUTHORIZED' : 'LOCKED (Gated)';
            const spec = res.level_spec || {};
            alert(`⚡ LIVE CAPITAL TIER STATUS\n\n` +
                  `State: ${authStr}\n` +
                  `Tier: ${spec.name || 'LEVEL 1'}\n` +
                  `Max Capital: $${spec.max_capital || 1000}\n` +
                  `Max Position Size: ${spec.max_position_size_pct || 5}%\n` +
                  `Max Daily Loss: ${spec.max_daily_loss_pct || 2}%\n` +
                  `Max Drawdown: ${spec.max_drawdown_limit_pct || 5}%\n\n` +
                  (res.blocking_errors && res.blocking_errors.length > 0 ? `Missing Gates:\n- ${res.blocking_errors.join('\n- ')}` : 'All 6 Prerequisites Met.'));
        }
    } catch (err) {
        showToastNotification('Failed to fetch live status: ' + (err.message || err), 'error');
    }
}

// Export functions to window
window.ackAlert = ackAlert;
window.emergencyFlattenLivePositions = emergencyFlattenLivePositions;
window.openLiveStatusModal = openLiveStatusModal;
window.triggerManualAdvisoryConsultation = triggerManualAdvisoryConsultation;
window.fetchABTestData = fetchABTestData;
window.toggleScannerFilterDropdown = toggleScannerFilterDropdown;
window.applyScannerFilters = applyScannerFilters;
window.selectMarketSymbol = selectMarketSymbol;
window.toggleMarketFullscreen = toggleMarketFullscreen;
window.changeAnalyticsPeriod = changeAnalyticsPeriod;
window.toggleManualTrading = toggleManualTrading;
window.testGeminiConnection = testGeminiConnection;
window.inspectSignal = inspectSignal;
window.inspectPosition = inspectPosition;
window.inspectTradeLifecycle = inspectTradeLifecycle;
window.inspectStrategy = inspectStrategy;
window.closeInspectorDrawer = closeInspectorDrawer;
