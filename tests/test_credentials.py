"""
tests/test_credentials.py

Credential Security Regression Tests — Stage 12.1.3

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

# A credential value is one long alphanumeric run (Binance keys are ~64 chars,
# most API secrets are 32+). Hyphenated/underscored strings are deliberately
# synthetic test values and are not flagged.
_CREDENTIAL_PATTERN = re.compile(r'[A-Za-z0-9]{32,}')

# Variable names that plausibly hold a credential. The previous scanner matched
# only lines starting with exactly `API_KEY =` or `SECRET_KEY =`, so it missed
# every other spelling — BINANCE_API_SECRET, lowercase names, dict entries and
# `os.environ[...] = "..."` assignments all passed unnoticed.
_CREDENTIAL_NAME = re.compile(
    r'(API_?KEY|SECRET|TOKEN|PASSW(?:OR)?D|CREDENTIAL|PRIVATE_?KEY)', re.I
)

# `NAME = "value"` or `"NAME": "value"`, capturing both sides.
_ASSIGNMENT = re.compile(
    r'''["']?([A-Za-z_][\w.\[\]"' ]{0,60}?)["']?\s*(?:=|:)\s*["']([^"']+)["']'''
)

# Paths to scan
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SOURCE_EXTENSIONS = {".py", ".env.example"}
# Files to exclude from credential scanning (the scanner itself uses cred-like patterns in logic)
_EXCLUDE_FILES = {"test_credentials.py"}
_EXCLUDE_DIRS = {
    ".git", "__pycache__", "data_cache", "backtest_results", ".env",
    # Dependency and tooling directories. These hold third-party code that is
    # not ours to audit, and scanning them produces false positives: a developer
    # who follows the README and creates .venv inside the repo used to fail this
    # test on a 64+ char string in flask/config.py.
    ".venv", "venv", "env", "site-packages", "node_modules",
    ".tox", ".nox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "optimization_results", "experiments",
}


def _is_third_party_dir(path):
    """True for virtualenvs and anything else that is not first-party source.

    ``pyvenv.cfg`` is the definitive virtualenv marker, so environments created
    under a name nobody thought to exclude are still pruned.
    """
    return os.path.exists(os.path.join(path, "pyvenv.cfg"))

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
        # Prune excluded dirs and any virtualenv / third-party tree
        dirs[:] = [
            d for d in dirs
            if d not in _EXCLUDE_DIRS and not _is_third_party_dir(os.path.join(root, d))
        ]
        for fn in files:
            ext = os.path.splitext(fn)[1]
            if ext in _SOURCE_EXTENSIONS:
                source_files.append(os.path.join(root, fn))
    return source_files


def _is_placeholder(value):
    """True for values that are obviously not a live credential."""
    upper = value.upper()
    return (
        value.startswith("YOUR_")
        or "PLACEHOLDER" in upper
        or "CHANGE_ME" in upper
        or "EXAMPLE" in upper
        or value.startswith(("<", "${", "{{"))
        or "os.getenv" in value
        or "os.environ" in value
    )


def _scan_file_for_credentials(filepath):
    """Returns list of (line_number, snippet) for suspicious credential-like values.

    Flags any assignment or mapping entry whose NAME looks credential-bearing and
    whose VALUE is a long alphanumeric run that is not an obvious placeholder or
    an environment lookup. Comment and blank lines are skipped.
    """
    findings = []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            # Skip comment lines and blank lines
            if not stripped or stripped.startswith("#"):
                continue
            for match in _ASSIGNMENT.finditer(stripped):
                name, value = match.group(1), match.group(2)
                if not _CREDENTIAL_NAME.search(name):
                    continue
                if _is_placeholder(value):
                    continue
                # This is the check the module always claimed to make but never
                # wired up: the value must actually look like a key.
                if not _CREDENTIAL_PATTERN.search(value):
                    continue
                findings.append((i, f"{name.strip()}=<REDACTED:{len(value)}chars>"))
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


# --- Proofs that the scanner itself is not vacuous -------------------------
#
# The detector above used to match only lines beginning with exactly
# `API_KEY =` or `SECRET_KEY =`, so it reported "no hardcoded credentials"
# regardless of what was actually in the tree. These tests pin real detection.

_LEAK_SHAPES = {
    "upper-case vendor secret": 'BINANCE_API_SECRET = "A1b2C3d4E5f6A1b2C3d4E5f6A1b2C3d4E5f6A1b2C3d4E5f6A1b2C3d4E5f6A1b2"',
    "lower-case name": 'api_key = "zZ9yY8xX7wW6vV5uU4tT3sS2rR1qQ0pP"',
    "os.environ assignment": 'os.environ["BINANCE_TOKEN"] = "aB3dE5fG7hI9jK1lM3nO5pQ7rS9tU1vW"',
    "dict / mapping entry": 'CONFIG = {"secret_key": "QwErTyUiOpAsDfGhJkLzXcVbNm123456"}',
    "suffixed name": 'MY_PRIVATE_KEY_V2 = "1234567890abcdefghij1234567890abcdefghij"',
}

_SAFE_SHAPES = {
    "env lookup": 'API_KEY = os.getenv("BINANCE_API_KEY", "")',
    "documented placeholder": 'API_KEY = "YOUR_BINANCE_TESTNET_API_KEY_HERE"',
    "empty default": 'SECRET_KEY = ""',
    "short header name, not a value": 'API_KEY_HEADER = "X-API-KEY"',
    "obviously synthetic test key": 'CONTROL_API_KEY = "pytest-control-scope-key-not-a-real-credential"',
    "comment line": '# API_KEY = "A1b2C3d4E5f6A1b2C3d4E5f6A1b2C3d4E5f6A1b2C3d4E5f6A1b2C3d4E5f6A1b2"',
}


def _scan_source(tmp_path, source):
    probe = tmp_path / "probe_module.py"
    probe.write_text(source + "\n", encoding="utf-8")
    return _scan_file_for_credentials(str(probe))


@pytest.mark.parametrize("label", sorted(_LEAK_SHAPES))
def test_scanner_detects_realistic_hardcoded_credentials(tmp_path, label):
    findings = _scan_source(tmp_path, _LEAK_SHAPES[label])
    assert findings, f"Scanner missed a hardcoded credential ({label}): {_LEAK_SHAPES[label]}"


@pytest.mark.parametrize("label", sorted(_SAFE_SHAPES))
def test_scanner_does_not_flag_placeholders_and_env_lookups(tmp_path, label):
    findings = _scan_source(tmp_path, _SAFE_SHAPES[label])
    assert not findings, f"False positive on {label}: {findings}"


def test_dependency_trees_are_not_scanned(tmp_path):
    """Neither a known venv name nor an unrecognised one may be audited.

    ``_EXCLUDE_DIRS`` handles the conventional names; ``_is_third_party_dir``
    handles a virtualenv created under a name nobody thought to exclude.
    """
    leak = ('SECRET_KEY = "A1b2C3d4E5f6A1b2C3d4E5f6A1b2C3d4E5f6A1b2C3d4E5f6'
            'A1b2C3d4E5f6A1b2"\n')

    for env_name in (".venv", "my_unusual_env_name"):
        env = tmp_path / env_name
        (env / "lib" / "site-packages" / "flask").mkdir(parents=True)
        (env / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        (env / "lib" / "site-packages" / "flask" / "config.py").write_text(
            leak, encoding="utf-8")

        collected = []
        for root, dirs, files in os.walk(str(tmp_path)):
            dirs[:] = [
                d for d in dirs
                if d not in _EXCLUDE_DIRS
                and not _is_third_party_dir(os.path.join(root, d))
            ]
            collected.extend(os.path.join(root, f) for f in files)

        assert not any("site-packages" in p for p in collected), (
            f"Third-party code under {env_name}/ was scanned: {collected}"
        )
        assert not any(p.endswith("config.py") for p in collected), (
            f"{env_name}/lib/.../flask/config.py was scanned: {collected}"
        )

    # And the marker really is what prunes the unconventionally named env.
    assert _is_third_party_dir(str(tmp_path / "my_unusual_env_name")) is True
    assert _is_third_party_dir(str(tmp_path)) is False
