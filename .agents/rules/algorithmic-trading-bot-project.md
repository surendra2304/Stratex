# Algorithmic Trading Bot — Master Project Architecture & Design Rules

## 1. Project Identity & Repository Context
- **Project Name:** Algorithmic Trading Bot
- **Local Workspace Path:** `d:\MT5\python_bot`
- **Remote Repository:** `https://github.com/surendra2304/algorithmic-trading-bot.git`
- **Active Working Branch:** `master`
- **Backend Stack:** Python 3.11, Flask Web/API Server, Binance Testnet REST & WebSocket clients, Process Supervisor.
- **Frontend Stack:** Vanilla HTML5, CSS3, ES6 JavaScript (`app.js`, `apiClient.js`), Chart.js. Single Page Application with container toggling.
- **Core Architecture:** Real-time multi-asset market scanner, quantitative multi-strategy evaluation engine, profitability gate, risk engine, Binance testnet order execution, trade lifecycle ledger, and diagnostics.

---

## 2. Approved Information Architecture (Navigation Order)
The sidebar navigation order is **FIXED** and must strictly remain:
1. **Dashboard**
2. **Scanner**
3. **Positions**
4. **Trades**
5. **Markets**
6. **Strategies**
7. **Risk**
8. **Analytics**
9. **System**
10. **Settings**

*Rule:* Future agents MUST NOT rename, reorder, or remove these views without explicit user instruction.

---

## 3. Global Design System (Institutional Black Theme)
The application adheres strictly to the **Premium Black Institutional Quantitative Trading Terminal** design system.

### Color Palette:
- **Background (`--bg-base`):** `#05070B`
- **Sidebar (`--bg-sidebar`):** `#070A0F`
- **Header (`--bg-header`):** `#080C12`
- **Panel (`--bg-panel`):** `#0A0F16`
- **Elevated Panel (`--bg-panel-hover` / `--bg-subtle`):** `#111923`
- **Border (`--border-subtle` / `--border-medium`):** `#1D2A3A`
- **Border Active/Hover (`--border-active`):** `#2A3B52`
- **Primary Blue (`--accent-primary`):** `#3B82F6`
- **Bright Blue (`--accent-bright`):** `#60A5FA`
- **Cyan (`--accent-secondary`):** `#22D3EE`
- **Success / Profit (`--profit-green`):** `#22C55E`
- **Danger / Loss (`--loss-red`):** `#EF4444`
- **Warning (`--warning-amber`):** `#F59E0B`
- **Primary Text (`--text-primary`):** `#F8FAFC`
- **Secondary Text (`--text-secondary`):** `#A7B5C8`
- **Muted Text (`--text-muted`):** `#66758A`
- **Disabled Text (`--text-dim`):** `#3D4A5A`

*Rule:* No purple/pink accents, no neon glow, no excessive glassmorphism, no light-mode variants.

### Typography:
- **UI / Headings / Labels:** `Inter`, sans-serif
- **Financial / Technical Values (Prices, Quantities, PnL, %, IDs, Timestamps):** `JetBrains Mono`, monospace (with tabular figures enabled).

---

## 4. Layout & Structural Contracts

### Global Header:
- **Single-Line Desktop Bar:** Contains Bot Name (`⚡ ALGORITHMIC TRADING BOT`), Engine Status (`● ENGINE ONLINE`), Environment (`TESTNET`), Uptime counter, Real-time IST Clock, Global Notification Bell (`🔔`), and Sound Toggle (`🔊 ON`).
- *Constraint:* Engine, mode, and uptime belong here and in System — do NOT place them in the sidebar.

### Sidebar Bottom:
- **System Status Matrix:** Binance REST, WebSocket, Market Data, Execution, Strategy, Portfolio, Risk, Persistence.
- **Latency Readouts:** REST and WebSocket latency metrics.
- *Constraint:* Do NOT place Engine status, Uptime, or Heartbeat in the sidebar.

### Chart Standard:
- **Toolbar Position:** MUST be **ABOVE** the chart.
- **Timeframes:** `5m`, `15m`, `30m`, `1h`, `2h`, `4h`.
- **Dropdowns (Closed by Default):**
  - **Chart Types:** `Candlestick` (default), `Heikin Ashi`, `Bars`, `Line`, `Area`.
  - **Indicators Dropdown**
  - **Draw Dropdown**
  - **Fullscreen Toggle (`⛶`)**
- **Chart Overlays:** Signals, Trades (Entry, Exit), Current Price, SL, and TP render directly on the canvas. No permanent left panels for signals/search beside the chart.

### Popup Modal Contract:
- **Trigger:** Row click in Scanner, Positions, or Trades opens the modal.
- *Constraint:* NO separate Details or Chart icons on rows.
- **Layout:**
  - **LEFT:** Metadata, parameters, execution details, and rationale.
  - **RIGHT:** Large, readable, interactive chart (dominates modal width).

---

## 5. Detailed View Specifications

### 1. Dashboard (Command Center)
- Executive account snapshot: Total Account Value, Today's PnL, Realized PnL, Unrealized PnL.
- Compact opportunity scanner summary and latest open trades.
- *Constraint:* Must not duplicate full Scanner, Risk, or Analytics pages.

### 2. Scanner (Live Opportunity Evaluation)
- Single Filters dropdown at the TOP.
- Row click opens Signal Details popup explaining *why* the opportunity was accepted or rejected across Signal, Profitability, Risk, and Execution gates.
- *Removals (DO NOT ADD):* No Opportunity Pipeline, no Selected Signal panel, no Strategy column in main table.

