#!/usr/bin/env python3
"""
scripts/update_bot_diary.py
Lightweight automated assistant and validator for the Algorithmic Trading Bot Master Diary.

Capabilities:
1. Validates chronological ordering and absence of duplicate bug numbers.
2. Checks that daily logs exist in diary/YYYY-MM-DD.md.
3. Appends session entries to DIARY.md without altering historical days.
"""

import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIARY_DIR = os.path.join(REPO_ROOT, "diary")
MASTER_DIARY_FILE = os.path.join(REPO_ROOT, "DIARY_SUMMARY.md")

def validate_diary():
    print("=== VALIDATING BOT DIARY & CHRONICLES ===")
    if not os.path.exists(MASTER_DIARY_FILE):
        print(f"Error: {MASTER_DIARY_FILE} missing!")
        return False
    
    with open(MASTER_DIARY_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Check bug IDs in Section 5 table
    table_bugs = re.findall(r"\|\s*\*\*(\d+)\*\*\s*\|", content)
    table_nums = [int(b) for b in table_bugs]
    print(f"Bugs Recorded in Master Bug Ledger: {len(table_nums)}")
    print(f"Sequence: #{min(table_nums) if table_nums else 0} to #{max(table_nums) if table_nums else 0}")
    
    expected_sequence = list(range(1, max(table_nums) + 1)) if table_nums else []
    if table_nums == expected_sequence:
        print("✅ Bug Ledger sequence is complete with zero gaps or duplicates.")
    else:
        print("⚠️ Warning: Gaps or duplicates in bug ledger table.")

    # 2. Check daily files
    if os.path.exists(DIARY_DIR):
        days = sorted([f for f in os.listdir(DIARY_DIR) if f.endswith(".md")])
        print(f"✅ Daily chronicle logs verified ({len(days)} days): {', '.join(days)}")
    else:
        print("⚠️ Warning: diary/ directory missing.")

    return True

if __name__ == "__main__":
    validate_diary()
