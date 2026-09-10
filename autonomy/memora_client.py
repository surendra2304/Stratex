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
    import sqlite3
    import uuid
    import time
    from typing import Optional, Dict, Any, List

    class MemoraClient:
        def __init__(self):
            self.local_db_path = "d:/FRIDAY Universe/Memora/data/memora.db"

        def learn_from_outcome(self, agent_name: str, task_name: str, status: str, error_log: Optional[str] = None, actions_taken: Optional[str] = None, context: Optional[str] = None, domain: Optional[str] = None):
            if not os.path.exists(self.local_db_path):
                return {"status": "error", "message": "no db"}
            try:
                with sqlite3.connect(self.local_db_path, timeout=5.0) as conn:
                    c = conn.cursor()
                    c.execute("SELECT id FROM agents WHERE name = 'stratex'")
                    row = c.fetchone()
                    aid = row[0] if row else str(uuid.uuid4())
                    c.execute("SELECT id FROM namespaces WHERE agent_id = ?", (aid,))
                    row_ns = c.fetchone()
                    nid = row_ns[0] if row_ns else str(uuid.uuid4())
                    now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
                    dom = domain or "quantitative_trading"
                    if status.lower() in ("failure", "error", "veto"):
                        content = f"[TRADING RISK RULE in '{dom}'] Strategy: {task_name}. Regime: {context or 'unknown'}. {error_log or 'Underperformed baseline'}."
                    else:
                        content = f"[PROVEN ALPHA PATTERN in '{dom}'] Strategy: {task_name}. Regime: {context or 'unknown'}. High win rate validated."
                    mid = str(uuid.uuid4())
                    c.execute("""
                        INSERT INTO memory_records (id, namespace_id, owner_id, memory_type, content_text, source, confidence, importance, lifecycle_state, tenant_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (mid, nid, aid, "experience", content, "agent:stratex", 1.0, 0.99, "active", "default", now_iso))
                    conn.commit()
                return {"status": "success", "id": mid, "memory_type": "experience", "content": content}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        def recall_experience(self, agent_name: str, task_query: str, domain: Optional[str] = None, limit: int = 5):
            return []

        def build_self_upgrade_context(self, agent_name: str, task_query: str, domain: Optional[str] = None) -> str:
            return ""

    memora_client = MemoraClient()

__all__ = ["MemoraClient", "memora_client"]
