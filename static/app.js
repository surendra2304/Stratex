/* Stratex operations desk. All displayed metrics come from read-only APIs. */
(() => {
  'use strict';

  const REFRESH_MS = 15000;
  const resourceUrls = {
    account: '/api/status',
    engine: '/api/engine-health',
    paper: '/api/paper/forward-status',
    positions: '/api/positions?status=OPEN',
    trades: '/api/telemetry/trades?status=CLOSED&limit=100',
    equity: '/api/equity-history?range=7d',
    markets: '/api/markets',
    signals: '/api/telemetry/signals?limit=30'
  };
  const resources = Object.fromEntries(Object.keys(resourceUrls).map((key) => [key, {
    state: 'loading', value: null, error: null, updatedAt: null
  }]));
  const viewCopy = {
    overview: ['Overview', 'A live view of Stratex service data, positions, and paper validation.'],
    portfolio: ['Portfolio', 'Current exposure and recorded closed-trade results.'],
    activity: ['Trade activity', 'Closed trades and signal events returned by Stratex telemetry.'],
    markets: ['Markets', 'Market quotes currently reported as streaming by the service.'],
    health: ['System health', 'Read-only health and persistence status from each service endpoint.']
  };
  let isRefreshing = false;


  const byId = (id) => document.getElementById(id);
  const setText = (id, value) => {
    const node = byId(id);
    if (node) node.textContent = value;
  };
  const numeric = (value) => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const formatMoney = (value, currency = 'USDT') => {
    const amount = numeric(value);
    if (amount === null) return '—';
    const formatted = new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2
    }).format(amount);
    return currency === 'USDT' ? `${formatted} USDT` : formatted;
  };
  const formatNumber = (value, decimals = 4) => {
    const amount = numeric(value);
    return amount === null ? '—' : amount.toLocaleString('en-US', {
      minimumFractionDigits: 0, maximumFractionDigits: decimals
    });
  };
  const formatPct = (value) => {
    const amount = numeric(value);
    return amount === null ? '—' : `${amount.toFixed(2)}%`;
  };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const formatDate = (value, options = {}) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return new Intl.DateTimeFormat(undefined, options).format(date);
  };
  const formatUtc = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    return `${date.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
  };
  const moneyClass = (value) => {
    const amount = numeric(value);
    return amount === null ? 'neutral' : amount > 0 ? 'positive' : amount < 0 ? 'negative' : 'neutral';
  };
  const valueOrUnavailable = (value) => value === null || value === undefined || value === '' ? 'Unavailable' : String(value);

  function stateMarkup(state, message) {
    const label = state === 'error' ? 'Could not load' : state === 'loading' ? 'Loading' : 'No records';
    return `<div class="section-state" data-state="${escapeHtml(state)}"><span class="state-symbol">${state === 'error' ? '!' : state === 'loading' ? '◌' : '∅'}</span><strong>${label}</strong><span>${escapeHtml(message)}</span></div>`;
  }

  async function fetchJson(url) {
    const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(12000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function refreshData() {
    if (isRefreshing) return;
    isRefreshing = true;
    const refreshButton = byId('btn-refresh');
    if (refreshButton) refreshButton.classList.add('is-loading');

    const settled = await Promise.allSettled(Object.entries(resourceUrls).map(async ([key, url]) => [key, await fetchJson(url)]));
    settled.forEach((result) => {
      if (result.status === 'fulfilled') {
        const [key, value] = result.value;
        resources[key] = { state: 'ready', value, error: null, updatedAt: Date.now() };
      } else {
        const index = settled.indexOf(result);
        const key = Object.keys(resourceUrls)[index];
        const previous = resources[key];
        resources[key] = {
          state: previous.value ? 'stale' : 'error',
          value: previous.value,
          error: result.reason instanceof Error ? result.reason.message : 'Request failed',
          updatedAt: previous.updatedAt
        };
      }
    });

    renderAll();
    const now = new Date();
    setText('last-sync-time', `Synced ${now.toLocaleTimeString()}`);
    setText('footer-update', `Last refresh ${now.toLocaleTimeString()}`);
    if (refreshButton) refreshButton.classList.remove('is-loading');
    isRefreshing = false;
  }

  function renderAll() {
    renderHeader();
    renderMetrics();
    renderEquity();
    renderEngine();
    renderPaper();
    renderPositions();
    renderTrades();
    renderDailyPnl();
    renderMarkets();
    renderSignals();
    renderHealth();
    renderSources();
  }

  function renderHeader() {
    const engineResource = resources.engine;
    const engine = engineResource.value;
    const account = resources.account.value;
    const paper = resources.paper.value;
    const mode = account && account.mode ? account.mode : paper && paper.mode ? paper.mode : null;
    setText('header-mode', mode ? `${mode} MODE` : 'Mode unavailable');
    setText('heading-date', new Intl.DateTimeFormat(undefined, { dateStyle: 'full' }).format(new Date()));

    const connection = byId('connection-state');
    const runtime = byId('runtime-pill');
    const engineStatus = engine && engine.engine_status ? String(engine.engine_status).toUpperCase() : null;
    const online = Boolean(engine && engine.healthy === true && engineStatus === 'ONLINE');
    const state = engineResource.state === 'error' ? 'error' : engineResource.state === 'stale' ? 'stale' : online ? 'online' : 'warning';
    if (connection) connection.dataset.state = state;
    if (runtime) runtime.dataset.state = state;

    setText('connection-label', engineResource.state === 'error' ? 'Engine health unavailable' : engineStatus ? `Engine ${engineStatus.toLowerCase()}` : 'Engine state unknown');
    setText('runtime-title', engineResource.state === 'error' ? 'Service health unavailable' : online ? 'Engine online' : engineStatus ? `Engine ${engineStatus.toLowerCase()}` : 'Engine state unknown');
    const activeStrategies = engine && Array.isArray(engine.strategies) ? engine.strategies : null;
    const context = engineResource.state === 'stale' ? 'Showing the last successful engine response; refresh failed.'
      : engineResource.state === 'error' ? `Health request failed: ${engineResource.error || 'unknown error'}`
        : activeStrategies && activeStrategies.length === 0 ? 'The general engine currently reports no active strategies.'
          : activeStrategies ? `${activeStrategies.length} active strategy${activeStrategies.length === 1 ? '' : 'ies'} reported.`
            : 'Strategy state was not included in the service response.';
    setText('runtime-caption', context);
    setText('sidebar-engine', engineStatus || 'Unknown');
    setText('sidebar-caption', online ? 'Service reports healthy' : engineStatus ? `Service reports ${engineStatus.toLowerCase()}` : 'Waiting for service data');
    const dot = byId('sidebar-dot');
    if (dot) dot.classList.toggle('muted', !online);
  }

  function renderMetrics() {
    const accountState = resources.account;
    const account = accountState.value;
    const engine = resources.engine.value;
    const tradesState = resources.trades;
    const tradesPayload = tradesState.value;
    const positionsState = resources.positions;
    const positionsPayload = positionsState.value;
    const tradeRows = tradesPayload && Array.isArray(tradesPayload.trades) ? tradesPayload.trades : null;
    const positionRows = positionsPayload && Array.isArray(positionsPayload.positions) ? positionsPayload.positions : null;

    const accountAvailable = Boolean(account && accountState.state !== 'error' && engine && engine.binance_connected === true);
    setText('kpi-balance', accountAvailable ? formatMoney(account.equity) : '—');
    setText('kpi-equity-sub', accountAvailable ? `Cash ${formatMoney(account.cash)} · Mode ${valueOrUnavailable(account.mode)}` : accountState.state === 'error' ? 'Account endpoint unavailable' : 'Waiting for verified engine connection');

    const net = accountAvailable ? numeric(account.total_pnl) : null;
    setText('kpi-total-pnl', net === null ? '—' : formatMoney(net));
    setMoneyTone('kpi-total-pnl', net);
    setText('kpi-pnl-sub', accountAvailable ? `Realized ${formatMoney(account.realized_pnl)} · Open ${formatMoney(account.unrealized_pnl)}` : 'Account P&L unavailable');

    const today = accountAvailable ? numeric(account.today_pnl) : null;
    setText('kpi-today-pnl', today === null ? '—' : formatMoney(today));
    setMoneyTone('kpi-today-pnl', today);
    setText('kpi-today-sub', accountAvailable ? `UTC day · ${formatUtc(account.server_time)}` : 'Daily P&L unavailable');

    const openCount = positionsPayload && numeric(positionsPayload.count) !== null
      ? Number(positionsPayload.count) : positionRows ? positionRows.length : null;
    setText('kpi-open-count', positionsState.state === 'error' ? '—' : openCount === null ? '—' : String(openCount));
    setText('kpi-open-unrealized', accountAvailable ? `Open P&L ${formatMoney(account.unrealized_pnl)}` : 'From position telemetry');

    const closedCount = tradesPayload && numeric(tradesPayload.count) !== null
      ? Number(tradesPayload.count) : tradeRows ? tradeRows.length : null;
    setText('kpi-trades-count', tradesState.state === 'error' ? '—' : closedCount === null ? '—' : String(closedCount));
    const wins = tradeRows ? tradeRows.filter((trade) => numeric(trade.net_pnl) > 0).length : null;
    const winRate = closedCount !== null && wins !== null && closedCount > 0 ? (wins / closedCount) * 100 : closedCount === 0 ? null : null;
    setText('kpi-win-rate', tradesState.state === 'error' ? 'Trade telemetry unavailable' : closedCount === 0 ? 'No closed trades recorded' : winRate === null ? 'Win rate unavailable' : `${formatPct(winRate)} from returned records`);
  }

  function setMoneyTone(id, value) {
    const node = byId(id);
    if (!node) return;
    node.classList.remove('positive', 'negative', 'neutral');
    node.classList.add(moneyClass(value));
  }

  function renderEquity() {
    const resource = resources.equity;
    const payload = resource.value;
    const snapshots = payload && Array.isArray(payload.snapshots) ? payload.snapshots : null;
    const valid = snapshots ? snapshots.map((point) => ({
      timestamp: point.timestamp || point.time,
      equity: numeric(point.equity)
    })).filter((point) => point.timestamp && point.equity !== null) : [];
    const wrap = byId('equity-chart-wrap');
    const svg = byId('equity-chart');
    if (!wrap || !svg) return;

    if (resource.state === 'error' && !payload) {
      wrap.dataset.state = 'error';
      svg.hidden = true;
      setText('equity-chart-state', `Equity history unavailable: ${resource.error || 'request failed'}`);
      setText('chart-current', '—');
      setText('chart-range', 'No timeline loaded');
      setText('chart-point-count', 'No data');
      return;
    }
    if (valid.length === 0) {
      wrap.dataset.state = 'empty';
      svg.hidden = true;
      setText('equity-chart-state', resource.state === 'stale' ? 'The last successful response contained no equity points.' : 'No recorded equity points are available for this range.');
      setText('chart-current', '—');
      setText('chart-range', resource.state === 'stale' ? 'Data may be stale' : 'No recorded history');
      setText('chart-point-count', '0 points');
      return;
    }

    wrap.dataset.state = resource.state === 'stale' ? 'stale' : 'ready';
    svg.hidden = false;
    setText('equity-chart-state', '');
    const values = valid.map((point) => point.equity);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || Math.max(Math.abs(max) * 0.01, 1);
    const xPad = 12;
    const yPad = 22;
    const width = 780;
    const height = 250;
    const points = valid.map((point, index) => {
      const x = valid.length === 1 ? width / 2 : xPad + (index / (valid.length - 1)) * (width - xPad * 2);
      const y = height - yPad - ((point.equity - min) / range) * (height - yPad * 2);
      return [x, y];
    });
    const line = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `${line} L${points[points.length - 1][0].toFixed(1)},${height} L${points[0][0].toFixed(1)},${height} Z`;
    const lastPoint = points[points.length - 1];
    svg.innerHTML = `<defs><linearGradient id="equity-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#b4f46b" stop-opacity=".24"/><stop offset="1" stop-color="#b4f46b" stop-opacity="0"/></linearGradient></defs><path class="chart-area" d="${area}"/><path class="chart-line" d="${line}"/><circle class="chart-end" cx="${lastPoint[0].toFixed(1)}" cy="${lastPoint[1].toFixed(1)}" r="4"/>`;
    setText('chart-current', formatMoney(values[values.length - 1]));
    setText('chart-range', `${formatDate(valid[0].timestamp, { month: 'short', day: 'numeric' })} — ${formatDate(valid[valid.length - 1].timestamp, { month: 'short', day: 'numeric' })}${resource.state === 'stale' ? ' · stale' : ''}`);
    setText('chart-point-count', `${valid.length} recorded point${valid.length === 1 ? '' : 's'}`);
  }

  function renderEngine() {
    const resource = resources.engine;
    const data = resource.value;
    const badge = byId('diag-engine-status');
    const status = data && data.engine_status ? String(data.engine_status).toUpperCase() : null;
    const state = resource.state === 'error' ? 'error' : resource.state === 'stale' ? 'stale' : data && data.healthy === true ? 'online' : data ? 'warning' : 'loading';
    if (badge) {
      badge.dataset.state = state;
      badge.textContent = status || (resource.state === 'error' ? 'Unavailable' : 'Unknown');
    }
    const ring = byId('engine-ring');
    if (ring) ring.dataset.state = state;
    setText('engine-initial', status ? status.slice(0, 1) : '·');
    setText('engine-summary-title', resource.state === 'error' ? 'Health request failed' : status ? `Engine ${status.toLowerCase()}` : 'Engine state unavailable');
    setText('engine-summary-copy', data ? (data.healthy === true ? 'Health endpoint reports healthy' : 'Health endpoint reports degraded') : resource.error || 'No engine payload received');

    const strategies = data && Array.isArray(data.strategies) ? data.strategies : null;
    setText('engine-strategies', strategies === null ? 'Unavailable' : strategies.length ? strategies.join(', ') : 'None reported');
    setText('engine-feed', data ? data.websocket_connected === true ? 'Connected' : data.websocket_connected === false ? 'Disconnected' : 'Unknown' : 'Unavailable');
    setText('engine-candle', data && data.last_candle_close ? formatUtc(data.last_candle_close) : 'Unavailable');
    const age = data ? numeric(data.heartbeat_age_seconds) : null;
    setText('engine-heartbeat', age === null ? 'Unavailable' : `${formatNumber(age, 1)}s ago`);
    setText('engine-context', resource.state === 'stale' ? `Showing last successful response. Latest refresh failed: ${resource.error}` : strategies && strategies.length === 0 ? 'This general engine reports no active strategies. The paper experiment below is separate.' : 'This general engine status does not describe the isolated paper experiment below.');
  }

  function renderPaper() {
    const resource = resources.paper;
    const data = resource.value;
    const stateValue = data && data.runner_status ? String(data.runner_status).toUpperCase() : null;
    const state = resource.state === 'error' ? 'error' : resource.state === 'stale' ? 'stale' : stateValue === 'RUNNING' || stateValue === 'HEALTHY' ? 'online' : data ? 'warning' : 'loading';
    const badge = byId('paper-state');
    if (badge) {
      badge.dataset.state = state;
      badge.textContent = stateValue || (resource.state === 'error' ? 'Unavailable' : 'Unknown');
    }
    const age = data ? numeric(data.experiment_age_days) : null;
    const signalCount = data ? numeric(data.signal_count) : null;
    const closed = data ? numeric(data.closed_trades) : null;
    const open = data ? numeric(data.open_positions) : null;
    setText('paper-mode', data && data.mode ? `${data.mode} experiment` : 'Paper experiment unavailable');
    setText('paper-runner-status', data ? `Runner ${stateValue || 'unknown'} · validation ${valueOrUnavailable(data.validation_status)}` : resource.error || 'Waiting for experiment status');
    setText('paper-age', age === null ? 'Unavailable' : `${age.toFixed(3)} days`);
    setText('paper-signals', signalCount === null ? 'Unavailable' : signalCount.toLocaleString());
    setText('paper-closed', closed === null ? 'Unavailable' : closed.toLocaleString());
    setText('paper-open', open === null ? 'Unavailable' : open.toLocaleString());

    const warning = byId('paper-warning');
    if (warning) {
      const persistence = data && data.durability_status ? String(data.durability_status) : null;
      const cloud = data && data.cloud_persistence_status ? String(data.cloud_persistence_status) : null;
      const message = persistence && persistence !== 'DURABLE' ? `Storage: ${persistence.replaceAll('_', ' ').toLowerCase()}${cloud ? ` · cloud persistence ${cloud.replaceAll('_', ' ').toLowerCase()}` : ''}.` : '';
      warning.hidden = !message;
      warning.textContent = message;
    }

    const validation = byId('paper-validation');
    if (!validation) return;
    if (!data) {
      validation.dataset.state = resource.state === 'error' ? 'error' : 'loading';
      validation.innerHTML = stateMarkup(resource.state === 'error' ? 'error' : 'loading', resource.error || 'Waiting for paper status');
      return;
    }
    const reasons = Array.isArray(data.validation_reasons) ? data.validation_reasons : [];
    validation.dataset.state = reasons.length ? 'warning' : 'ready';
    if (reasons.length === 0) {
      validation.innerHTML = '<span class="validation-ok">No validation blockers were returned.</span>';
      return;
    }
    validation.innerHTML = `<span class="validation-title">Validation gates still open</span><ul>${reasons.map((reason) => `<li>${escapeHtml(String(reason).replaceAll('_', ' ').toLowerCase())}</li>`).join('')}</ul>`;
  }

  function positionRows() {
    const payload = resources.positions.value;
    return payload && Array.isArray(payload.positions) ? payload.positions : null;
  }

  function renderPositions() {
    const resource = resources.positions;
    const rows = positionRows();
    const overviewBody = byId('overview-positions-tbody');
    const fullBody = byId('open-positions-tbody');
    const count = rows ? rows.length : null;
    setText('open-positions-count', count === null ? 'Unavailable' : `${count} open`);
    const message = resource.state === 'error' ? `Position request failed: ${resource.error || 'unknown error'}` : resource.state === 'stale' ? 'Showing the last successful position response; refresh failed.' : 'No open positions were returned by the service.';
    const rendered = rows && rows.length ? rows.map((position) => {
      const side = position.side || position.direction || null;
      const pnl = position.unrealized_pnl ?? position.current_unrealized_pnl;
      const cells = [
        `<span class="symbol-cell">${escapeHtml(position.symbol || '—')}</span>`,
        `<span class="side-pill ${String(side || '').toUpperCase().includes('SELL') || String(side || '').toUpperCase().includes('SHORT') ? 'short' : 'long'}">${escapeHtml(side || '—')}</span>`,
        escapeHtml(formatNumber(position.quantity)), escapeHtml(formatNumber(position.entry_price)),
        escapeHtml(formatNumber(position.current_price ?? position.mark_price)),
        `<span class="${moneyClass(pnl)}">${escapeHtml(formatMoney(pnl))}</span>`
      ];
      return `<tr>${cells.map((cell) => `<td>${cell}</td>`).join('')}</tr>`;
    }).join('') : stateMarkup(resource.state === 'error' && !rows ? 'error' : rows ? 'empty' : 'loading', rows ? message : resource.error || 'Loading position records');
    if (overviewBody) overviewBody.innerHTML = rows && rows.length ? rendered : `<tr><td colspan="6" class="empty-cell">${rendered}</td></tr>`;
    if (fullBody) {
      if (rows && rows.length) {
        fullBody.innerHTML = rows.map((position) => {
          const side = position.side || position.direction;
          const pnl = position.unrealized_pnl ?? position.current_unrealized_pnl;
          return `<tr><td><span class="symbol-cell">${escapeHtml(position.symbol || '—')}</span></td><td><span class="side-pill ${String(side || '').toUpperCase().includes('SELL') || String(side || '').toUpperCase().includes('SHORT') ? 'short' : 'long'}">${escapeHtml(side || '—')}</span></td><td>${escapeHtml(formatNumber(position.quantity))}</td><td>${escapeHtml(formatNumber(position.entry_price))}</td><td>${escapeHtml(formatNumber(position.current_price ?? position.mark_price))}</td><td class="${moneyClass(pnl)}">${escapeHtml(formatMoney(pnl))}</td><td>${escapeHtml(position.strategy || '—')}</td><td>${escapeHtml(formatUtc(position.entry_timestamp || position.timestamp))}</td></tr>`;
        }).join('');
      } else {
        fullBody.innerHTML = `<tr><td colspan="8" class="empty-cell">${stateMarkup(resource.state === 'error' && !rows ? 'error' : rows ? 'empty' : 'loading', rows ? message : resource.error || 'Loading position records')}</td></tr>`;
      }
    }
  }

  function tradeRows() {
    const payload = resources.trades.value;
    return payload && Array.isArray(payload.trades) ? payload.trades : null;
  }

  function renderTrades() {
    const resource = resources.trades;
    const rows = tradeRows();
    const body = byId('trades-history-tbody');
    const badge = byId('total-trades-badge');
    const count = rows ? rows.length : null;
    if (badge) badge.textContent = count === null ? 'Unavailable' : `${count} returned`;
    setText('tab-badge-trades', count === null ? '—' : `${count} closed`);
    if (!body) return;
    if (!rows) {
      body.innerHTML = `<tr><td colspan="9" class="empty-cell">${stateMarkup('error', resource.error || 'Trade telemetry unavailable')}</td></tr>`;
      return;
    }
    const query = (byId('trades-search')?.value || '').trim().toUpperCase();
    const filtered = rows.filter((trade) => !query || String(trade.symbol || '').toUpperCase().includes(query));
    if (!filtered.length) {
      body.innerHTML = `<tr><td colspan="9" class="empty-cell">${stateMarkup('empty', rows.length ? 'No returned trades match this filter.' : 'No closed trade records were returned.')}</td></tr>`;
      return;
    }
    body.innerHTML = filtered.map((trade) => {
      const pnl = trade.net_pnl;
      const fees = trade.total_fees;
      return `<tr><td class="muted-cell">${escapeHtml(formatUtc(trade.close_timestamp || trade.close_time))}</td><td><span class="symbol-cell">${escapeHtml(trade.symbol || '—')}</span></td><td>${escapeHtml(trade.side || '—')}</td><td>${escapeHtml(trade.strategy || '—')}</td><td>${escapeHtml(formatNumber(trade.quantity))}</td><td>${escapeHtml(formatNumber(trade.entry_price))}</td><td>${escapeHtml(formatNumber(trade.exit_price))}</td><td class="${moneyClass(pnl)}">${escapeHtml(formatMoney(pnl))}</td><td>${escapeHtml(formatMoney(fees))}</td></tr>`;
    }).join('');
  }

  function renderDailyPnl() {
    const resource = resources.trades;
    const trades = tradeRows();
    const body = byId('daily-pnl-tbody');
    if (!body) return;
    if (!trades) {
      body.innerHTML = `<tr><td colspan="5" class="empty-cell">${stateMarkup('error', resource.error || 'Trade telemetry unavailable')}</td></tr>`;
      return;
    }
    const days = new Map();
    trades.forEach((trade) => {
      const dateValue = trade.close_timestamp || trade.close_time;
      if (!dateValue) return;
      const date = new Date(dateValue);
      if (Number.isNaN(date.getTime())) return;
      const key = date.toISOString().slice(0, 10);
      const day = days.get(key) || { count: 0, wins: 0, losses: 0, net: 0, knownPnl: 0 };
      day.count += 1;
      const pnl = numeric(trade.net_pnl);
      if (pnl !== null) {
        day.knownPnl += 1;
        day.net += pnl;
        if (pnl > 0) day.wins += 1;
        if (pnl < 0) day.losses += 1;
      }
      days.set(key, day);
    });
    const ordered = [...days.entries()].sort(([a], [b]) => b.localeCompare(a));
    setText('tab-badge-daily', `${ordered.length} days`);
    if (!ordered.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty-cell">${stateMarkup('empty', 'No closed trades with a close timestamp were returned.')}</td></tr>`;
      return;
    }
    body.innerHTML = ordered.map(([date, item]) => `<tr><td>${escapeHtml(date)}</td><td>${item.count}</td><td>${item.wins}</td><td>${item.losses}</td><td class="${moneyClass(item.knownPnl ? item.net : null)}">${item.knownPnl === item.count ? escapeHtml(formatMoney(item.net)) : 'Incomplete P&L data'}</td></tr>`).join('');
  }

  function renderMarkets() {
    const resource = resources.markets;
    const host = byId('market-scanner-grid');
    if (!host) return;
    const all = resource.value && Array.isArray(resource.value.markets) ? resource.value.markets : null;
    const live = all ? all.filter((market) => market.status === 'STREAMING' && numeric(market.price) !== null && numeric(market.price) > 0) : null;
    setText('scanned-symbols-count', live ? `${live.length} streaming quotes` : 'Unavailable');
    if (!live) {
      host.dataset.state = 'error';
      host.innerHTML = stateMarkup('error', resource.error || 'Market endpoint returned no quote list.');
      return;
    }
    if (!live.length) {
      host.dataset.state = 'empty';
      host.innerHTML = stateMarkup('empty', 'The service returned no quotes marked STREAMING.');
      return;
    }
    host.dataset.state = resource.state === 'stale' ? 'stale' : 'ready';
    host.innerHTML = live.map((market) => {
      const change = numeric(market.change_24h);
      return `<article class="market-card"><div class="market-card-top"><span class="symbol-cell">${escapeHtml(market.symbol || '—')}</span><span class="market-change ${moneyClass(change)}">${change === null ? '—' : `${change > 0 ? '+' : ''}${formatPct(change)}`}</span></div><strong>${escapeHtml(formatMoney(market.price))}</strong><div class="market-meta"><span>24h volume</span><span>${escapeHtml(formatNumber(market.quote_volume, 0))}</span></div><div class="market-meta"><span>Feed</span><span class="feed-tag">${escapeHtml(market.status)}</span></div></article>`;
    }).join('');
  }

  function renderSignals() {
    const resource = resources.signals;
    const payload = resource.value;
    const signals = payload && Array.isArray(payload.signals) ? payload.signals : null;
    const host = byId('signal-list');
    if (!host) return;
    setText('signal-count', signals ? `${signals.length} events returned` : 'Unavailable');
    if (!signals) {
      host.dataset.state = 'error';
      host.innerHTML = stateMarkup('error', resource.error || 'Signal telemetry unavailable.');
      return;
    }
    if (!signals.length) {
      host.dataset.state = 'empty';
      host.innerHTML = stateMarkup('empty', 'No signal events were returned by telemetry.');
      return;
    }
    host.dataset.state = resource.state === 'stale' ? 'stale' : 'ready';
    host.innerHTML = signals.map((signal) => {
      const decision = signal.final_decision || signal.decision || signal.execution_decision || '—';
      const side = signal.side || signal.direction || signal.decision;
      const symbol = signal.symbol || '—';
      return `<article class="signal-row"><span class="signal-time">${escapeHtml(formatUtc(signal.timestamp || signal.signal_timestamp))}</span><span class="symbol-cell">${escapeHtml(symbol)}</span><span class="signal-side">${escapeHtml(side || '—')}</span><span class="signal-strategy">${escapeHtml(signal.strategy || '—')}</span><span class="decision-tag">${escapeHtml(decision)}</span><span class="signal-reason">${escapeHtml(signal.reason || signal.risk_reason || signal.profitability_reason || '—')}</span></article>`;
    }).join('');
  }

  function healthRow(label, value, state = '') {
    const className = state ? ` class="health-value ${escapeHtml(state)}"` : ' class="health-value"';
    return `<div class="health-row"><span>${escapeHtml(label)}</span><strong${className}>${escapeHtml(valueOrUnavailable(value))}</strong></div>`;
  }

  function renderHealth() {
    const engine = resources.engine.value;
    const engineHost = byId('engine-health-rows');
    const engineBadge = byId('health-engine-badge');
    const engineResource = resources.engine;
    if (engineHost) {
      if (!engine) {
        engineHost.innerHTML = stateMarkup(engineResource.state === 'error' ? 'error' : 'loading', engineResource.error || 'Loading engine health');
      } else {
        const strategies = Array.isArray(engine.strategies) ? engine.strategies.join(', ') || 'None reported' : null;
        const symbols = Array.isArray(engine.symbols) ? engine.symbols.join(', ') || 'None reported' : null;
        engineHost.innerHTML = [
          healthRow('Engine status', engine.engine_status, engine.healthy ? 'good' : 'warning'),
          healthRow('Active strategies', strategies),
          healthRow('Market websocket', engine.websocket_connected === true ? 'Connected' : engine.websocket_connected === false ? 'Disconnected' : null, engine.websocket_connected ? 'good' : 'warning'),
          healthRow('Worker process', engine.worker_alive === true ? 'Alive' : engine.worker_alive === false ? 'Not alive' : null),
          healthRow('Paper runner heartbeat', engine.paper_runner_status, engine.paper_runner_status === 'RUNNING' ? 'good' : 'warning'),
          healthRow('Heartbeat age', numeric(engine.heartbeat_age_seconds) === null ? null : `${formatNumber(engine.heartbeat_age_seconds, 1)} seconds`),
          healthRow('Last market update', formatUtc(engine.last_market_update)),
          healthRow('Last candle close', formatUtc(engine.last_candle_close)),
          healthRow('Symbols reported', symbols),
          healthRow('Service started', formatUtc(engine.service_start_time))
        ].join('');
      }
    }
    if (engineBadge) {
      const state = engineResource.state === 'error' ? 'error' : engine && engine.healthy ? 'online' : engine ? 'warning' : 'loading';
      engineBadge.dataset.state = state;
      engineBadge.textContent = engine && engine.engine_status ? engine.engine_status : engineResource.state === 'error' ? 'Unavailable' : 'Loading';
    }
    const paper = resources.paper.value;
    const paperResource = resources.paper;
    const paperHost = byId('paper-health-rows');
    const paperBadge = byId('health-paper-badge');
    if (paperHost) {
      if (!paper) {
        paperHost.innerHTML = stateMarkup(paperResource.state === 'error' ? 'error' : 'loading', paperResource.error || 'Loading paper health');
      } else {
        const health = paper.runner_health || {};
        paperHost.innerHTML = [
          healthRow('Experiment mode', paper.mode),
          healthRow('Runner status', paper.runner_status, paper.runner_status === 'HEALTHY' ? 'good' : 'warning'),
          healthRow('Validation status', paper.validation_status),
          healthRow('Experiment age', numeric(paper.experiment_age_days) === null ? null : `${paper.experiment_age_days} days`),
          healthRow('Signal log', paper.artifact_statuses && paper.artifact_statuses.signals),
          healthRow('Portfolio file', paper.artifact_statuses && paper.artifact_statuses.portfolio),
          healthRow('Trade ledger', paper.artifact_statuses && paper.artifact_statuses.ledger),
          healthRow('Local writes', paper.local_write_status),
          healthRow('Durability', paper.durability_status),
          healthRow('Cloud persistence', paper.cloud_persistence_status),
          healthRow('Market data', health.market_data),
          healthRow('Reconciliation', health.reconciliation)
        ].join('');
      }
    }
    if (paperBadge) {
      const state = paperResource.state === 'error' ? 'error' : paper && ['HEALTHY', 'RUNNING'].includes(paper.runner_status) ? 'online' : paper ? 'warning' : 'loading';
      paperBadge.dataset.state = state;
      paperBadge.textContent = paper && paper.runner_status ? paper.runner_status : paperResource.state === 'error' ? 'Unavailable' : 'Loading';
    }
    setText('diag-heartbeat', engine && numeric(engine.heartbeat_age_seconds) !== null ? `${formatNumber(engine.heartbeat_age_seconds, 1)} seconds` : '—');
    setText('diag-uptime', engine && engine.service_start_time ? formatUtc(engine.service_start_time) : '—');
  }

  function renderSources() {
    const host = byId('source-grid');
    if (!host) return;
    host.innerHTML = Object.entries(resources).map(([key, resource]) => {
      const label = key.replaceAll('_', ' ');
      const detail = resource.state === 'ready' ? 'Updated successfully'
        : resource.state === 'stale' ? `Showing prior response · ${resource.error || 'refresh failed'}`
          : resource.state === 'error' ? resource.error || 'Request failed' : 'Request in progress';
      const state = resource.state === 'ready' ? 'online' : resource.state === 'stale' ? 'stale' : resource.state === 'error' ? 'error' : 'loading';
      return `<div class="source-row"><span class="source-indicator" data-state="${state}"></span><div><strong>${escapeHtml(label)}</strong><small>${escapeHtml(detail)}</small></div><span class="source-state">${escapeHtml(state)}</span></div>`;
    }).join('');
  }

  function switchView(name) {
    if (!Object.prototype.hasOwnProperty.call(viewCopy, name)) return;
    document.querySelectorAll('[data-view]').forEach((button) => {
      button.classList.toggle('active', button.dataset.view === name);
      if (button.tagName === 'BUTTON') button.setAttribute('aria-current', button.dataset.view === name ? 'page' : 'false');
    });
    document.querySelectorAll('[data-view-panel]').forEach((panel) => {
      const visible = panel.dataset.viewPanel === name;
      panel.hidden = !visible;
      panel.classList.toggle('active', visible);
    });
    const [title, description] = viewCopy[name];
    setText('page-title', title);
    setText('page-description', description);
    setText('breadcrumb-view', title);
    try { history.replaceState(null, '', `#${name}`); } catch { /* hash is optional */ }
  }

  document.querySelectorAll('.nav-item[data-view]').forEach((button) => {
    button.addEventListener('click', () => switchView(button.dataset.view));
  });
  document.querySelectorAll('[data-go]').forEach((button) => {
    button.addEventListener('click', () => switchView(button.dataset.go));
  });
  byId('btn-refresh')?.addEventListener('click', refreshData);
  byId('trades-search')?.addEventListener('input', renderTrades);

  const initialView = window.location.hash.replace('#', '');
  switchView(Object.prototype.hasOwnProperty.call(viewCopy, initialView) ? initialView : 'overview');
  refreshData();
  window.setInterval(refreshData, REFRESH_MS);
})();
