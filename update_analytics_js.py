
with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

analytics_js = """
// ==========================================
// ANALYTICS LOGIC
// ==========================================

let activeAnalyticsPeriod = 'ALL';
let analyticsEquityChartInst = null;
let analyticsDrawdownChartInst = null;

function changeAnalyticsPeriod(period, el) {
    activeAnalyticsPeriod = period;
    document.querySelectorAll('.an-period').forEach(e => {
        e.classList.remove('active');
        e.style.color = 'var(--text-muted)';
        e.style.fontWeight = 'normal';
    });
    if (el) {
        el.classList.add('active');
        el.style.color = 'var(--accent-primary)';
        el.style.fontWeight = '700';
    }
    fetchAnalyticsData();
}

async function fetchAnalyticsData() {
    try {
        const hb = await apiClient.get('/api/engine-health');
        const posRes = await apiClient.get('/api/positions');
        const activePositions = (posRes && Array.isArray(posRes.positions)) ? posRes.positions : [];
        
        let startEquity = 12672.13;
        if (hb && hb.equity) startEquity = hb.equity; // Note: simplified. Historically we need start of period equity
        
        let cutoffMs = 0;
        const nowMs = Date.now();
        if (activeAnalyticsPeriod === '1D') cutoffMs = nowMs - (86400000);
        else if (activeAnalyticsPeriod === '7D') cutoffMs = nowMs - (7 * 86400000);
        else if (activeAnalyticsPeriod === '30D') cutoffMs = nowMs - (30 * 86400000);
        
        const periodTrades = globalJournalTrades.filter(t => {
            const time = new Date(t.exit_timestamp || t.timestamp || 0).getTime();
            return time >= cutoffMs;
        });

        // Time sorting ascending for charts
        periodTrades.sort((a, b) => new Date(a.exit_timestamp || a.timestamp || 0).getTime() - new Date(b.exit_timestamp || b.timestamp || 0).getTime());

        let totalTrades = periodTrades.length;
        let wins = 0, losses = 0;
        let grossProf = 0, grossLoss = 0, totalFees = 0;
        let bestTrade = -999999, worstTrade = 999999;
        let totalHoldMs = 0;
        let longestHold = 0, shortestHold = 99999999999;
        
        let stratMap = {};
        let tfMap = {};
        
        let equityCurve = [];
        let drawdownCurve = [];
        let labels = [];
        
        let runningPnl = 0;
        let peakPnl = 0;
        let maxDdPct = 0;

        periodTrades.forEach(t => {
            const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
            const fees = Number(t.fees || 0);
            const gross = net + fees;
            
            totalFees += fees;
            
            if (net >= 0) {
                wins++;
                grossProf += net;
                if (net > bestTrade) bestTrade = net;
            } else {
                losses++;
                grossLoss += Math.abs(net);
                if (net < worstTrade) worstTrade = net;
            }
            
            const openMs = new Date(t.entry_timestamp || t.timestamp || 0).getTime();
            const closeMs = new Date(t.exit_timestamp || t.timestamp || 0).getTime();
            const hold = closeMs - openMs;
            if (hold > 0) {
                totalHoldMs += hold;
                if (hold > longestHold) longestHold = hold;
                if (hold < shortestHold) shortestHold = hold;
            }

            // Strategy perf
            const s = (t.strategy || 'UNKNOWN').toUpperCase();
            if (!stratMap[s]) stratMap[s] = { pnl: 0, wins: 0, trades: 0 };
            stratMap[s].trades++;
            stratMap[s].pnl += net;
            if (net >= 0) stratMap[s].wins++;
            
            // TF perf
            const tf = (t.timeframe || 'UNKNOWN');
            if (!tfMap[tf]) tfMap[tf] = { pnl: 0, wins: 0, trades: 0 };
            tfMap[tf].trades++;
            tfMap[tf].pnl += net;
            if (net >= 0) tfMap[tf].wins++;
            
            // Curves
            runningPnl += net;
            if (runningPnl > peakPnl) peakPnl = runningPnl;
            
            const currentEquity = startEquity + runningPnl;
            const peakEquity = startEquity + peakPnl;
            
            let ddPct = 0;
            if (peakEquity > 0) {
                ddPct = ((peakEquity - currentEquity) / peakEquity) * 100;
            }
            if (ddPct > maxDdPct) maxDdPct = ddPct;
            
            labels.push(new Date(closeMs).toLocaleDateString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}));
            equityCurve.push(runningPnl);
            drawdownCurve.push(-ddPct);
        });

        // Unrealized calculation
        let unrealized = 0;
        activePositions.forEach(p => {
            unrealized += Number(p.unrealizedProfit || p.pnl || 0);
        });
        
        const netPnl = grossProf - grossLoss + unrealized;
        const realizedPnl = grossProf - grossLoss;
        const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
        const profitFactor = grossLoss > 0 ? (grossProf / grossLoss) : (grossProf > 0 ? 999 : 0);
        
        const avgWin = wins > 0 ? grossProf / wins : 0;
        const avgLoss = losses > 0 ? grossLoss / losses : 0;
        const avgTrade = totalTrades > 0 ? realizedPnl / totalTrades : 0;
        
        if (bestTrade === -999999) bestTrade = 0;
        if (worstTrade === 999999) worstTrade = 0;
        if (shortestHold === 99999999999) shortestHold = 0;
        
        const avgHoldStr = totalTrades > 0 ? calcDurationBetween(Date.now(), Date.now() + (totalHoldMs / totalTrades)) : '0m';
        const longHoldStr = longestHold > 0 ? calcDurationBetween(Date.now(), Date.now() + longestHold) : '0m';
        const shortHoldStr = shortestHold > 0 ? calcDurationBetween(Date.now(), Date.now() + shortestHold) : '0m';
        
        // Update KPIs
        const elNet = document.getElementById('an-net-pnl');
        elNet.innerText = (netPnl >= 0 ? '+' : '') + '$' + formatCurrency(Math.abs(netPnl));
        elNet.className = 'mono ' + (netPnl >= 0 ? 'profit' : 'loss');
        
        document.getElementById('an-total-trades').innerText = totalTrades;
        document.getElementById('an-win-rate').innerText = winRate.toFixed(1) + '%';
        document.getElementById('an-profit-factor').innerText = profitFactor.toFixed(2);
        document.getElementById('an-max-dd').innerText = '-' + maxDdPct.toFixed(2) + '%';
        
        // Breakdown
        document.getElementById('an-realized').innerText = (realizedPnl >= 0 ? '+' : '') + '$' + formatCurrency(Math.abs(realizedPnl));
        document.getElementById('an-unrealized').innerText = (unrealized >= 0 ? '+' : '') + '$' + formatCurrency(Math.abs(unrealized));
        document.getElementById('an-unrealized').className = 'mono ' + (unrealized >= 0 ? 'profit' : 'loss');
        document.getElementById('an-fees').innerText = '-$' + formatCurrency(Math.abs(totalFees));
        
        const elNet2 = document.getElementById('an-net-pnl2');
        elNet2.innerText = (netPnl >= 0 ? '+' : '') + '$' + formatCurrency(Math.abs(netPnl));
        elNet2.className = 'mono td-strong ' + (netPnl >= 0 ? 'profit' : 'loss');
        
        // Strat & TF Tables
        let stratHtml = '';
        const stratKeys = Object.keys(stratMap).sort((a,b) => stratMap[b].pnl - stratMap[a].pnl);
        stratKeys.forEach(k => {
            const v = stratMap[k];
            const w = v.trades > 0 ? (v.wins / v.trades)*100 : 0;
            stratHtml += `<tr>
                <td class="td-strong">${k}</td>
                <td class="mono ${v.pnl >= 0 ? 'profit' : 'loss'}">${(v.pnl >= 0 ? '+' : '') + '$' + formatCurrency(Math.abs(v.pnl))}</td>
                <td class="mono ${w >= 50 ? 'profit' : 'loss'}">${w.toFixed(1)}%</td>
            </tr>`;
        });
        if (stratKeys.length === 0) stratHtml = '<tr><td colspan="3" class="text-center text-muted" style="padding: 24px;">No strategy data</td></tr>';
        document.getElementById('an-strat-body').innerHTML = stratHtml;
        
        let tfHtml = '';
        const tfKeys = Object.keys(tfMap).sort((a,b) => tfMap[b].pnl - tfMap[a].pnl);
        tfKeys.forEach(k => {
            const v = tfMap[k];
            const w = v.trades > 0 ? (v.wins / v.trades)*100 : 0;
            tfHtml += `<tr>
                <td class="mono text-secondary">${k}</td>
                <td class="mono ${v.pnl >= 0 ? 'profit' : 'loss'}">${(v.pnl >= 0 ? '+' : '') + '$' + formatCurrency(Math.abs(v.pnl))}</td>
                <td class="mono ${w >= 50 ? 'profit' : 'loss'}">${w.toFixed(1)}%</td>
            </tr>`;
        });
        if (tfKeys.length === 0) tfHtml = '<tr><td colspan="3" class="text-center text-muted" style="padding: 24px;">No timeframe data</td></tr>';
        document.getElementById('an-tf-body').innerHTML = tfHtml;
        
        // Trade Perf
        document.getElementById('an-tp').innerText = '+$' + formatCurrency(grossProf);
        document.getElementById('an-tl').innerText = '-$' + formatCurrency(grossLoss);
        document.getElementById('an-aw').innerText = '+$' + formatCurrency(avgWin);
        document.getElementById('an-al').innerText = '-$' + formatCurrency(avgLoss);
        document.getElementById('an-at').innerText = (avgTrade >= 0 ? '+' : '') + '$' + formatCurrency(Math.abs(avgTrade));
        document.getElementById('an-at').className = 'mono ' + (avgTrade >= 0 ? 'profit' : 'loss');
        
        document.getElementById('an-best').innerText = '+$' + formatCurrency(bestTrade);
        document.getElementById('an-worst').innerText = '-$' + formatCurrency(worstTrade);
        document.getElementById('an-long').innerText = longHoldStr;
        document.getElementById('an-short').innerText = shortHoldStr;

        // Render Charts
        renderAnalyticsCharts(labels, equityCurve, drawdownCurve);

    } catch (e) {
        console.error("fetchAnalyticsData error:", e);
    }
}

function renderAnalyticsCharts(labels, eqData, ddData) {
    const ctxEq = document.getElementById('analytics-equity-chart');
    if (analyticsEquityChartInst) analyticsEquityChartInst.destroy();
    
    if (ctxEq) {
        let isProf = eqData.length > 0 && eqData[eqData.length-1] >= 0;
        let bColor = isProf ? '#10B981' : '#EF4444';
        let bgColor = isProf ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';
        
        analyticsEquityChartInst = new Chart(ctxEq.getContext('2d'), {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Cumulative Net PnL',
                    data: eqData,
                    borderColor: bColor,
                    backgroundColor: bgColor,
                    borderWidth: 2,
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A', maxTicksLimit: 10 } },
                    y: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } }
                }
            }
        });
    }

    const ctxDd = document.getElementById('analytics-drawdown-chart');
    if (analyticsDrawdownChartInst) analyticsDrawdownChartInst.destroy();
    
    if (ctxDd) {
        analyticsDrawdownChartInst = new Chart(ctxDd.getContext('2d'), {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Drawdown %',
                    data: ddData,
                    borderColor: '#EF4444',
                    backgroundColor: 'rgba(239, 68, 68, 0.2)',
                    borderWidth: 1,
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: { legend: { display: false } },
                scales: {
                    x: { display: false },
                    y: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } }
                }
            }
        });
    }
}
"""

if 'fetchAnalyticsData' not in content:
    content += analytics_js

content = content.replace('fetchDashboardData(), fetchDashboardDataV2(), fetchScannerDataV2(), fetchPositionsV2(), fetchMarketData(), fetchStrategiesV2(), fetchRiskData(),', 'fetchDashboardData(), fetchDashboardDataV2(), fetchScannerDataV2(), fetchPositionsV2(), fetchMarketData(), fetchStrategiesV2(), fetchRiskData(), fetchAnalyticsData(),')

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Analytics JS updated.")
