# 🏛️ SYSTEM MANIFEST — Stratex 24/7 Algorithmic Trading Platform

> **Official Subsystem Name:** Stratex  
> **Role in Ecosystem:** 24/7 Automated Binance Futures Execution Engine & Live Trading Dashboard  
> **Repository:** [surendra2304/algorithmic-trading-bot](https://github.com/surendra2304/algorithmic-trading-bot) (Branch: master)  
> **Workspace Path:** d:\FRIDAY Universe\Stratex  

---

## ☁️ 1. Live Cloud Infrastructure & Deployment

| Attribute | Production Configuration |
| :--- | :--- |
| **Live Production URL** | [https://stratex-8wj1.onrender.com](https://stratex-8wj1.onrender.com) |
| **Configured Health Check Endpoint** | `/health` (live response/revision must be verified separately) |
| **Master API Key Variable** | `STRATEX_API_KEY` (unique secret required; value is not stored here) |
| **Authentication Header** | `Authorization: Bearer <STRATEX_API_KEY>` or `X-API-Key` |
| **Database Topology** | SQLite Trade Ledger / State Backups / Connected to Memora |
| **Database Connection** | sqlite+aiosqlite:///./data/trades.db |
| **Hosting Platform** | Render Docker Web Service (Singapore / AWS Mumbai) |

---

## 🎯 2. Purpose & Responsibilities

### What Stratex IS:
* Stratex is the 24/7 automated cryptocurrency futures execution platform. It runs 16 quant strategies, manages risk overlays, features a full web dashboard, and connects to Inference, Memora, IntelX, and Futuris.

### What Stratex DOES:
* Operates as the **24/7 Automated Binance Futures Execution Engine & Live Trading Dashboard** within the 9-agent FRIDAY Universe.
* Communicates directly with peer agents via authenticated REST and WebSocket protocols.
* Persists private long-term memory records to **Memora** under memora://stratex/private.

---

## 🌐 3. Full Ecosystem Network Connectivity

Every agent in the universe communicates using standard environment variables:

`env
# ============================================================================== #
#               FRIDAY UNIVERSE MASTER ECOSYSTEM CONFIGURATION                  #
# ============================================================================== #

# 1. ⚡ Inference AI Multi-Model Gateway
INFERENCE_URL=https://inference-r1sn.onrender.com
# Set INFERENCE_API_KEY to the unique key issued by the Inference service.

# 2. 🧠 Memora Cloud Persistent Memory (9 GB Turso AWS Mumbai)
MEMORA_URL=https://memora-cavc.onrender.com
# Set MEMORA_API_KEY to the unique key issued by the Memora service.

# 3. 📈 Stratex 24/7 Algorithmic Trading Platform (Binance Futures)
STRATEX_URL=https://stratex-8wj1.onrender.com
# Set STRATEX_API_KEY to the unique key issued by the Stratex service.

# 4. 🧠 IntelX Evidence & Intelligence Research Engine (Turso AWS Mumbai)
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
`

---

## 🤖 4. Antigravity AI Session Guide

When opening this directory in **Antigravity AI**:
* **Identity:** You are working inside **Stratex** (d:\FRIDAY Universe\Stratex).
* **Configured Service URL:** https://stratex-8wj1.onrender.com. A URL in this manifest does not prove current deployment or health.
* **Authentication:** Incoming requests require a unique STRATEX_API_KEY configured in the service environment.
* **Test Evidence:** Label local tests, test doubles, and live endpoint checks separately; report only commands and results actually observed.
* **No Unapproved Git Pushes:** Keep modifications local unless explicitly instructed to push.
