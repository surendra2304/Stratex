import re
import sys

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update font imports
content = re.sub(
    r'<link href="https://fonts\.googleapis\.com/css2\?family=Inter:wght@400;500;600;700&family=JetBrains\+Mono.*?rel="stylesheet">',
    '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">',
    content, flags=re.DOTALL
)

# 2. Replace Header
new_header = """            <!-- PERSISTENT TOP STATUS BAR -->
            <header class="top-status-bar">
                <div class="status-left">
                    <div class="brand-title" style="margin-right: 20px;">
                        <span style="color: #F59E0B; font-size: 16px;">⚡</span>
                        <span class="brand-main" style="color: var(--text-primary); font-family: var(--font-heading); font-weight: 700;">ALGORITHMIC TRADING BOT</span>
                    </div>
                    
                    <div class="engine-state-badge">
                        <span class="dot dot-green" id="status-indicator"></span>
                        <strong id="engine-status" class="engine-state-val" style="color: var(--profit-green);">ENGINE ONLINE</strong>
                    </div>

                    <div class="status-sep">|</div>

                    <div class="status-item">
                        <span class="badge-mono" style="border: none; background: transparent; padding: 0;">TESTNET</span>
                    </div>

                    <div class="status-sep">|</div>

                    <div class="status-item">
                        <span class="status-lbl">UPTIME</span>
                        <span class="status-val mono" id="hdr-uptime">00:00:00</span>
                    </div>
                </div>
                
                <div class="status-right">
                    <div class="clock-display" style="border: none; background: transparent; padding: 0;">
                        <span id="live-clock" class="mono clock-val">--:--:-- IST</span>
                    </div>

                    <div class="notif-bell-container" style="margin-left: 10px;">
                        <button class="btn-terminal-icon" id="btn-notif" onclick="toggleNotificationDropdown()" title="System Alerts" style="border: none; background: transparent; font-size: 16px;">
                            🔔
                            <span class="badge-count" id="notif-badge" style="display: none;">0</span>
                        </button>
                    </div>

                    <button class="btn-terminal-pill" id="btn-sound-toggle" onclick="toggleSoundAlerts()" title="Toggle Terminal Audio" style="border: none; background: transparent;">
                        <span id="sound-status-text">🔊 ON</span>
                    </button>
                </div>
            </header>"""

content = re.sub(r'<!-- PERSISTENT TOP STATUS BAR -->.*?<\/header>', new_header, content, flags=re.DOTALL)

# 3. Replace Sidebar
new_sidebar = """        <!-- ─── 1. TERMINAL SIDEBAR NAVIGATION ─── -->
        <aside class="sidebar">
            <nav class="sidebar-nav" style="padding-top: 24px;">
                <a href="#dashboard" class="nav-item active" data-view="dashboard" id="nav-dashboard">
                    <span>Dashboard</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#scanner" class="nav-item" data-view="scanner" id="nav-scanner">
                    <span>Scanner</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#positions" class="nav-item" data-view="positions" id="nav-positions">
                    <span>Positions</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#trades" class="nav-item" data-view="trades" id="nav-trades">
                    <span>Trades</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#markets" class="nav-item" data-view="markets" id="nav-markets">
                    <span>Markets</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#strategies" class="nav-item" data-view="strategies" id="nav-strategies">
                    <span>Strategies</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#risk" class="nav-item" data-view="risk" id="nav-risk">
                    <span>Risk</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#analytics" class="nav-item" data-view="analytics" id="nav-analytics">
                    <span>Analytics</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#system" class="nav-item" data-view="system" id="nav-system">
                    <span>System</span>
                    <span class="nav-indicator"></span>
                </a>
                <a href="#settings" class="nav-item" data-view="settings" id="nav-settings">
                    <span>Settings</span>
                    <span class="nav-indicator"></span>
                </a>
            </nav>

            <div class="sidebar-system-health" style="border-top: 1px solid var(--border-medium); padding: 16px 20px;">
                <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 12px; letter-spacing: 0.5px;">SYSTEM STATUS</div>
                
                <div style="display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;"><span style="color: var(--text-secondary); font-size: 11px;">Binance REST</span><span class="dot dot-green" id="h-bn-rest"></span></div>
                    <div style="display: flex; justify-content: space-between; align-items: center;"><span style="color: var(--text-secondary); font-size: 11px;">WebSocket</span><span class="dot dot-green" id="h-ws"></span></div>
                    <div style="display: flex; justify-content: space-between; align-items: center;"><span style="color: var(--text-secondary); font-size: 11px;">Market Data</span><span class="dot dot-green" id="h-md"></span></div>
                    <div style="display: flex; justify-content: space-between; align-items: center;"><span style="color: var(--text-secondary); font-size: 11px;">Execution</span><span class="dot dot-green" id="h-ex"></span></div>
                    <div style="display: flex; justify-content: space-between; align-items: center;"><span style="color: var(--text-secondary); font-size: 11px;">Strategy</span><span class="dot dot-green" id="h-se"></span></div>
                    <div style="display: flex; justify-content: space-between; align-items: center;"><span style="color: var(--text-secondary); font-size: 11px;">Portfolio</span><span class="dot dot-green" id="h-pf"></span></div>
                    <div style="display: flex; justify-content: space-between; align-items: center;"><span style="color: var(--text-secondary); font-size: 11px;">Risk</span><span class="dot dot-green" id="h-rk"></span></div>
                    <div style="display: flex; justify-content: space-between; align-items: center;"><span style="color: var(--text-secondary); font-size: 11px;">Persistence</span><span class="dot dot-green" id="h-db"></span></div>
                </div>

                <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 12px; letter-spacing: 0.5px;">LATENCY</div>
                
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <span style="color: var(--text-secondary); font-size: 11px;">REST</span>
                    <span style="font-family: var(--font-mono); font-size: 11px; color: var(--text-primary);" id="lat-rest">42 ms</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="color: var(--text-secondary); font-size: 11px;">WebSocket</span>
                    <span style="font-family: var(--font-mono); font-size: 11px; color: var(--text-primary);" id="lat-ws">18 ms</span>
                </div>
            </div>
        </aside>"""

content = re.sub(r'<!-- ─── 1\. TERMINAL SIDEBAR NAVIGATION ─── -->.*?<\/aside>', new_sidebar, content, flags=re.DOTALL)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("index.html rewritten successfully.")
