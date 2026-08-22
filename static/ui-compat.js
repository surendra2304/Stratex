/*
 * ui-compat.js — Interactive controller bridge (2026-08-22)
 *
 * The redesigned index.html ships inline handlers (changeMarketSymbol,
 * fetchRiskData, saveSettings, ...) whose names differ from app.js's internal
 * functions (selectMarketSymbol, fetchRiskViewData, ...). Before this file,
 * every one of those controls was dead. This module implements every missing
 * handler, maps it onto the existing data layer, and adds the genuinely new
 * behaviours (settings persistence, sound/notification toggles, chart
 * drawings). Loaded after app.js; shares its top-level state.
 */
"use strict";

/* ── Markets view controls ─────────────────────────────────────────────── */

function changeMarketSymbol(symbol, el) {
    document.querySelectorAll('#mkt-symbol-row .btn-sym').forEach(b => b.classList.remove('active'));
    if (el && el.classList) el.classList.add('active');
    if (typeof selectMarketSymbol === 'function') selectMarketSymbol(String(symbol).toUpperCase());
}

function changeMarketTimeframe(tf, el) {
    document.querySelectorAll('#mkt-tf-row .btn-tf').forEach(b => b.classList.remove('active'));
    if (el && el.classList) el.classList.add('active');
    currentMarketTf = tf;
    if (typeof fetchMarketCandlesAndRender === 'function') fetchMarketCandlesAndRender();
}

function toggleMarketDropdown(ddId) {
    const dd = document.getElementById(ddId);
    if (!dd) return;
    document.querySelectorAll('.mkt-dd, .dropdown').forEach(d => { if (d.id !== ddId) d.classList.add('hidden'); });
    dd.classList.toggle('hidden');
}

function toggleMarketsFullscreen() {
    if (typeof toggleMarketFullscreen === 'function') toggleMarketFullscreen();
}

function setChartType(type) {
    currentMarketType = type;
    const label = { area: 'Area', line: 'Line', stepped: 'Stepped', candlestick: 'Candles' }[type] || type;
    const btn = document.getElementById('btn-mkt-type');
    if (btn) btn.innerText = label + ' ▾';
    const dd = document.getElementById('mkt-dd-type');
    if (dd) dd.classList.add('hidden');
    if (typeof fetchMarketCandlesAndRender === 'function') fetchMarketCandlesAndRender();
}

/* Chart drawings: horizontal level at last close, ±2% channel, or clear. */
let marketDrawings = [];

async function applyChartDrawing(mode) {
    const dd = document.getElementById('mkt-dd-draw');
    if (dd) dd.classList.add('hidden');
    if (mode === 'clear') {
        marketDrawings = [];
        showToast('Chart drawings cleared', 'info');
        if (typeof fetchMarketCandlesAndRender === 'function') await fetchMarketCandlesAndRender();
        return;
    }
    const data = await apiClient.get(`/api/candles?symbol=${currentMarketSymbol}&tf=${currentMarketTf}&limit=2`);
    const lastClose = data && data.length ? Number(data[data.length - 1].close) : null;
    if (!lastClose) { showToast('Cannot draw: no price data', 'error'); return; }
    if (mode === 'horiz') {
        marketDrawings.push({ type: 'horizontal', price: lastClose, color: '#F59E0B' });
        showToast(`Horizontal level drawn at $${lastClose.toFixed(2)}`, 'success');
    } else if (mode === 'channel') {
        marketDrawings.push({ type: 'horizontal', price: lastClose * 1.02, color: '#22C55E' });
        marketDrawings.push({ type: 'horizontal', price: lastClose * 0.98, color: '#EF4444' });
        showToast('Channel drawn at ±2%', 'success');
    }
    // Re-render chart then overlay the stored drawings as extra datasets.
    if (typeof fetchMarketCandlesAndRender === 'function') await fetchMarketCandlesAndRender();
    if (marketMainChartInst && marketMainChartInst.data && marketMainChartInst.data.datasets) {
        const labels = marketMainChartInst.data.labels || [];
        marketDrawings.forEach(d => {
            marketMainChartInst.data.datasets.push({
                label: d.type === 'horizontal' ? `$${d.price.toFixed(2)}` : 'draw',
                data: labels.map(() => d.price),
                borderColor: d.color,
                borderWidth: 1,
                borderDash: [6, 4],
                pointRadius: 0,
                fill: false,
            });
        });
        marketMainChartInst.update('none');
    }
}

/* ── Inspector modal timeframe ─────────────────────────────────────────── */

function changeModalTimeframe(tf, el) {
    document.querySelectorAll('#modal-tf-row .btn-tf').forEach(b => b.classList.remove('active'));
    if (el && el.classList) el.classList.add('active');
    currentModalTf = tf;
    renderModalCandleChart(currentModalSymbol || 'BTCUSDT', tf);
}

