"""Execution package init."""
import os
import sys
import importlib.util

# Ensure execution package shares exact same module object
try:
    _curr_dir = os.path.dirname(os.path.abspath(__file__))
    _root_dir = os.path.dirname(_curr_dir)
    _exec_file = os.path.join(_root_dir, "execution.py")
    if os.path.exists(_exec_file):
        with open(_exec_file, "r", encoding="utf-8") as _f:
            _code = _f.read()
        exec(compile(_code, _exec_file, 'exec'), globals())
except Exception as _err:
    pass

