import os

raw = open('.env', 'rb').read()
# Detect encoding: UTF-16LE has BOM \xff\xfe
if raw[:2] == b'\xff\xfe':
    content = raw[2:].decode('utf-16le')
else:
    content = raw.decode('utf-8')

markers = ["dummy", "your_api", "your_secret", "redacted", "fake", "placeholder"]
print("=== .env line-by-line analysis ===")
for line in content.splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    if '=' in line:
        k, v = line.split('=', 1)
        is_dummy = any(m in v.lower() for m in markers)
        print(f"  {k}: present={bool(v)}, length={len(v)}, is_placeholder={is_dummy}")
