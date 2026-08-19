import sys

with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

markets_js = """
// ==========================================
// MARKETS LOGIC V2
// ==========================================

let activeMarketSymbol = 'BTCUSDT';
let activeMarketTF = '15m';
let marketsChartInst = null;
let marketCandles = [];

function changeMarketSymbol(sym, el) {
    activeMarketSymbol = sym;
    document.querySelectorAll('.mkt-sym').forEach(e => {
        e.classList.remove('active');
        e.style.color = 'var(--text-muted)';
        e.style.fontWeight = 'normal';
    });
    if (el) {
        el.classList.add('active');
        el.style.color = 'var(--text-primary)';
        el.style.fontWeight = '700';
    }
    document.getElementById('mkt-ticker').innerText = sym;
    fetchMarketData();
}

function changeMarketTimeframe(tf, el) {
    activeMarketTF = tf;
    document.querySelectorAll('.mkt-tf').forEach(e => {
        e.classList.remove('active');
        e.style.color = 'var(--text-muted)';
        e.style.fontWeight = 'normal';
    });
    if (el) {
        el.classList.add('active');
        el.style.color = 'var(--accent-primary)';
        el.style.fontWeight = '700';
    }
    fetchMarketData();
}

async function fetchMarketData() {
    try {
        const res = await apiClient.get(`/api/candles?symbol=${activeMarketSymbol}&tf=${activeMarketTF}&limit=100`);
        if (res && Array.isArray(res)) {
            marketCandles = res;
            renderMarketChart();
            updateMarketInfoBar(res);
        }
    } catch (e) {
        console.error("fetchMarketData error", e);
    }
}

function updateMarketInfoBar(data) {
    if (data.length === 0) return;
    const last = data[data.length - 1];
    const first = data[0];
    
    let high24 = last.high;
    let low24 = last.low;
    let vol24 = 0;
    
    data.forEach(c => {
        if (c.high > high24) high24 = c.high;
        if (c.low < low24) low24 = c.low;
        vol24 += c.volume;
    });
    
    const change = ((last.close - first.open) / first.open) * 100;
    const cClass = change >= 0 ? 'profit' : 'loss';
    const cStr = (change >= 0 ? '+' : '') + change.toFixed(2) + '%';
    
    // Ticker bar
    document.getElementById('mkt-price').innerText = '$' + last.close.toFixed(4);
    document.getElementById('mkt-change').innerText = cStr;
    document.getElementById('mkt-change').className = 'mono ' + cClass;
    document.getElementById('mkt-high').innerText = high24.toFixed(4);
    document.getElementById('mkt-low').innerText = low24.toFixed(4);
    document.getElementById('mkt-vol').innerText = vol24.toFixed(2);
    
    // Info table
    document.getElementById('mi-price').innerText = last.close.toFixed(4);
    document.getElementById('mi-open').innerText = last.open.toFixed(4);
    document.getElementById('mi-high').innerText = last.high.toFixed(4);
    document.getElementById('mi-low').innerText = last.low.toFixed(4);
    document.getElementById('mi-vol').innerText = vol24.toFixed(2);
    document.getElementById('mi-change').innerText = cStr;
    document.getElementById('mi-change').className = 'mono ' + cClass;
}

function renderMarketChart() {
    const ctx = document.getElementById('markets-main-chart');
    if (!ctx) return;
    
    if (marketsChartInst) marketsChartInst.destroy();
    
    const labels = marketCandles.map(c => new Date(c.time * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}));
    const data = marketCandles.map(c => c.close);
    
    const isBullish = marketCandles[marketCandles.length-1].close >= marketCandles[0].open;
    const strokeColor = isBullish ? '#10B981' : '#EF4444';
    const bgColor = isBullish ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)';
    
    marketsChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: activeMarketSymbol,
                data: data,
                borderColor: strokeColor,
                backgroundColor: bgColor,
                borderWidth: 2,
                pointRadius: 0,
                fill: true,
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(ctx) { return 'Price: ' + ctx.raw; }
                    }
                }
            },
            scales: {
                x: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A', maxTicksLimit: 10 } },
                y: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } }
            }
        }
    });
}
"""

if 'fetchMarketData' not in content:
    content += markets_js

content = content.replace('fetchDashboardData(), fetchDashboardDataV2(), fetchScannerDataV2(), fetchPositionsV2(),', 'fetchDashboardData(), fetchDashboardDataV2(), fetchScannerDataV2(), fetchPositionsV2(), fetchMarketData(),')

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Markets JS updated.")
