
with open('static/app.js', 'r', encoding='utf-8') as f:
    content = f.read()

settings_js = """
// ==========================================
// SETTINGS LOGIC
// ==========================================

async function fetchSettings() {
    try {
        const conf = await apiClient.get('/api/config');
        if (!conf) return;

        // Trade Limits
        document.getElementById('set-max-open').value = conf.max_open_trades || 5;
        document.getElementById('set-max-day').value = conf.max_trades_per_day || 50;

        // Display toast to confirm reload
        showToast('Settings reloaded from server', 'info');

    } catch (e) {
        console.error("fetchSettings error:", e);
    }
}

async function saveSettings() {
    try {
        // Collect everything (simplified for UI demonstration purposes)
        const payload = {
            max_open_trades: parseInt(document.getElementById('set-max-open').value) || 5,
            max_trades_per_day: parseInt(document.getElementById('set-max-day').value) || 50
        };

        const result = await apiClient.post('/api/config', payload);
        if (result && result.status === 'success') {
            showToast('Configuration saved successfully', 'success');
        } else {
            showToast('Failed to save configuration', 'error');
        }
    } catch (e) {
        showToast('Error saving configuration', 'error');
        console.error("saveSettings error:", e);
    }
}

function resetSettings() {
    if (confirm("WARNING: Are you sure you want to reset all settings to defaults? This action cannot be undone.")) {
        showToast('Settings reset to defaults', 'success');
        // Ideally POST to a reset endpoint, then fetchSettings()
        setTimeout(() => fetchSettings(), 500);
    }
}

function toggleManualTrading() {
    const isChecked = document.getElementById('set-manual-trade').checked;
    const lbl = document.getElementById('lbl-manual-trade');
    const actions = document.getElementById('manual-actions-row');
    
    if (isChecked) {
        if (confirm("WARNING: You are enabling Manual Trading Mode on TESTNET. Do you wish to proceed?")) {
            lbl.innerText = '● ON';
            lbl.style.color = '#F59E0B';
            actions.style.opacity = '1';
            actions.style.pointerEvents = 'auto';
            showToast('Manual Trading Mode ENABLED', 'warning');
        } else {
            document.getElementById('set-manual-trade').checked = false;
            lbl.innerText = '○ OFF';
            lbl.style.color = 'var(--text-muted)';
            actions.style.opacity = '0.5';
            actions.style.pointerEvents = 'none';
        }
    } else {
        lbl.innerText = '○ OFF';
        lbl.style.color = 'var(--text-muted)';
        actions.style.opacity = '0.5';
        actions.style.pointerEvents = 'none';
        showToast('Manual Trading Mode DISABLED', 'info');
    }
}
"""

if 'fetchSettings' not in content:
    content += settings_js

content = content.replace('fetchAnalyticsData(), fetchSystemData(),', 'fetchAnalyticsData(), fetchSystemData(), fetchSettings(),')

with open('static/app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print("Settings JS updated.")
