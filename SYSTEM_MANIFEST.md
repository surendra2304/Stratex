# 🏛️ SYSTEM MANIFEST — Stratex Paper/Testnet Strategy Platform

> **Official Subsystem Name:** Stratex  
> **Role in Ecosystem:** Paper/Testnet Futures Strategy Service  
> **Repository:** [surendra2304/algorithmic-trading-bot](https://github.com/surendra2304/algorithmic-trading-bot) (Branch: main)  
> **Workspace Path:** d:\FRIDAY Universe\Stratex  

---

## ☁️ 1. Configured service

| Attribute | Repository configuration |
| :--- | :--- |
| **Configured Service URL** | [https://friday-universe.duckdns.org/stratex](https://friday-universe.duckdns.org/stratex) |
| **Internal Mesh Service URL** | `http://stratex:8000` |
| **Internal Port / Host Port** | Port 8000 (Host: 8008) |
| **Configured Health Check Endpoint** | `/health` (live process liveness response) |
| **API key variable (keep value in secret environment)** | `STRATEX_API_KEY` (unique secret required; value is not stored here) |
| **Authentication Header** | `Authorization: Bearer <STRATEX_API_KEY>` or `X-API-Key` |
| **Configured database topology** | SQLite Trade Ledger / State Backups / Connected to Memora |
| **Configured database URL or namespace** | sqlite+aiosqlite:///./data/trades.db |
| **Hosting Platform** | Oracle Cloud Always Free (friday_mesh Docker bridge, Caddy HTTPS reverse proxy) |

---

## 🎯 2. Purpose & Responsibilities

### What Stratex IS:
* Stratex is the paper/testnet strategy and risk-analysis service. The configured executable strategy set and runtime dependencies are verified from current evidence. Live-money orders remain blocked in source.

### What Stratex DOES:
* Operates as the **Paper/Testnet Futures Strategy Service** within the 9-agent FRIDAY Universe.
* Communicates directly with peer agents via authenticated REST and WebSocket protocols across `friday_mesh`.
* Persists private long-term memory records to **Memora** under memora://stratex/private.

---

## 🌐 3. Ecosystem endpoint configuration

These variable names and URLs represent the unified Oracle Cloud container mesh topology:

```env
# ============================================================================== #
#               FRIDAY UNIVERSE MASTER ECOSYSTEM CONFIGURATION                  #
# ============================================================================== #

# 1. ⚡ Inference AI Multi-Model Gateway
INFERENCE_URL=https://friday-universe.duckdns.org/cortex
# Set INFERENCE_API_KEY to the unique key issued by the Inference service.

# 2. Memora cloud memory service
MEMORA_URL=https://friday-universe.duckdns.org/memora
# Set MEMORA_API_KEY to the unique key issued by the Memora service.

# 3. 📈 Stratex Paper/Testnet Strategy Platform (Binance Futures)
STRATEX_URL=https://friday-universe.duckdns.org/stratex
# Set STRATEX_API_KEY to the unique key issued by the Stratex service.

# 4. IntelX research service
INTELX_URL=https://friday-universe.duckdns.org/intelx
# Set INTELX_API_KEY to the unique key issued by the IntelX service.

# 5. 🔮 Futuris Calibrated Predictive Forecasting Engine
FUTURIS_URL=https://friday-universe.duckdns.org/futuris
# Set FUTURIS_API_KEY to the unique key issued by the Futuris service.

# 6. 🌐 Cortex Autonomous Web Operations & Intelligence
CORTEX_URL=https://friday-universe.duckdns.org/cortex
# Set CORTEX_API_KEY to the unique key issued by the Cortex service.

# 7. 🛠️ Forge Local Software Engineering Engine
FORGE_URL=https://friday-universe.duckdns.org/forge
# Set FORGE_API_KEY to the unique key issued by Forge.

# 8. 🛡️ Sentinel Local Cybersecurity & Threat Defense Shield
SENTINEL_URL=https://friday-universe.duckdns.org/sentinel
# Set SENTINEL_API_KEY to the unique key issued by Sentinel.

# 9. 🤖 FRIDAY Central Desktop Operating System
FRIDAY_URL=https://friday-universe.duckdns.org/friday
# Set FRIDAY_API_KEY to the unique key issued by FRIDAY.
```

---

## 🤖 4. Repository guide

When opening this repository:
* **Identity:** You are working inside **Stratex** (d:\FRIDAY Universe\Stratex).
* **Configured Service URL:** https://friday-universe.duckdns.org/stratex
* **Internal Mesh:** http://stratex:8000
* **Authentication:** Incoming requests require a unique STRATEX_API_KEY configured in the service environment.
* **Test Evidence:** Label local tests, test doubles, and live endpoint checks separately; report only commands and results actually observed.