### 3. Positions (Active Portfolio)
- Contains **ONLY OPEN POSITIONS**.
- Row click opens Position Details popup with live Entry, Current Price, SL, and TP overlays on the chart.

### 4. Trades (Trading Journal)
- Contains **ONLY CLOSED TRADES**.
- Top summary: Total Trades, Wins, Losses, Win Rate, Total Profit, Total Loss, Net PnL.
- Day-wise grouping (collapsed by default; click day to expand).
- Row format: `OPENED - CLOSED (HOLDING TIME)` using `-` (never arrows).
- Main row columns: `SYMBOL · TIMEFRAME · SIDE`, `ENTRY`, `EXIT`, `NET PNL`, `CLOSE REASON`.
- *Constraint:* No Strategy column on the main table row. Complete details belong inside the row-click popup.

### 5. Markets (Technical Chart Analysis)
- Chart is the main attraction (large viewport, zero clutter).
- Toolbar placed strictly above the chart with closed-by-default dropdowns.

### 6. Strategies (Quantitative Models)
- Main table: Strategy, Status, Active Timeframes, Evaluations, Signals, Trades, Win Rate (must include count, e.g., `83.3% (5/6)`).
- *Constraint:* **NO Profit/Loss** column in main strategy table. PnL belongs inside the Strategy Details popup.
- *Removals (DO NOT ADD):* No Strategy Performance History, no Selected Strategy card.

### 7. Risk (Risk Management & Guardrails)
- Explicit limit matrices showing: **MAX | USED | AVAILABLE** (showing both percentage and monetary values where applicable).
- Separate sections for Portfolio Exposure, Risk Per Trade, Daily Loss Limit, Max Drawdown, Open Positions, Risk Decisions, and Circuit Breakers.

### 8. Analytics (Historical Performance)
- Quantitative attribution: 1D, 7D, 30D, ALL filters.
- Equity growth curve, PnL breakdown, Drawdown depth chart, Strategy attribution, and Timeframe distribution.

### 9. System (Diagnostics & Infrastructure)
- Comprehensive technical diagnostics: Engine status (PID, uptime, restarts, heartbeat), Market Data integrity, Connectivity & Latency, Supervisor status, Persistence status, Recent System Events log, Diagnostics.
- **Deployment Status:** Localized strictly in System (Render container, UptimeRobot, Region, Commit hash).

### 10. Settings (Bot Control Center)
- Configuration matrices for Trade Limits, Risk Limits, Strategy toggles/timeframes, Profitability thresholds, Execution models, Protection/SL/TP parameters, Notifications, Sound, and Universe selection.
- **Manual Trading Mode (TESTNET ONLY):** Disabled by default (`○ OFF`). When enabled with explicit confirmation dialog, exposes `BUY`, `SELL`, `CLOSE POSITION`, `CLOSE BOT TRADE`, `CANCEL ORDER`.
- *Constraint:* Do NOT place Deployment in Settings.

---

## 6. Accounting & Financial Integrity
- **Authoritative Equity Formula:**
  $$\text{TOTAL EQUITY} = \text{USDT CASH} + \text{ACTIVE MANAGED CRYPTO MARKET VALUE}$$
- Realized and Unrealized PnL are mathematically partitioned and never double-counted into the base cash ledger.
- The backend API is the single source of truth for all accounting metrics.

---

## 7. Global Services (Singletons)
- **Notification Center:** Single global bell (`🔔`) in header. Events: Trade Opened/Closed, TP/SL hit, Order Failed, New Signal, Engine Offline/Recovered, Safety Halt.
- **Sound System:** Global header toggle (`🔊 ON/OFF`). Volume and testing controls in Settings. Respects master mute.

---

## 8. Permanent "DO NOT REINTRODUCE" List
Future agents must **NEVER** reintroduce any of the following removed items:
1. No Opportunity Pipeline on Scanner.
2. No Selected Signal panel on Scanner.
3. No duplicate bottom filters on Scanner.
4. No Details icon or Chart icon buttons on Scanner, Positions, or Trades tables.
5. No Strategy column in Trades main table.
6. No PnL in Strategies main table.
7. No Strategy Performance History section.
8. No Selected Strategy section.
9. No Deployment block in Settings.
10. No Engine/Uptime/Heartbeat in the sidebar.
11. No permanent side search/signals panels on Markets.

---

## 9. DIARY MAINTENANCE PROTOCOL
Before and after future development sessions:
1. **Before coding:** Read this rule file, read `DIARY.md`, inspect existing repository code, and maintain approved architecture.
2. **After implementation (Every session must update the diary):**
   - Determine today's date.
   - Open `diary/YYYY-MM-DD.md` (Create it if missing).
   - Add the detailed work completed during the session to the dated diary file.
   - Update the corresponding DAY section in `DIARY.md` (the master summary).
   - Include important bugs, fixes, decisions, tests, deployment state and commits.
   - Never skip diary updates for completed development sessions.
   - Never invent historical activity.
   - Never delete historical diary entries.
   - Follow `DIARY_SPEC.md` exactly.
   - Review diary changes before commit.
   - Commit diary changes to GitHub.
3. **Persistence:** Never overwrite historical diary entries or silently alter approved UI structures.
