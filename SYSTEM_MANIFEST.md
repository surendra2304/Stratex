# 🏛️ SYSTEM MANIFEST — Stratex 24/7 Algorithmic Trading Platform

> **Official Subsystem Name:** Stratex  
> **Role in Ecosystem:** 24/7 Automated Binance Futures Execution Engine & Live Trading Dashboard  
> **Repository:** [surendra2304/algorithmic-trading-bot](https://github.com/surendra2304/algorithmic-trading-bot) (Branch: master)  
> **Workspace Path:** d:\FRIDAY Universe\Stratex  

---

## ☁️ 1. Live Cloud Infrastructure & Deployment

| Attribute | Production Configuration |
| :--- | :--- |
| **Live Production URL** | [https://stratex-ucjz.onrender.com](https://stratex-ucjz.onrender.com) |
| **Health Check Endpoint** | https://stratex-ucjz.onrender.com/api/performance |
| **Master API Key Variable** | STRATEX_API_KEY=stratex_api |
| **Authentication Header** | Authorization: Bearer stratex_api / X-STRATEX-API-KEY: stratex_api |
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

# 1. ⚡ Inference AI Multi-Model Gateway (25 Keys)
INFERENCE_URL=https://inference-3i2b.onrender.com
INFERENCE_API_KEY=inference_api

# 2. 🧠 Memora Cloud Persistent Memory (9 GB Turso AWS Mumbai)
MEMORA_URL=https://memora-9zr9.onrender.com
MEMORA_API_KEY=memora_api

# 3. 📈 Stratex 24/7 Algorithmic Trading Platform (Binance Futures)
STRATEX_URL=https://stratex-ucjz.onrender.com
STRATEX_API_KEY=stratex_api

# 4. 🧠 IntelX Evidence & Intelligence Research Engine (Turso AWS Mumbai)
INTELX_URL=https://intelx-3cz1.onrender.com
INTELX_API_KEY=intelx_api

# 5. 🔮 Futuris Calibrated Predictive Forecasting Engine
FUTURIS_URL=https://futuris-x4f4.onrender.com
FUTURIS_API_KEY=futuris_api

# 6. 🌐 Cortex Autonomous Web Operations & Intelligence
CORTEX_URL=https://cortex-qifr.onrender.com
CORTEX_API_KEY=cortex_api

# 7. 🛠️ Forge Local Software Engineering Engine
FORGE_URL=http://localhost:8001
FORGE_API_KEY=forge_api

# 8. 🛡️ Sentinel Local Cybersecurity & Threat Defense Shield
SENTINEL_URL=http://localhost:8003
SENTINEL_API_KEY=sentinel_api

# 9. 🤖 FRIDAY Central Desktop Operating System
FRIDAY_URL=http://localhost:9000
FRIDAY_API_KEY=friday_api
`

---

## 🤖 4. Antigravity AI Session Guide

When opening this directory in **Antigravity AI**:
* **Identity:** You are working inside **Stratex** (d:\FRIDAY Universe\Stratex).
* **Live Service:** This service is deployed live at https://stratex-ucjz.onrender.com.
* **Authentication:** Incoming requests use STRATEX_API_KEY=stratex_api.
* **Never Fake Tests:** All tests and verifications must be executed against real code and real endpoints.
* **No Unapproved Git Pushes:** Keep modifications local unless explicitly instructed to push.
