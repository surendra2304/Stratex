document.addEventListener('DOMContentLoaded', async () => {
    
    // --- UI Toggles ---
    const btnTable = document.getElementById('btn-table');
    const btnChart = document.getElementById('btn-chart');
    const viewTable = document.getElementById('view-table');
    const viewChart = document.getElementById('view-chart');

    btnTable.addEventListener('click', () => {
        btnTable.classList.add('active');
        btnChart.classList.remove('active');
        viewTable.style.display = 'block';
        viewChart.style.display = 'none';
    });

    btnChart.addEventListener('click', () => {
        btnChart.classList.add('active');
        btnTable.classList.remove('active');
        viewChart.style.display = 'block';
        viewTable.style.display = 'none';
        
        // Force resize when unhidden to fix canvas width issues
        if (window.chartInstance) {
            window.chartInstance.resize(document.getElementById('tvchart').clientWidth, document.getElementById('tvchart').clientHeight);
        }
    });

    // --- Table Ledger View ---
    const tableBody = document.getElementById('trade-table-body');
    
    function getBadgeClass(status) {
        const s = status.toUpperCase();
        if (s.includes('WIN')) return 'badge badge-win';
        if (s.includes('LOSS')) return 'badge badge-loss';
        if (s === 'ACTIVE') return 'badge badge-active';
        return 'badge';
    }

    function getActionLabel(action) {
        const s = action.toUpperCase();
        if (s === 'BUY') return '🟢 LONG';
        if (s === 'SELL') return '🔴 SHORT';
        return action;
    }

    async function loadTableData() {
        try {
            const res = await fetch('/api/trades');
            const data = await res.json();
            
            // Update Advanced Stats
            document.getElementById('stat-trades').textContent = data.total_trades;
            document.getElementById('stat-winrate').textContent = data.win_rate.toFixed(1) + '%';
            document.getElementById('stat-winrate').style.color = data.win_rate >= 50 ? '#4caf50' : '#ef5350';
            
            document.getElementById('stat-pf').textContent = data.profit_factor.toFixed(2);
            document.getElementById('stat-pf').style.color = data.profit_factor >= 1.5 ? '#4caf50' : (data.profit_factor >= 1 ? '#ff9800' : '#ef5350');
            
            // Update Net PnL
            const pnlElement = document.getElementById('net-pnl-val');
            pnlElement.textContent = (data.net_pnl >= 0 ? '+$' : '-$') + Math.abs(data.net_pnl).toFixed(2);
            pnlElement.style.color = data.net_pnl >= 0 ? '#4caf50' : '#ef5350';
            
            tableBody.innerHTML = '';
            
            data.positions.forEach(t => {
                const tr = document.createElement('tr');
                
                let pnlString = '-';
                let pnlColor = '#8b92a5';
                if (t.status !== 'ACTIVE') {
                    pnlString = (t.pnl >= 0 ? '+$' : '-$') + Math.abs(t.pnl).toFixed(2);
                    pnlColor = t.pnl >= 0 ? '#4caf50' : '#ef5350';
                }
                
                tr.innerHTML = `
                    <td>${t.timestamp}</td>
                    <td><b>${t.symbol}</b></td>
                    <td>${getActionLabel(t.action)}</td>
                    <td>$${parseFloat(t.entry_price).toFixed(2)}</td>
                    <td>${t.quantity}</td>
                    <td><span class="${getBadgeClass(t.status)}">${t.status}</span></td>
                    <td style="color: ${pnlColor}; font-weight: bold;">${pnlString}</td>
                `;
                tableBody.appendChild(tr);
            });
        } catch (e) {
            console.error("Failed to load trades table:", e);
        }
    }


    // --- Chart View ---
    async function loadChartData() {
        const chartOptions = {
            layout: {
                textColor: '#d1d4dc',
                background: { type: 'solid', color: '#131722' }
            },
            grid: {
                vertLines: { color: '#2b2b43' },
                horzLines: { color: '#2b2b43' }
            },
            timeScale: {
                timeVisible: true,
                secondsVisible: false,
            }
        };
        
        const chart = LightweightCharts.createChart(document.getElementById('tvchart'), chartOptions);
        window.chartInstance = chart; // save for resizing later

        const candlestickSeries = chart.addCandlestickSeries({
            upColor: '#26a69a',
            downColor: '#ef5350',
            borderVisible: false,
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350',
        });

        // Fetch Candles
        try {
            const response = await fetch('/api/candles');
            const data = await response.json();
            if (data.length > 0) {
                candlestickSeries.setData(data);
            }
        } catch (e) {
            console.error("Error fetching candles:", e);
        }

        // Fetch Chart Markers
        try {
            const response = await fetch('/api/chart_trades');
            const trades = await response.json();
            trades.sort((a, b) => a.time - b.time);
            if (trades.length > 0) {
                candlestickSeries.setMarkers(trades);
            }
        } catch (e) {
            console.error("Error fetching chart markers:", e);
        }
        
        window.addEventListener('resize', () => {
            if (viewChart.style.display !== 'none') {
                chart.resize(document.getElementById('tvchart').clientWidth, document.getElementById('tvchart').clientHeight);
            }
        });
    }

    // Initialize both
    await loadTableData();
    await loadChartData();
    
    // Optional: Refresh table data automatically every 10 seconds
    setInterval(loadTableData, 10000);
});