/* ── View data refresh buttons ─────────────────────────────────────────── */

function fetchPositionsV2()  { if (typeof fetchPositionsData === 'function')  fetchPositionsData(); }
function fetchStrategiesV2() { if (typeof fetchStrategiesData === 'function') fetchStrategiesData(); }
function fetchRiskData()     { if (typeof fetchRiskViewData === 'function')   fetchRiskViewData(); }
function fetchSystemData()   { if (typeof fetchSystemViewData === 'function') fetchSystemViewData(); }
function fetchAnalyticsData(){ if (typeof fetchAnalyticsViewData === 'function') fetchAnalyticsViewData(); }

/* ── Settings persistence ──────────────────────────────────────────────── */

async function saveSettings() {
    // Map UI inputs onto the whitelisted runtime knobs the API actually
    // persists (see SETTINGS_WHITELIST in dashboard.py). Other controls are
    // display-only; sending them would be rejected by the API.
    const mapping = {
        'set-max-open':     ['max_open_positions', parseInt],
        'set-max-day':      ['max_trades_per_day', parseInt],
        'set-risk-trade':   ['risk_per_trade', parseFloat],
        'set-risk-port':    ['max_portfolio_risk', parseFloat],
        'set-exp-port':     ['max_portfolio_exposure', parseFloat],
        'set-max-dd':       ['max_drawdown', parseFloat],
        'set-daily-loss':   ['daily_loss_limit_pct', parseFloat],
        'set-min-edge':     ['min_expected_edge', parseFloat],
    };
    const payload = {};
    for (const [id, [key, cast]] of Object.entries(mapping)) {
        const el = document.getElementById(id);
        if (!el) continue;
        const v = cast(el.value);
        if (!isNaN(v)) payload[key] = v;
    }
    if (Object.keys(payload).length === 0) {
        showToast('No runtime-adjustable settings in this panel', 'info');
        return;
    }
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const body = await res.json().catch(() => ({}));
        if (res.ok) {
            showToast(body.message || 'Settings saved', 'success');
            playAudioAlert('success');
        } else {
            showToast(body.error || ('Save failed (HTTP ' + res.status + ')'), 'error');
        }
    } catch (e) {
        showToast('Settings save error: ' + e.message, 'error');
    }
}

async function resetSettings() {
    if (typeof fetchSettingsViewData === 'function') await fetchSettingsViewData();
    showToast('Settings restored from server configuration', 'info');
}

/* ── Sound & notifications ─────────────────────────────────────────────── */

function toggleSoundAlerts() {
    isSoundEnabled = !isSoundEnabled;
    try { localStorage.setItem('soundEnabled', isSoundEnabled ? '1' : '0'); } catch (e) { /* ignore */ }
    const txt = document.getElementById('sound-status-text');
    if (txt) txt.textContent = isSoundEnabled ? 'ON' : 'OFF';
    const btn = document.getElementById('btn-sound-toggle');
    if (btn) btn.classList.toggle('muted', !isSoundEnabled);
    showToast('Sound alerts ' + (isSoundEnabled ? 'enabled' : 'disabled'), 'info');
}

let notifDropdownOpen = false;
function toggleNotificationDropdown() {
    const badge = document.getElementById('notif-badge');
    const count = badge && badge.textContent ? parseInt(badge.textContent, 10) || 0 : 0;
    showToast(count > 0 ? `${count} active alert${count === 1 ? '' : 's'} — see System view for details` : 'No active alerts', count > 0 ? 'warning' : 'info');
    notifDropdownOpen = !notifDropdownOpen;
}

function playAudioAlert(type) {
    if (!isSoundEnabled && type !== 'error') return;
    if (typeof playNotificationSound === 'function') playNotificationSound();
}

/* ── Export to window for inline handlers ──────────────────────────────── */
window.changeMarketSymbol = changeMarketSymbol;
window.changeMarketTimeframe = changeMarketTimeframe;
window.toggleMarketDropdown = toggleMarketDropdown;
window.toggleMarketsFullscreen = toggleMarketsFullscreen;
window.setChartType = setChartType;
window.applyChartDrawing = applyChartDrawing;
window.changeModalTimeframe = changeModalTimeframe;
window.fetchPositionsV2 = fetchPositionsV2;
window.fetchStrategiesV2 = fetchStrategiesV2;
window.fetchRiskData = fetchRiskData;
window.fetchSystemData = fetchSystemData;
window.fetchAnalyticsData = fetchAnalyticsData;
window.saveSettings = saveSettings;
window.resetSettings = resetSettings;
window.toggleSoundAlerts = toggleSoundAlerts;
window.toggleNotificationDropdown = toggleNotificationDropdown;
window.playAudioAlert = playAudioAlert;
