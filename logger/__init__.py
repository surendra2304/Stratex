"""Logger package init."""
import os
import sys

try:
    _curr_dir = os.path.dirname(os.path.abspath(__file__))
    _root_log = os.path.join(os.path.dirname(_curr_dir), "logger.py")
    if os.path.exists(_root_log):
        with open(_root_log, "r", encoding="utf-8") as _f:
            exec(compile(_f.read(), _root_log, "exec"), globals())
except Exception:
    pass