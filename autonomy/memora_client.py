"""
Universal Memora Client for Stratex Quantitative Trading Autonomy
"""
import os
import sys
from pathlib import Path

# Try importing from central Memora SDK first
try:
    MEMORA_ROOT = Path("d:/FRIDAY Universe/Memora")
    if str(MEMORA_ROOT) not in sys.path:
        sys.path.insert(0, str(MEMORA_ROOT))
    from sdk.memora_client import MemoraClient, memora_client
except Exception:
    from .memora_cloud_fallback import MemoraClient, memora_client

__all__ = ["MemoraClient", "memora_client"]
