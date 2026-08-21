import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

modal_html = """    <!-- TRADE LIFECYCLE INSPECTOR MODAL & BACKDROP -->
    <div id="drawer-backdrop" class="drawer-backdrop" onclick="closeInspectorDrawer()" style="display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background: rgba(0,0,0,0.8); z-index: 999; backdrop-filter: blur(2px);"></div>
    <div id="inspector-drawer" class="inspector-modal" style="display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 90vw; max-width: 1400px; height: 85vh; background: var(--bg-panel); border: 1px solid var(--border-medium); border-radius: 8px; z-index: 1000; box-shadow: 0 10px 40px rgba(0,0,0,0.5); flex-direction: row; overflow: hidden;">
        <div class="modal-left-details" style="width: 30%; height: 100%; border-right: 1px solid var(--border-medium); background: var(--bg-subtle); display: flex; flex-direction: column;">
            <div class="drawer-header" style="padding: 16px 20px; border-bottom: 1px solid var(--border-medium); display: flex; justify-content: space-between; align-items: center;">
                <div class="drawer-title-wrap">
                    <span class="badge-indigo" style="background: var(--accent-primary-dim); color: var(--accent-primary); border: 1px solid var(--border-indigo); padding: 2px 6px; border-radius: 4px; font-size: 9px; font-family: var(--font-mono); font-weight: 700;">AUDIT</span>
                    <span class="drawer-title" id="drawer-title" style="font-family: var(--font-heading); font-size: 13px; font-weight: 700; margin-left: 8px; color: var(--text-primary);">TRADE DETAILS</span>
                </div>
            </div>
            <div class="drawer-body" id="drawer-body" style="padding: 20px; overflow-y: auto; flex: 1;">
                <div class="empty-state">Select a trade row to inspect complete execution lifecycle telemetry.</div>
            </div>
        </div>
        <div class="modal-right-chart" style="width: 70%; height: 100%; display: flex; flex-direction: column; background: var(--bg-base);">
            <div class="chart-toolbar" style="padding: 12px 20px; border-bottom: 1px solid var(--border-medium); display: flex; gap: 12px; align-items: center; background: var(--bg-panel);">
                <div class="tf-buttons" style="display: flex; gap: 4px;">
                    <button class="btn-tf" style="background: var(--bg-input); color: var(--text-muted); border: 1px solid var(--border-medium); padding: 4px 8px; font-size: 11px; border-radius: 4px;">5m</button>
                    <button class="btn-tf active" style="background: var(--accent-primary); color: #fff; border: 1px solid var(--accent-primary); padding: 4px 8px; font-size: 11px; border-radius: 4px;">15m</button>
                    <button class="btn-tf" style="background: var(--bg-input); color: var(--text-muted); border: 1px solid var(--border-medium); padding: 4px 8px; font-size: 11px; border-radius: 4px;">30m</button>
                    <button class="btn-tf" style="background: var(--bg-input); color: var(--text-muted); border: 1px solid var(--border-medium); padding: 4px 8px; font-size: 11px; border-radius: 4px;">1h</button>
                    <button class="btn-tf" style="background: var(--bg-input); color: var(--text-muted); border: 1px solid var(--border-medium); padding: 4px 8px; font-size: 11px; border-radius: 4px;">2h</button>
                    <button class="btn-tf" style="background: var(--bg-input); color: var(--text-muted); border: 1px solid var(--border-medium); padding: 4px 8px; font-size: 11px; border-radius: 4px;">4h</button>
                </div>
                <div class="status-sep" style="color: var(--border-medium);">|</div>
                <select class="select-terminal" style="background: var(--bg-input); color: var(--text-secondary); border: 1px solid var(--border-medium); padding: 4px 8px; border-radius: 4px; font-size: 11px; appearance: none; cursor: pointer;">
                    <option>CANDLESTICK ▾</option>
                    <option>HEIKIN ASHI</option>
                    <option>BARS</option>
                    <option>LINE</option>
                    <option>AREA</option>
                </select>
                <select class="select-terminal" style="background: var(--bg-input); color: var(--text-secondary); border: 1px solid var(--border-medium); padding: 4px 8px; border-radius: 4px; font-size: 11px; appearance: none; cursor: pointer;">
                    <option>INDICATORS ▾</option>
                </select>
                <select class="select-terminal" style="background: var(--bg-input); color: var(--text-secondary); border: 1px solid var(--border-medium); padding: 4px 8px; border-radius: 4px; font-size: 11px; appearance: none; cursor: pointer;">
                    <option>DRAW ▾</option>
                </select>
                <div style="flex: 1;"></div>
                <button class="drawer-close" onclick="closeInspectorDrawer()" title="Close Inspector" style="background: none; border: none; color: var(--text-muted); font-size: 24px; cursor: pointer; padding: 0 8px;">&times;</button>
            </div>
            <div id="modal-chart-container" style="flex: 1; padding: 20px; position: relative;">
                <canvas id="modal-trade-chart"></canvas>
            </div>
        </div>
    </div>"""

content = re.sub(r'<!-- TRADE LIFECYCLE INSPECTOR DRAWER & BACKDROP -->.*?<\/aside>', modal_html, content, flags=re.DOTALL)

with open('static/index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("index.html rewritten successfully.")
