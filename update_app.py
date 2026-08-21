
with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add window.showView if missing
if 'window.showView' not in content:
    content = 'window.showView = function(v) { let btn = document.querySelector(\'[data-view="\' + v + \'"]\'); if(btn) btn.click(); };\n' + content

# 2. Add chart rendering inside inspectTradeLifecycle
chart_code = """
    // Render Modal Chart
    const chartContainer = document.getElementById('modal-trade-chart');
    if (chartContainer) {
        let ctx = chartContainer.getContext('2d');
        if (window.modalChartInstance) { window.modalChartInstance.destroy(); }
        window.modalChartInstance = new Chart(ctx, {
            type: 'line',
            data: {
                labels: ['Entry', 'Mid', 'Exit'],
                datasets: [{
                    label: 'Price',
                    data: [entryPx, (entryPx+exitPx)/2, exitPx],
                    borderColor: '#3B82F6',
                    backgroundColor: 'rgba(59, 130, 246, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } },
                    y: { grid: { color: '#1D2A3A' }, ticks: { color: '#66758A' } }
                }
            }
        });
    }
"""

if 'modalChartInstance' not in content:
    content = content.replace('function inspectTradeLifecycle(t) {', 'function inspectTradeLifecycle(t) {\n' + chart_code)
    # Also for the other one
    chart_code_2 = chart_code.replace('entryPx', 'Number(trade.entry_price || trade.price || 0)').replace('exitPx', 'Number(trade.exit_price || trade.entry_price || trade.price || 0)')
    content = content.replace('function inspectTradeLifecycle(trade) {', 'function inspectTradeLifecycle(trade) {\n' + chart_code_2)

# 3. Replace old color codes
content = content.replace('#5B7FFF', '#3B82F6') # old primary to new primary
content = content.replace('rgba(91, 127, 255', 'rgba(59, 130, 246')
content = content.replace('#FB7185', '#EF4444')
content = content.replace('#F5A623', '#F59E0B')
content = content.replace('#0A0F1E', '#05070B')
content = content.replace('#111A2E', '#0A0F16')
content = content.replace('#223050', '#1D2A3A')

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print('app.js updated')
