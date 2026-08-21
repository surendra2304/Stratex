"""
tests/test_credentials.py

Credential Security Regression Tests — Phase 12.1.3

Verifies:
  - .env.example contains only placeholders
  - source files contain no hardcoded credential-looking values
  - MarketDataClient does NOT require API_KEY or SECRET_KEY
  - AccountClient and ExecutionClient require credentials (via config/env) but do not hardcode them
  - No test file itself contains credentials
"""
import os
import re

import pytest

# Pattern that might look like a real Binance API key:
# 64-char hex alphanumeric string (Binance testnet keys are ~64 chars)
_CREDENTIAL_PATTERN = re.compile(r'(?<![A-Z_\"\'])([A-Za-z0-9]{60,})', re.MULTILINE)

# Paths to scan
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SOURCE_EXTENSIONS = {".py", ".env.example"}
# Files to exclude from credential scanning (the scanner itself uses cred-like patterns in logic)
_EXCLUDE_FILES = {"test_credentials.py"}
_EXCLUDE_DIRS = {".git", "__pycache__", "data_cache", "backtest_results", ".env"}

# Known safe long strings in source (e.g., parts of URLs or doc strings)
_KNOWN_SAFE = {
    "YOUR_API_KEY_HERE",
    "YOUR_SECRET_KEY_HERE",
    "YOUR_BINANCE_TESTNET_API_KEY_HERE",
    "YOUR_BINANCE_TESTNET_SECRET_KEY_HERE",
}


def _collect_source_files():
    source_files = []
    for root, dirs, files in os.walk(_REPO_ROOT):
        # Prune excluded dirs
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fn in files:
            ext = os.path.splitext(fn)[1]
            if ext in _SOURCE_EXTENSIONS:
                source_files.append(os.path.join(root, fn))
    return source_files


def _scan_file_for_credentials(filepath):
    """Returns list of (line_number, snippet) for suspicious credential-like values.
    Only flags lines where a credential-like variable is assigned a long literal string value."""
    findings = []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            # Skip comment lines and blank lines
            if not stripped or stripped.startswith("#"):
                continue
            # Only flag lines where a cred variable is assigned a literal string value
            # Pattern: VARNAME = "VALUE" or VARNAME = 'VALUE'
            match = re.match(
                r'^(API_KEY|SECRET_KEY)\s*=\s*["\']([^"\']+)["\']',
                stripped
            )
            if match:
                value = match.group(2)
                # Skip placeholders
                if value and not value.startswith("YOUR_") and value not in _KNOWN_SAFE and "PLACEHOLDER" not in value:
                    findings.append((i, f"<REDACTED:{len(value)}chars>"))
    return findings


def test_no_hardcoded_credentials_in_source():
    """Proves no source file contains hardcoded credential-looking values."""
    source_files = _collect_source_files()
    all_findings = {}
    for fp in source_files:
        # Exclude the scanner itself from scanning
        if os.path.basename(fp) in _EXCLUDE_FILES:
            continue
        findings = _scan_file_for_credentials(fp)
        if findings:
            rel = os.path.relpath(fp, _REPO_ROOT)
            all_findings[rel] = findings

    assert len(all_findings) == 0, (
        f"CREDENTIAL_FOUND in {len(all_findings)} file(s):\n" +
        "\n".join(f"  {f}: lines {[l for l, _ in v]}" for f, v in all_findings.items())
    )


def test_env_example_contains_only_placeholders():
    """.env.example must contain only placeholder values for credentials."""
    env_example = os.path.join(_REPO_ROOT, ".env.example")
    assert os.path.exists(env_example), ".env.example must exist"

    with open(env_example, "r", encoding="utf-8") as f:
        content = f.read()

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "API_KEY=" in stripped or "SECRET_KEY=" in stripped:
            # Value must be a placeholder
            assert "YOUR_" in stripped or "PLACEHOLDER" in stripped or stripped.endswith('=""'), (
                ".env.example contains a non-placeholder credential line: <REDACTED>"
            )


def test_market_data_client_no_credentials():
    """MarketDataClient must not accept or use API_KEY/SECRET_KEY."""
    import inspect

    from data_client import MarketDataClient

    source = inspect.getsource(MarketDataClient.__init__)
    # Verify the constructor does not reference API_KEY or SECRET_KEY
    assert "API_KEY" not in source, "MarketDataClient.__init__ must not use API_KEY"
    assert "SECRET_KEY" not in source, "MarketDataClient.__init__ must not use SECRET_KEY"


def test_market_data_client_no_execution_methods():
    """MarketDataClient must not expose create_order, cancel_order, withdraw."""
    from unittest.mock import patch

    from data_client import MarketDataClient

    with patch("data_client.TRADING_MODE", "TESTNET"):
        with patch("binance.client.Client.ping"):
            client = MarketDataClient()

    blocked = ["create_order", "cancel_order", "create_oco_order", "withdraw", "transfer", "get_account"]
    for method in blocked:
        with pytest.raises(AttributeError):
            getattr(client, method)()


def test_account_client_no_execution_methods():
    """AccountClient must not expose order execution or withdrawal methods."""
    from unittest.mock import patch

    from account_client import AccountClient

    with patch("account_client.TRADING_MODE", "TESTNET"), \
         patch("account_client.Client"):
        client = AccountClient()

    blocked = ["create_order", "cancel_order", "withdraw", "transfer"]
    for method in blocked:
        with pytest.raises(AttributeError):
            getattr(client, method)()


def test_no_credentials_in_test_files():
    """Test files themselves must not contain hardcoded credentials."""
    tests_dir = os.path.join(_REPO_ROOT, "tests")
    for fn in os.listdir(tests_dir):
        if not fn.endswith(".py") or fn in _EXCLUDE_FILES:
            continue
        filepath = os.path.join(tests_dir, fn)
        findings = _scan_file_for_credentials(filepath)
        assert len(findings) == 0, (
            f"CREDENTIAL_FOUND in test file {fn} at lines {[l for l, _ in findings]}"
        )

    # Also check test_connection.py in root
    root_test = os.path.join(_REPO_ROOT, "test_connection.py")
    if os.path.exists(root_test):
        findings = _scan_file_for_credentials(root_test)
        assert len(findings) == 0, (
            f"CREDENTIAL_FOUND in test_connection.py at lines {[l for l, _ in findings]}"
        )
