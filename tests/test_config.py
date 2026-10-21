import ast
from pathlib import Path

import pytest

import config


def test_module_level_config_constants_are_not_shadowed():
    """Duplicate top-level assignments silently override earlier defaults."""
    source = Path(config.__file__).read_text(encoding="utf-8")
    syntax_tree = ast.parse(source, filename=config.__file__)
    assignment_counts = {}

    for node in syntax_tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                assignment_counts[target.id] = assignment_counts.get(target.id, 0) + 1

    duplicates = {
        name: count
        for name, count in assignment_counts.items()
        if count > 1
    }
    assert duplicates == {}, f"Duplicate module-level config constants: {duplicates}"


def test_valid_config():
    """Test that the current config passes validation."""
    try:
        config.validate_config()
    except ValueError as e:
        pytest.fail(f"validate_config() raised ValueError unexpectedly: {e}")


def test_invalid_strategy(monkeypatch):
    monkeypatch.setattr(config, "ACTIVE_STRATEGY", "invalid_strat")
    with pytest.raises(ValueError, match="Invalid ACTIVE_STRATEGY"):
        config.validate_config()


def test_invalid_qty(monkeypatch):
    monkeypatch.setattr(config, "TRADE_QTY", -1.0)
    with pytest.raises(ValueError, match="TRADE_QTY must be a positive number"):
        config.validate_config()


def test_invalid_timeframe(monkeypatch):
    monkeypatch.setattr(config, "TIMEFRAME", "2m") # Invalid
    with pytest.raises(ValueError, match="Invalid TIMEFRAME"):
        config.validate_config()
