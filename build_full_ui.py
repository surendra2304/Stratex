import re

files_in_order = [
    'rewrite_dashboard.py',
    'rewrite_scanner.py',
    'rewrite_positions.py',
    'rewrite_trades.py',
    'rewrite_markets.py',
    'rewrite_strategies.py',
    'rewrite_risk.py',
    'rewrite_analytics.py',
    'rewrite_system.py'
]

views_html = ""

for file in files_in_order:
    try:
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read()
            # Extract the triple quoted string
            # Most files have `html = """..."""`
            match = re.search(r'"""(.*?)"""', content, re.DOTALL)
            if match:
                views_html += match.group(1) + "\n"
            else:
                print(f"No triple quotes found in {file}")
    except Exception as e:
        print(f"Error reading {file}: {e}")

# Now read the index.html from backup_ui, replace the main content, and write it
with open('backup_ui/index.html', 'r', encoding='utf-8') as f:
    base_html = f.read()

# I also need to apply rewrite_ui.py's changes to header and sidebar.
# Let's extract new_header and new_sidebar from rewrite_ui.py
with open('rewrite_ui.py', 'r', encoding='utf-8') as f:
    ui_content = f.read()
    
    header_match = re.search(r'new_header = """(.*?)"""', ui_content, re.DOTALL)
    if header_match:
        base_html = re.sub(r'<!-- PERSISTENT TOP STATUS BAR -->.*?<\/header>', header_match.group(1), base_html, flags=re.DOTALL)
        
    sidebar_match = re.search(r'new_sidebar = """(.*?)"""', ui_content, re.DOTALL)
    if sidebar_match:
        # Note: rewrite_ui.py replaced from <!-- 1. TERMINAL... to </aside>
        # Let's use a robust regex
        base_html = re.sub(r'<!-- \S+ 1\. TERMINAL SIDEBAR NAVIGATION \S+ -->.*?<\/aside>', sidebar_match.group(1), base_html, flags=re.DOTALL)

# Now inject views_html.
# We replace from <!-- ═══════════════════════════════════════════════════════════════
#                  VIEW 1: OVERVIEW DASHBOARD
#             ═══════════════════════════════════════════════════════════════ -->
# to the end of main content (before <div class="view-container" id="view-settings">)

start_marker = '<div class="view-container active" id="view-dashboard">'
end_marker = '<div class="view-container" id="view-settings">'

start_idx = base_html.find(start_marker)
end_idx = base_html.find(end_marker)

if start_idx != -1 and end_idx != -1:
    # Find the preceding comment for dashboard
    sep_marker = '<!-- ════'
    sep_idx = base_html.rfind(sep_marker, 0, start_idx)
    if sep_idx != -1:
        start_idx = sep_idx

    sep_idx_end = base_html.rfind(sep_marker, start_idx, end_idx)
    if sep_idx_end != -1:
        end_idx = sep_idx_end

    final_html = base_html[:start_idx] + views_html + base_html[end_idx:]
    
    with open('static/index.html', 'w', encoding='utf-8') as f:
        f.write(final_html)
    print("Successfully built index.html with all views!")
else:
    print("Could not find start/end markers in base HTML.")
