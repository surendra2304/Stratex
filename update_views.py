
with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Rename views
content = content.replace('id="view-signals"', 'id="view-scanner"')
content = content.replace('id="view-market"', 'id="view-markets"')
content = content.replace('id="view-activity"', 'id="view-system"')

# Add view-positions before view-trades
positions_view = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: POSITIONS
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-positions">
                <div class="page-header">
                    <div class="page-title-wrap">
                        <h1 class="page-title">Active Positions</h1>
                        <p class="page-subtitle">Real-time portfolio exposure.</p>
                    </div>
                </div>
                <div class="panel-card table-card">
                    <div class="panel-table-wrap">
                        <table class="terminal-table">
                            <thead>
                                <tr>
                                    <th>SYMBOL</th>
                                    <th>STRATEGY</th>
                                    <th>ENTRY PRICE</th>
                                    <th>MARK PRICE</th>
                                    <th>PROTECTION</th>
                                    <th>UNREALIZED PNL</th>
                                </tr>
                            </thead>
                            <tbody id="positions-full-body">
                                <tr>
                                    <td colspan="6" class="idle-state-row">No open positions</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
"""
content = content.replace('<div class="view-container" id="view-trades">', positions_view + '            <div class="view-container" id="view-trades">')

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Updated index.html views')
