import sys

with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

dashboard_v2_code = """
async function fetchDashboardDataV2() {
    try {
        const [statusData, positionsData, tradesData, scannerData] = await Promise.all([
            apiClient.get('/api/status'),
            apiClient.get('/api/positions'),
            apiClient.get('/api/trades'),
            apiClient.get('/api/scanner')
        ]);

        if (statusData) {
            const equity = Number(statusData.equity || 0);
            const cash = Number(statusData.cash !== undefined ? statusData.cash : equity);
            const managed = Number(statusData.crypto_holdings_value || 0);
            
            document.getElementById('db2-total-account').innerText = formatCurrency(equity);
            document.getElementById('db2-cash').innerText = formatCurrency(cash);
            document.getElementById('db2-managed').innerText = formatCurrency(managed);
            
            // Header overrides
            if (document.getElementById('hdr-uptime')) {
                document.getElementById('hdr-uptime').innerText = statusData.uptime || '00:00:00';
            }
        }

        let realizedProfit = 0, realizedLoss = 0, realizedWins = 0, realizedLosses = 0;
        let todayProfit = 0, todayLoss = 0, todayWins = 0, todayLosses = 0;
        const now = new Date();
        const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();

        if (tradesData && Array.isArray(tradesData)) {
            tradesData.forEach(t => {
                const net = Number(t.net_pnl !== undefined ? t.net_pnl : (t.pnl || 0));
                const fees = Number(t.fees || 0);
                const isClosed = t.status === 'CLOSED' || (t.exit_price && Number(t.exit_price) > 0);
                const exitTime = new Date(t.exit_timestamp || t.timestamp).getTime();
                
                if (isClosed) {
                    if (net >= 0) { realizedProfit += net; realizedWins++; }
                    else { realizedLoss += Math.abs(net); realizedLosses++; }
                    
                    if (exitTime >= startOfDay) {
                        if (net >= 0) { todayProfit += net; todayWins++; }
                        else { todayLoss += Math.abs(net); todayLosses++; }
                    }
                }
            });
        }

        const realizedNet = realizedProfit - realizedLoss;
        const todayNet = todayProfit - todayLoss;

        document.getElementById('db2-realized-net').innerText = (realizedNet >= 0 ? '+' : '') + formatCurrency(realizedNet);
        document.getElementById('db2-realized-net').className = 'kpi-val mono ' + (realizedNet >= 0 ? 'profit' : 'loss');
        document.getElementById('db2-realized-trades').innerText = (realizedWins + realizedLosses);
        document.getElementById('db2-realized-wins').innerText = realizedWins;
        document.getElementById('db2-realized-losses').innerText = realizedLosses;
        document.getElementById('db2-realized-profit').innerText = '+' + formatCurrency(realizedProfit);
        document.getElementById('db2-realized-loss').innerText = '-' + formatCurrency(realizedLoss);

        document.getElementById('db2-today-net').innerText = (todayNet >= 0 ? '+' : '') + formatCurrency(todayNet);
        document.getElementById('db2-today-net').className = 'kpi-val mono ' + (todayNet >= 0 ? 'profit' : 'loss');
        document.getElementById('db2-today-trades').innerText = (todayWins + todayLosses);
        document.getElementById('db2-today-wins').innerText = todayWins;
        document.getElementById('db2-today-losses').innerText = todayLosses;
        document.getElementById('db2-today-profit').innerText = '+' + formatCurrency(todayProfit);
        document.getElementById('db2-today-loss').innerText = '-' + formatCurrency(todayLoss);

        let unRealizedFloating = 0, unRealizedWins = 0, unRealizedLosses = 0;
        let openPosHtml = '';
        if (positionsData && Array.isArray(positionsData) && positionsData.length > 0) {
            positionsData.forEach(p => {
                const upnl = Number(p.unrealized_pnl || 0);
                unRealizedFloating += upnl;
                if (upnl >= 0) unRealizedWins++; else unRealizedLosses++;

                const sym = p.symbol || '-';
                const tf = p.timeframe || '-';
                const side = p.side || 'LONG';
                const entry = Number(p.entry_price || 0);
                const mark = Number(p.mark_price || p.current_price || entry);
                const uStr = (upnl >= 0 ? '+' : '') + formatCurrency(upnl);
                const uClass = upnl >= 0 ? 'profit' : 'loss';

                openPosHtml += `
                    <tr onclick="showView('positions')" style="cursor:pointer">
                        <td class="td-strong">${sym}</td>
                        <td class="mono">${tf}</td>
                        <td class="${side === 'LONG' || side === 'BUY' ? 'profit' : 'loss'}">${side}</td>
                        <td class="mono">${entry.toFixed(4)}</td>
                        <td class="mono">${mark.toFixed(4)}</td>
                        <td class="mono ${uClass}">${uStr}</td>
                        <td class="mono cyan">OPEN</td>
                    </tr>
                `;
            });
            document.getElementById('db2-open-trades-body').innerHTML = openPosHtml;
        } else {
            document.getElementById('db2-open-trades-body').innerHTML = '<tr><td colspan="7" class="idle-state-row">No open trades</td></tr>';
        }

        const unRealizedNet = unRealizedFloating;
        document.getElementById('db2-unrealized-net').innerText = (unRealizedNet >= 0 ? '+' : '') + formatCurrency(unRealizedNet);
        document.getElementById('db2-unrealized-net').className = 'kpi-val mono ' + (unRealizedNet >= 0 ? 'profit' : 'loss');
        document.getElementById('db2-unrealized-pos').innerText = (positionsData ? positionsData.length : 0);
        document.getElementById('db2-unrealized-floating').innerText = (unRealizedFloating >= 0 ? '+' : '') + formatCurrency(unRealizedFloating);
        document.getElementById('db2-unrealized-floating').className = 'mono ' + (unRealizedFloating >= 0 ? 'profit' : 'loss');
        document.getElementById('db2-unrealized-wins').innerText = unRealizedWins;
        document.getElementById('db2-unrealized-losses').innerText = unRealizedLosses;

        // Overall Today Update (merge unrealized into today for accurate account change)
        const trueTodayNet = todayNet + unRealizedNet;
        document.getElementById('db2-today-net').innerText = (trueTodayNet >= 0 ? '+' : '') + formatCurrency(trueTodayNet);
        document.getElementById('db2-today-net').className = 'kpi-val mono ' + (trueTodayNet >= 0 ? 'profit' : 'loss');

        if (scannerData) {
            document.getElementById('db2-scan-evals').innerText = scannerData.evaluations || 0;
            document.getElementById('db2-scan-signals').innerText = scannerData.signals || 0;
            document.getElementById('db2-scan-qual').innerText = scannerData.qualified || 0;
            document.getElementById('db2-scan-rej').innerText = scannerData.rejected || 0;
        }

    } catch (e) {
        console.error("fetchDashboardDataV2 error:", e);
    }
}
"""

if 'fetchDashboardDataV2' not in content:
    content += dashboard_v2_code

content = content.replace('fetchDashboardData(),', 'fetchDashboardData(), fetchDashboardDataV2(),')

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Dashboard V2 JS updated.")
