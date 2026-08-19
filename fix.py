with open('rewrite_scanner.py', 'r', encoding='utf-8') as f:
    code = f.read()
code = code.replace('<div class="view-container" id="view-positions">', '<div class="view-container" id="view-markets">')
with open('rewrite_scanner.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Script updated.')
