# 🏛️ SYSTEM MANIFEST — Stratex Paper/Testnet Strategy Platform

> **Official Subsystem Name:** Stratex  
> **Role in Ecosystem:** Paper/Testnet Futures Strategy Service
> **Repository:** [surendra2304/algorithmic-trading-bot](https://github.com/surendra2304/algorithmic-trading-bot) (Branch: main)
> **Workspace Path:** d:\FRIDAY Universe\Stratex  

---

## ☁️ 1. Configured service (runtime unverified)

| Attribute | Repository configuration |
| :--- | :--- |
| **Configured Service URL (deployment unverified)** | [https://stratex-8wj1.onrender.com](https://stratex-8wj1.onrender.com) |
| **Configured Health Check Endpoint** | `/health` (live response/revision must be verified separately) |
| **API key variable (keep value in secret environment)** | `STRATEX_API_KEY` (unique secret required; value is not stored here) |
| **Authentication Header** | `Authorization: Bearer <STRATEX_API_KEY>` or `X-API-Key` |
| **Configured database topology (runtime unverified)** | SQLite Trade Ledger / State Backups / Connected to Memora |
| **Configured database URL or namespace (not a secret)** | sqlite+aiosqlite:///./data/trades.db |
| **Configured host (plan, region, and runtime unverified)** | Render service configured (current plan, region, and deployment unverified) |

---

## 🎯 2. Purpose & Responsibilities

### What Stratex IS:
* Stratex is the paper/testnet strategy and risk-analysis service. The configured executable strategy set and runtime dependencies must be verified from current evidence. Live-money orders remain blocked in source.

### What Stratex DOES:
* Operates as the **Paper/Testnet Futures Strategy Service** within the 9-agent FRIDAY Universe.
* Communicates directly with peer agents via authenticated REST and WebSocket protocols.
* Persists private long-term memory records to **Memora** under memora://stratex/private.

---

## 🌐 3. Ecosystem endpoint configuration

These variable names and URLs are references only; they do not prove live communication. Set real credentials in secret environments.

```env
# ============================================================================== #
#               FRIDAY UNIVERSE MASTER ECOSYSTEM CONFIGURATION                  #
# ============================================================================== #

# 1. ⚡ Inference AI Multi-Model Gateway
INFERENCE_URL=https://inference-r1sn.onrender.com
# Set INFERENCE_API_KEY to the unique key issued by the Inference service.

# 2. Memora cloud memory service (active backend/capacity not verified)
MEMORA_URL=https://memora-cavc.onrender.com
# Set MEMORA_API_KEY to the unique key issued by the Memora service.

# 3. 📈 Stratex Paper/Testnet Strategy Platform (Binance Futures)
STRATEX_URL=https://stratex-8wj1.onrender.com
# Set STRATEX_API_KEY to the unique key issued by the Stratex service.

# 4. IntelX research service (active storage backend not verified)
INTELX_URL=https://intelx-mygl.onrender.com
# Set INTELX_API_KEY to the unique key issued by the IntelX service.

# 5. 🔮 Futuris Calibrated Predictive Forecasting Engine
FUTURIS_URL=https://futuris-th6f.onrender.com
# Set FUTURIS_API_KEY to the unique key issued by the Futuris service.

# 6. 🌐 Cortex Autonomous Web Operations & Intelligence
CORTEX_URL=https://cortex-0m7c.onrender.com
# Set CORTEX_API_KEY to the unique key issued by the Cortex service.

# 7. 🛠️ Forge Local Software Engineering Engine
FORGE_URL=https://forge-e9kl.onrender.com
# Set FORGE_API_KEY to the unique key issued by Forge.

# 8. 🛡️ Sentinel Local Cybersecurity & Threat Defense Shield
SENTINEL_URL=https://sentinel-a861.onrender.com
# Set SENTINEL_API_KEY to the unique key issued by Sentinel.

# 9. 🤖 FRIDAY Central Desktop Operating System
FRIDAY_URL=https://friday-zw59.onrender.com
# Set FRIDAY_API_KEY to the unique key issued by FRIDAY.
```

---

## 🤖 4. Repository guide

When opening this repository:
* **Identity:** You are working inside **Stratex** (d:\FRIDAY Universe\Stratex).
* **Configured Service URL:** https://stratex-8wj1.onrender.com. A URL in this manifest does not prove current deployment or health.
* **Authentication:** Incoming requests require a unique STRATEX_API_KEY configured in the service environment.
* **Test Evidence:** Label local tests, test doubles, and live endpoint checks separately; report only commands and results actually observed.
* **No Unapproved Git Pushes:** Keep modifications local unless explicitly instructed to push.
