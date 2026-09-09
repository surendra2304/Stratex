/**
 * STRATEX — Real-Time Binance Testnet Terminal Controller
 * Clean, Instant Real-Time Data Sync without fluff or synthetic indicators.
 */

(function () {
    'use strict';

    // State
    let isFetching = false;
    let pollTimer = null;
    let lastTradeFilter = '';
    let expandedDays = new Set(); // Store expanded date strings

    // DOM Elements
    const el = {
        lastSync: document.getElementById('last-sync-time'),
        btnRefresh: document.getElementById('btn-refresh'),
        kpiBalance: document.getElementById('kpi-balance'),
        kpiEquitySub: document.getElementById('kpi-equity-sub'),
        kpiTotalPnl: document.getElementById('kpi-total-pnl'),
        kpiPnlSub: document.getElementById('kpi-pnl-sub'),
        kpiCardTotalPnl: document.getElementById('kpi-card-total-pnl'),
        kpiTodayPnl: document.getElementById('kpi-today-pnl'),
        kpiTodaySub: document.getElementById('kpi-today-sub'),
        kpiCardTodayPnl: document.getElementById('kpi-card-today-pnl'),
        kpiWinRate: document.getElementById('kpi-win-rate'),
        kpiTradesCount: document.getElementById('kpi-trades-count'),
        kpiOpenCount: document.getElementById('kpi-open-count'),
        kpiOpenUnrealized: document.getElementById('kpi-open-unrealized'),
        
        dailyPnlTbody: document.getElementById('daily-pnl-tbody'),
        dailyDaysCount: document.getElementById('daily-days-count'),
        
        openPositionsTbody: document.getElementById('open-positions-tbody'),
        openPositionsCount: document.getElementById('open-positions-count'),
        
        tradesHistoryTbody: document.getElementById('trades-history-tbody'),
        totalTradesBadge: document.getElementById('total-trades-badge'),
        tradesSearch: document.getElementById('trades-search'),
        
        marketScannerGrid: document.getElementById('market-scanner-grid'),
        scannedSymbolsCount: document.getElementById('scanned-symbols-count'),
        
        diagEngineStatus: document.getElementById('diag-engine-status'),
        diagHeartbeat: document.getElementById('diag-heartbeat'),
        diagStrategies: document.getElementById('diag-strategies'),
        diagPid: document.getElementById('diag-pid'),
        diagUptime: document.getElementById('diag-uptime'),
    };

    // Number Formatting Helpers
    function fmtMoney(val, decimals = 2) {
        const num = parseFloat(val) || 0.0;
        return '$' + num.toLocaleString('en-US', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    }

    function fmtSignedMoney(val, decimals = 2) {
        const num = parseFloat(val) || 0.0;
        const sign = num > 0 ? '+' : num < 0 ? '-' : '';
        return sign + '$' + Math.abs(num).toLocaleString('en-US', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    }

    function fmtNumber(val, decimals = 4) {
        const num = parseFloat(val) || 0.0;
        return num.toLocaleString('en-US', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    }

    function fmtPct(val) {
        const num = parseFloat(val) || 0.0;
        return num.toFixed(1) + '%';
    }

    function fmtTime(isoStr) {
        if (!isoStr) return '--';
        try {
            const d = new Date(isoStr);
            if (isNaN(d.getTime())) return isoStr;
            return d.toISOString().replace('T', ' ').substring(0, 19);
        } catch (e) {
            return isoStr;
        }
    }

    // Main Data Fetcher
    async function fetchAllData() {
        if (isFetching) return;
        isFetching = true;
        if (el.btnRefresh) el.btnRefresh.classList.add('syncing');

        try {
            const [resStatus, resTrades, resDaily, resMarkets] = await Promise.allSettled([
                fetch('/api/status', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
                fetch('/api/trades', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
                fetch('/api/daily-pnl', { cache: 'no-store' }).then(r => r.ok ? r.json() : null),
                fetch('/api/markets', { cache: 'no-store' }).then(r => r.ok ? r.json() : null)
            ]);

            const status = resStatus.value || {};
            const trades = resTrades.value || {};
            const daily = resDaily.value || {};
            const markets = resMarkets.value || {};

            renderKPIs(status, trades);
            renderDailyPnL(daily, trades);
            renderOpenPositions(status);
            renderTradesHistory(trades);
            renderMarkets(markets, status);
            renderDiagnostics(status);

            const now = new Date();
            if (el.lastSync) {
                el.lastSync.textContent = now.toLocaleTimeString();
            }
        } catch (err) {
            console.error('[STRATEX] Sync error:', err);
            if (el.lastSync) el.lastSync.textContent = 'Sync error';
        } finally {
            isFetching = false;
            if (el.btnRefresh) el.btnRefresh.classList.remove('syncing');
        }
    }

    // Render KPIs
    function renderKPIs(status, trades) {
        const cash = parseFloat(status.cash || 0.0);
        const equity = parseFloat(status.equity || cash);
        const realizedPnl = parseFloat(trades.net_pnl !== undefined ? trades.net_pnl : (status.realized_pnl || 0.0));
        const todayPnl = parseFloat(status.today_pnl !== undefined ? status.today_pnl : (status.today_realized_pnl || 0.0));
        const unrealizedPnl = parseFloat(status.unrealized_pnl || 0.0);
        const winRate = parseFloat(trades.win_rate || 0.0);
        const wins = parseInt(trades.wins || 0, 10);
        const losses = parseInt(trades.losses || 0, 10);
        const totalTrades = parseInt(trades.total_trades || (wins + losses), 10);
        const grossProfit = parseFloat(trades.gross_profit || 0.0);
        const grossLoss = parseFloat(trades.gross_loss || 0.0);
        const fees = parseFloat(status.fees || 0.0);

        // Balance & Equity
        if (el.kpiBalance) el.kpiBalance.textContent = fmtMoney(cash, 2);
        if (el.kpiEquitySub) el.kpiEquitySub.textContent = `Total Equity: ${fmtMoney(equity, 2)} USDT`;

        // Total Realized PnL
        if (el.kpiTotalPnl) {
            el.kpiTotalPnl.textContent = fmtSignedMoney(realizedPnl, 2);
            el.kpiTotalPnl.className = 'kpi-value ' + (realizedPnl >= 0 ? 'text-green' : 'text-red');
        }
        if (el.kpiCardTotalPnl) {
            el.kpiCardTotalPnl.className = 'kpi-card ' + (realizedPnl >= 0 ? 'highlight-green' : 'highlight-red');
        }
        if (el.kpiPnlSub) {
            el.kpiPnlSub.textContent = `Gross: +${fmtMoney(grossProfit, 2)} | Loss: -${fmtMoney(grossLoss, 2)} | Fees: ${fmtMoney(fees, 2)}`;
        }

        // Today's PnL
        if (el.kpiTodayPnl) {
            el.kpiTodayPnl.textContent = fmtSignedMoney(todayPnl, 2);
            el.kpiTodayPnl.className = 'kpi-value ' + (todayPnl >= 0 ? 'text-green' : 'text-red');
        }
        if (el.kpiCardTodayPnl) {
            el.kpiCardTodayPnl.className = 'kpi-card ' + (todayPnl >= 0 ? 'highlight-green' : 'highlight-red');
        }
        if (el.kpiTodaySub) {
            const todayRealized = parseFloat(status.today_realized_pnl || 0.0);
            el.kpiTodaySub.textContent = `Realized: ${fmtSignedMoney(todayRealized, 2)} | Unrealized: ${fmtSignedMoney(unrealizedPnl, 2)}`;
        }

        // Win Rate
        if (el.kpiWinRate) {
            el.kpiWinRate.textContent = fmtPct(winRate);
            el.kpiWinRate.className = 'kpi-value ' + (winRate >= 50 ? 'text-green' : winRate > 0 ? 'text-amber' : 'text-main');
        }
        if (el.kpiTradesCount) {
            el.kpiTradesCount.textContent = `${wins} Wins / ${losses} Losses (${totalTrades} Closed)`;
        }

        // Open Positions Count
        const openList = status.open_positions_data || [];
        const openCount = status.open_positions !== undefined ? status.open_positions : openList.length;
        if (el.kpiOpenCount) {
            el.kpiOpenCount.textContent = openCount;
            el.kpiOpenCount.className = 'kpi-value ' + (openCount > 0 ? 'text-blue' : 'text-main');
        }
        if (el.kpiOpenUnrealized) {
            el.kpiOpenUnrealized.textContent = `Unrealized: ${fmtSignedMoney(unrealizedPnl, 2)}`;
        }
    }

    // Render Daily PnL Breakdown (Profits and Losses of Every Day)
    function renderDailyPnL(dailyData, tradesData) {
        if (!el.dailyPnlTbody) return;

        let days = (dailyData && Array.isArray(dailyData.days) && dailyData.days.length > 0)
            ? dailyData.days
            : null;

        // Fallback: group from tradesData.positions if backend daily endpoint hasn't updated yet
        if (!days) {
            const positions = (tradesData && Array.isArray(tradesData.positions)) ? tradesData.positions : [];
            const grouped = {};
            positions.forEach(p => {
                const ts = p.timestamp || '';
                const dateKey = ts.length >= 10 ? ts.substring(0, 10) : 'Unknown';
                if (!grouped[dateKey]) {
                    grouped[dateKey] = {
                        date: dateKey,
                        net_pnl: 0.0,
                        gross_profit: 0.0,
                        gross_loss: 0.0,
                        fees: 0.0,
                        trades_count: 0,
                        wins: 0,
                        losses: 0,
                        trades: []
                    };
                }
                const pnl = parseFloat(p.pnl || p.net_pnl || 0.0);
                const fees = parseFloat(p.fees || 0.0);
                grouped[dateKey].net_pnl += pnl;
                grouped[dateKey].fees += fees;
                grouped[dateKey].trades_count += 1;
                if (pnl > 0) {
                    grouped[dateKey].wins += 1;
                    grouped[dateKey].gross_profit += pnl;
                } else {
                    grouped[dateKey].losses += 1;
                    grouped[dateKey].gross_loss += Math.abs(pnl);
                }
                grouped[dateKey].trades.push(p);
            });

            days = Object.keys(grouped).sort().reverse().map(k => {
                const d = grouped[k];
                d.win_rate = d.trades_count > 0 ? ((d.wins / d.trades_count) * 100) : 0.0;
                return d;
            });
        }

        if (el.dailyDaysCount) {
            el.dailyDaysCount.textContent = `${days.length} Days Recorded`;
        }

        if (days.length === 0) {
            el.dailyPnlTbody.innerHTML = `
                <tr>
                    <td colspan="8" class="empty-state">
                        <div class="empty-state-icon">📊</div>
                        <div class="empty-state-text">No daily closed trades yet</div>
                        <div class="empty-state-sub">Every day's realized profit and loss ledger will automatically compile here.</div>
                    </td>
                </tr>
            `;
            return;
        }

        const todayStr = new Date().toISOString().substring(0, 10);
        let html = '';

        days.forEach(d => {
            const isToday = d.date === todayStr;
            const netPnl = parseFloat(d.net_pnl || 0.0);
            const pnlClass = netPnl > 0 ? 'text-green' : netPnl < 0 ? 'text-red' : 'text-muted';
            const winRate = parseFloat(d.win_rate || 0.0);
            const isExpanded = expandedDays.has(d.date);

            html += `
                <tr class="day-row ${isExpanded ? 'expanded' : ''}" data-day="${d.date}">
                    <td class="mono" style="font-weight: 700;">
                        <span class="expand-icon">▶</span>
                        ${d.date} ${isToday ? '<span class="badge badge-tag" style="color: var(--blue);">TODAY</span>' : ''}
                    </td>
                    <td class="mono ${pnlClass}" style="font-weight: 700; font-size: 14px;">
                        ${fmtSignedMoney(netPnl, 4)}
                    </td>
                    <td class="mono">
                        <span class="badge ${winRate >= 50 ? 'badge-win' : winRate > 0 ? 'badge-tag' : 'badge-loss'}">
                            ${fmtPct(winRate)}
                        </span>
                    </td>
                    <td class="mono">
                        <span class="text-green">${d.wins || 0}W</span> / 
                        <span class="text-red">${d.losses || 0}L</span> 
                        <span class="text-muted">(${d.trades_count || 0})</span>
                    </td>
                    <td class="mono text-green">+${fmtMoney(d.gross_profit || 0.0, 4)}</td>
                    <td class="mono text-red">-${fmtMoney(d.gross_loss || 0.0, 4)}</td>
                    <td class="mono text-muted">${fmtMoney(d.fees || 0.0, 4)}</td>
                    <td>
                        <button class="btn-sync" style="padding: 3px 8px; font-size: 11px;" onclick="window.toggleDayExpand('${d.date}', event)">
                            ${isExpanded ? 'Hide' : 'View'} (${d.trades ? d.trades.length : 0})
                        </button>
                    </td>
                </tr>
            `;

            // Nested trades accordion for this day
            if (isExpanded && d.trades && d.trades.length > 0) {
                html += `
                    <tr>
                        <td colspan="8" style="padding: 0; background: #0c0f17;">
                            <div class="day-trades-wrapper">
                                <div class="day-trades-inner-title">
                                    <span>Individual Executions for ${d.date} (${d.trades.length} Trades)</span>
                                </div>
                                <table class="data-table" style="font-size: 12px;">
                                    <thead>
                                        <tr>
                                            <th>Time (UTC)</th>
                                            <th>Symbol</th>
                                            <th>Side</th>
                                            <th>Quantity</th>
                                            <th>Entry Price</th>
                                            <th>Exit Price</th>
                                            <th>Net PnL</th>
                                            <th>Fees</th>
                                            <th>Strategy</th>
                                            <th>Order ID</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${d.trades.map(t => {
                                            const tPnl = parseFloat(t.pnl || t.net_pnl || 0.0);
                                            const side = (t.action || t.direction || 'BUY').toUpperCase();
                                            return `
                                                <tr>
                                                    <td class="mono text-muted">${fmtTime(t.timestamp || t.exit_timestamp)}</td>
                                                    <td class="mono" style="font-weight: 700;">${t.symbol}</td>
                                                    <td>
                                                        <span class="badge ${side.includes('BUY') || side.includes('LONG') ? 'badge-buy' : 'badge-sell'}">
                                                            ${side}
                                                        </span>
                                                    </td>
                                                    <td class="mono">${t.quantity || '--'}</td>
                                                    <td class="mono">${fmtNumber(t.entry_price, 4)}</td>
                                                    <td class="mono">${fmtNumber(t.exit_price, 4)}</td>
                                                    <td class="mono ${tPnl >= 0 ? 'text-green' : 'text-red'}" style="font-weight: 700;">
                                                        ${fmtSignedMoney(tPnl, 4)}
                                                    </td>
                                                    <td class="mono text-muted">${fmtMoney(t.fees || 0.0, 4)}</td>
                                                    <td><span class="badge-tag">${t.strategy || 'supertrend'}</span></td>
                                                    <td class="mono text-muted" style="font-size: 10px;">${t.order_id || '--'}</td>
                                                </tr>
                                            `;
                                        }).join('')}
                                    </tbody>
                                </table>
                            </div>
                        </td>
                    </tr>
                `;
            }
        });

        el.dailyPnlTbody.innerHTML = html;

        // Attach click listeners to day rows for expanding
        el.dailyPnlTbody.querySelectorAll('.day-row').forEach(row => {
            row.addEventListener('click', (e) => {
                const dateKey = row.getAttribute('data-day');
                window.toggleDayExpand(dateKey, e);
            });
        });
    }

    // Toggle day expand
    window.toggleDayExpand = function (dateKey, e) {
        if (e) e.stopPropagation();
        if (expandedDays.has(dateKey)) {
            expandedDays.delete(dateKey);
        } else {
            expandedDays.add(dateKey);
        }
        fetchAllData();
    };

    // Render Open Positions
    function renderOpenPositions(status) {
        if (!el.openPositionsTbody) return;

        const positions = status.open_positions_data || [];
        if (el.openPositionsCount) {
            el.openPositionsCount.textContent = `${positions.length} Open`;
        }

        if (positions.length === 0) {
            el.openPositionsTbody.innerHTML = `
                <tr>
                    <td colspan="10" class="empty-state">
                        <div class="empty-state-icon">⚡</div>
                        <div class="empty-state-text">No active open positions on Binance Testnet</div>
                        <div class="empty-state-sub">Strategy engine is actively scanning 16 pairs for qualified high-expectancy entries.</div>
                    </td>
                </tr>
            `;
            return;
        }

        el.openPositionsTbody.innerHTML = positions.map(pos => {
            const side = (pos.side || pos.direction || 'BUY').toUpperCase();
            const uPnl = parseFloat(pos.unrealized_pnl || 0.0);
            const entryPrice = parseFloat(pos.entry_price || 0.0);
            const currentPrice = parseFloat(pos.current_price || entryPrice);
            const pnlPct = entryPrice > 0 ? ((currentPrice - entryPrice) / entryPrice * 100 * (side.includes('BUY') || side.includes('LONG') ? 1 : -1)) : 0.0;
            const pnlClass = uPnl >= 0 ? 'text-green' : 'text-red';

            return `
                <tr>
                    <td class="mono" style="font-weight: 700; font-size: 14px; color: #fff;">${pos.symbol}</td>
                    <td>
                        <span class="badge ${side.includes('BUY') || side.includes('LONG') ? 'badge-buy' : 'badge-sell'}">
                            ${side}
                        </span>
                    </td>
                    <td class="mono">${pos.quantity || '--'}</td>
                    <td class="mono">${fmtNumber(entryPrice, 4)}</td>
                    <td class="mono">${fmtNumber(currentPrice, 4)}</td>
                    <td class="mono ${pnlClass}" style="font-weight: 700;">
                        ${fmtSignedMoney(uPnl, 2)} (${fmtPct(pnlPct)})
                    </td>
                    <td class="mono text-red">${pos.sl ? fmtNumber(pos.sl, 4) : '--'}</td>
                    <td class="mono text-green">${pos.tp ? fmtNumber(pos.tp, 4) : '--'}</td>
                    <td><span class="badge-tag">${pos.strategy || 'supertrend'}</span></td>
                    <td class="mono text-muted">${fmtTime(pos.timestamp)}</td>
                </tr>
            `;
        }).join('');
    }

    // Render Trade History
    function renderTradesHistory(tradesData) {
        if (!el.tradesHistoryTbody) return;

        let positions = tradesData && Array.isArray(tradesData.positions) ? tradesData.positions : [];
        if (el.totalTradesBadge) {
            el.totalTradesBadge.textContent = `${positions.length} Closed`;
        }

        const filter = (lastTradeFilter || '').toUpperCase().trim();
        if (filter) {
            positions = positions.filter(p => (p.symbol || '').toUpperCase().includes(filter));
        }

        if (positions.length === 0) {
            el.tradesHistoryTbody.innerHTML = `
                <tr>
                    <td colspan="12" class="empty-state">
                        <div class="empty-state-icon">📜</div>
                        <div class="empty-state-text">No closed trades matching filter</div>
                    </td>
                </tr>
            `;
            return;
        }

        el.tradesHistoryTbody.innerHTML = positions.map(t => {
            const pnl = parseFloat(t.pnl || t.net_pnl || 0.0);
            const side = (t.action || t.direction || 'BUY').toUpperCase();
            const pnlClass = pnl > 0 ? 'text-green' : pnl < 0 ? 'text-red' : 'text-muted';

            return `
                <tr>
                    <td class="mono text-muted">${fmtTime(t.timestamp || t.exit_timestamp)}</td>
                    <td class="mono" style="font-weight: 700; color: #fff;">${t.symbol}</td>
                    <td>
                        <span class="badge ${side.includes('BUY') || side.includes('LONG') ? 'badge-buy' : 'badge-sell'}">
                            ${side}
                        </span>
                    </td>
                    <td class="mono">${t.quantity || '--'}</td>
                    <td class="mono">${fmtNumber(t.entry_price, 4)}</td>
                    <td class="mono">${fmtNumber(t.exit_price, 4)}</td>
                    <td class="mono text-muted">${fmtSignedMoney(t.gross_pnl || pnl, 4)}</td>
                    <td class="mono text-muted">${fmtMoney(t.fees || 0.0, 4)}</td>
                    <td class="mono ${pnlClass}" style="font-weight: 700; font-size: 13px;">
                        ${fmtSignedMoney(pnl, 4)}
                    </td>
                    <td><span class="badge-tag">${t.exit_reason || 'TARGET'}</span></td>
                    <td><span class="badge-tag">${t.strategy || 'supertrend'}</span></td>
                    <td class="mono text-muted" style="font-size: 10px;">${t.order_id || '--'}</td>
                </tr>
            `;
        }).join('');
    }

    // Render Market Scanner Grid
    function renderMarkets(marketsData, status) {
        if (!el.marketScannerGrid) return;

        const rawList = (marketsData && Array.isArray(marketsData.markets)) ? marketsData.markets : [];
        const tracked = [
            'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'LTCUSDT',
            'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT', 'ATOMUSDT', 'UNIUSDT', 'NEARUSDT',
            'APTUSDT', 'ADAUSDT', 'DOTUSDT', 'INJUSDT'
        ];

        if (el.scannedSymbolsCount) {
            el.scannedSymbolsCount.textContent = `${tracked.length} Pairs Active`;
        }

        const map = {};
        rawList.forEach(m => { map[m.symbol] = m; });

        el.marketScannerGrid.innerHTML = tracked.map(sym => {
            const m = map[sym] || {};
            const price = parseFloat(m.price || 0.0);
            const change = parseFloat(m.change_24h || 0.0);
            const changeClass = change >= 0 ? 'text-green' : 'text-red';
            const changeSign = change >= 0 ? '+' : '';

            return `
                <div class="market-chip">
                    <div class="market-chip-header">
                        <span class="market-chip-symbol">${sym}</span>
                        <span class="market-chip-change ${changeClass}">${changeSign}${change.toFixed(2)}%</span>
                    </div>
                    <div class="market-chip-price">${price > 0 ? fmtNumber(price, price > 10 ? 2 : 4) : '--'}</div>
                    <div class="market-chip-vol">Vol: ${m.volume ? parseFloat(m.volume).toFixed(0) : '--'}</div>
                </div>
            `;
        }).join('');
    }

    // Render Diagnostics
    function renderDiagnostics(status) {
        const engineData = status.engine_data || {};
        if (el.diagEngineStatus) {
            const isOnline = status.engine_status === 'ONLINE' || engineData.engine_status === 'ONLINE';
            el.diagEngineStatus.textContent = isOnline ? 'ONLINE' : (status.engine_status || 'OFFLINE');
            el.diagEngineStatus.className = 'panel-title-badge ' + (isOnline ? 'text-green' : 'text-red');
        }
        if (el.diagHeartbeat) {
            const age = engineData.heartbeat_age_seconds !== undefined ? parseFloat(engineData.heartbeat_age_seconds).toFixed(1) + 's ago' : 'OK';
            el.diagHeartbeat.textContent = `Heartbeat: ${age}`;
        }
        if (el.diagStrategies && Array.isArray(engineData.strategies)) {
            el.diagStrategies.textContent = engineData.strategies.join(', ');
        }
        if (el.diagPid && engineData.pid) {
            el.diagPid.textContent = `${engineData.pid} (Daemon)`;
        }
    }

    // Event Listeners
    if (el.btnRefresh) {
        el.btnRefresh.addEventListener('click', () => {
            fetchAllData();
        });
    }

    if (el.tradesSearch) {
        el.tradesSearch.addEventListener('input', (e) => {
            lastTradeFilter = e.target.value;
            fetch('/api/trades', { cache: 'no-store' })
                .then(r => r.json())
                .then(trades => renderTradesHistory(trades))
                .catch(() => {});
        });
    }

    // Initialize & Start Real-Time 2-Second Polling
    fetchAllData();
    pollTimer = setInterval(fetchAllData, 2000);

})();
