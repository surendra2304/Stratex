# 📜 BOT DIARY SPECIFICATION & PERMANENT MAINTENANCE PROTOCOL

**Document Version**: 1.0.0  
**Effective Date**: 14 August 2026  
**Scope**: All current and future development, auditing, and maintenance of **Algorithmic Trading Bot**.

---

## 1. Core Principles

1. **Chronological & Never-Ending**:
   - The diary begins on **14 August 2026** and continues forever.
   - Every development session must append to the existing history.
   - No end date shall ever be applied.
2. **Append-Only & Immutable Historical Days**:
   - Past daily records are frozen historical artifacts.
   - Once a day has concluded, its historical section must **never be silently altered or rewritten**.
3. **Additive Corrections & Errata**:
   - If an earlier historical claim is later discovered to be erroneous, flawed, or synthetic, the original entry is preserved as what was reported at that time, and a dedicated **CORRECTION / ERRATUM** block is appended explaining:
     - The previous claim
     - The exact root cause of the error/misreport
     - The verified final state backed by cryptographic or exchange evidence.
4. **Authoritative Evidence Priority**:
   - **Tier 1 (Highest)**: Live Binance Testnet REST API queries (`get_my_trades`, `get_account`, `get_open_orders`).
   - **Tier 2**: Git commit ledger and repository contents (`git log --all --date-order`).
   - **Tier 3**: Render runtime/deployment logs and health endpoints.
   - **Tier 4**: Automated test suite execution reports (`pytest`).
   - **Tier 5**: Historical project reports and markdown records.
   - **Tier 6 (Lowest)**: Unverified local assertions (must be explicitly labeled `UNVERIFIED`).
5. **Strict Bug Numbering**:
   - Bug IDs are permanent global sequence numbers (`Bug #01`, `Bug #02`, ..., `Bug #32`, `Bug #33`, ...).
   - Bug numbers must **never be reused, deleted, or reordered**.
6. **No Synthetic / Fabricated Data in Production**:
   - Only trades backed by verified Binance order IDs and exchange fill receipts may be recorded as completed trades.
   - Any synthetic simulation data must be explicitly labeled `TEST / PAPER / SYNTHETIC` and excluded from production ledgers.
7. **Future Events Must Remain Plans**:
   - Never document future milestones or roadmap items as completed events until code has been committed, tested, and deployed.

---

## 2. File Organization

```
/
├── BOT_DIARY.md              # Master chronological index and consolidated project history
├── DIARY_SPEC.md             # This permanent maintenance specification
├── diary/                    # Day-by-day detailed raw chronicle logs
│   ├── 2026-08-14.md
│   ├── 2026-08-15.md
│   ├── 2026-08-16.md
│   ├── 2026-08-17.md
│   └── 2026-08-18.md
└── scripts/
    └── update_bot_diary.py   # Automated helper script to append and validate diary entries
```

---

## 3. Standard Daily Entry Schema

Every daily entry in `diary/YYYY-MM-DD.md` and `BOT_DIARY.md` must adhere to the following standardized section structure:

1. `## Daily Summary`
2. `## User Directives / Requirements`
3. `## Work Performed`
4. `## Architecture / Structure Changes`
5. `## Files Created`
6. `## Files Modified`
7. `## Files Deleted`
8. `## Strategies / Trading Logic Changes`
9. `## Market / Data Pipeline Changes`
10. `## Execution / Order Changes`
11. `## Risk / Profitability Changes`
12. `## Accounting / PnL Changes`
13. `## Dashboard / UI Changes`
14. `## Deployment / Infrastructure Changes`
15. `## Tests Performed & Test Results`
16. `## Bugs / Errors Discovered` (with Bug #ID, Symptoms, Root Cause, Fix, Commit, Verification)
17. `## Important Decisions`
18. `## Incidents / Misconfigurations`
19. `## Corrections to Earlier Information`
20. `## Git Commits` (SHA, Message, Purpose)
21. `## Render / Cloud Events`
22. `## Binance Testnet Events`
23. `## Current End-of-Day State` (Verified metrics)
24. `## Next Planned Work`
