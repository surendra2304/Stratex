import re

css_path = 'static/style.css'
with open(css_path, 'r', encoding='utf-8') as f:
    css = f.read()

# Replace root variables
new_root = """:root {
    /* ✨🚀 NEXT-GEN QUANTITATIVE TRADING TERMINAL ✨🚀 */
    --bg-base:        #09090b;
    --bg-sidebar:     #121217;
    --bg-header:      #121217;
    --bg-panel:       #18181b;
    --bg-panel-hover: #27272a;
    --bg-subtle:      #0f0f13;
    --bg-input:       #18181b;

    --border-subtle:  #27272a;
    --border-medium:  #3f3f46;
    --border-active:  #52525b;
    --border-indigo:  rgba(139, 92, 246, 0.4);

    --accent-primary:       #8b5cf6;
    --accent-primary-dim:   rgba(139, 92, 246, 0.15);
    --accent-secondary:     #0ea5e9;
    --accent-secondary-dim: rgba(14, 165, 233, 0.15);
    --accent-bright:        #a78bfa;

    --profit-green:   #10b981;
    --profit-dim:     rgba(16, 185, 129, 0.15);
    --loss-red:       #f43f5e;
    --loss-dim:       rgba(244, 63, 94, 0.15);
    --warning-amber:  #f59e0b;
    --warning-dim:    rgba(245, 158, 11, 0.15);

    --text-primary:   #fafafa;
    --text-secondary: #a1a1aa;
    --text-muted:     #71717a;
    --text-dim:       #52525b;

    --font-heading:   'Sora', -apple-system, sans-serif;
    --font-body:      'Inter', -apple-system, sans-serif;
    --font-mono:      'JetBrains Mono', monospace;

    --sidebar-width:  250px;
    --status-height:  56px;
    
    --shadow-neon: 0 0 10px rgba(139, 92, 246, 0.5), 0 0 20px rgba(139, 92, 246, 0.3);
    --shadow-profit: 0 0 8px rgba(16, 185, 129, 0.4);
    --shadow-loss: 0 0 8px rgba(244, 63, 94, 0.4);
}"""

# Find the root block
root_match = re.search(r':root\s*\{.*?(?=\n\})', css, flags=re.DOTALL)
if root_match:
    css = css[:root_match.start()] + new_root + css[root_match.end()+1:]

# Append custom glow styles
css += """
.val-profit {
    color: var(--profit-green) !important;
    text-shadow: var(--shadow-profit);
}
.val-loss {
    color: var(--loss-red) !important;
    text-shadow: var(--shadow-loss);
}
.brand-title {
    font-family: 'Sora', sans-serif;
    font-weight: 800;
    background: linear-gradient(90deg, #8b5cf6, #0ea5e9);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    text-shadow: 0 0 10px rgba(139, 92, 246, 0.3);
    letter-spacing: 2px;
}
.stat-card {
    border-radius: 12px !important;
    background: linear-gradient(145deg, var(--bg-panel), var(--bg-sidebar)) !important;
    border: 1px solid var(--border-subtle) !important;
    box-shadow: 0 4px 15px rgba(0,0,0,0.5) !important;
    transition: all 0.3s ease !important;
}
.stat-card:hover {
    transform: translateY(-2px);
    border-color: var(--accent-primary) !important;
    box-shadow: var(--shadow-neon) !important;
}
.sidebar {
    background: rgba(18, 18, 23, 0.8) !important;
    backdrop-filter: blur(12px) !important;
    border-right: 1px solid rgba(255,255,255,0.05) !important;
}
.top-nav {
    background: rgba(18, 18, 23, 0.9) !important;
    backdrop-filter: blur(12px) !important;
    border-bottom: 1px solid rgba(255,255,255,0.05) !important;
}
.btn-primary {
    background: linear-gradient(90deg, #8b5cf6, #0ea5e9) !important;
    color: white !important;
    border: none !important;
    box-shadow: var(--shadow-neon) !important;
    border-radius: 8px !important;
}
.btn-primary:hover {
    filter: brightness(1.2);
}
/* Neon scrollbars */
::-webkit-scrollbar-thumb {
    background: var(--border-active);
    border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
    background: var(--accent-primary);
    box-shadow: var(--shadow-neon);
}
"""

with open(css_path, 'w', encoding='utf-8') as f:
    f.write(css)

# Update index.html to apply some classes
html_path = 'static/index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()
    
# Change brand name to be glowing
html = html.replace('<div class="brand-title">STRATEX</div>', '<div class="brand-title">STRATEX QUANT V2</div>')
html = html.replace('<span style="font-weight: 800; font-size: 16px; letter-spacing: 1px; color: var(--text-primary);">STRATEX</span>', '<span class="brand-title" style="font-size: 18px;">STRATEX QUANT V2</span>')

# Convert the engine online indicator to neon green
html = html.replace('background: var(--profit-dim); color: var(--profit-green);', 'background: var(--profit-dim); color: var(--profit-green); text-shadow: var(--shadow-profit); box-shadow: var(--shadow-profit);')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
