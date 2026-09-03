# Stratex Portfolio Intelligence & Quantitative Analytics Architecture

## 1. Overview

This subsystem incorporates institutional portfolio theory, factor research, conditional volatility modeling, and performance analytics into the **STRATEX** quantitative trading platform.

The implementation synthesizes core architectural concepts from:
1. **Riskfolio-Lib / skfolio**: Convex optimization for minimum variance and risk parity portfolios.
2. **Alphalens**: Information coefficient (IC), rank IC, and quantile forward return attribution.
3. **ARCH**: Autoregressive Conditional Heteroskedasticity (GARCH-family) volatility forecasting.
4. **QuantStats**: Standardized institutional performance metrics (Sharpe, Sortino, Calmar, max drawdown).
5. **Hummingbot / LEAN**: Multi-stage Alpha $\to$ Portfolio $\to$ Risk Overlay $\to$ Execution pipeline.

---

## 2. Integrated Mathematical Models

### A. Portfolio Optimization ([`stratex_more_integrations/portfolio.py`](file:///d:/FRIDAY%20Universe/Stratex/stratex_more_integrations/portfolio.py))

#### 1. Minimum Variance Formulation
Given an estimated covariance matrix $\mathbf{\Sigma} \in \mathbb{R}^{n \times n}$, the minimum variance portfolio solves:
$$\min_{\mathbf{w}} \mathbf{w}^T \mathbf{\Sigma} \mathbf{w}$$
$$\text{subject to} \quad \sum_{i=1}^n w_i = w_{\text{gross}}, \quad w_{\min} \le w_i \le w_{\max}, \quad \|\mathbf{w} - \mathbf{w}_{\text{prev}}\|_1 \le \tau_{\max}$$

When `cvxpy` is available, this problem is solved using the `CLARABEL` interior-point cone solver. When unavailable, a deterministic inverse-volatility analytical fallback is computed:
$$w_i = \frac{\sigma_i^{-1}}{\sum_{j=1}^n \sigma_j^{-1}} \cdot w_{\text{gross}}$$

#### 2. Risk Parity Formulation
Allocates capital such that each asset contributes proportionally to portfolio risk:
$$w_i \propto \frac{1}{\sigma_i}$$
bounded by $w_{\min} \le w_i \le w_{\max}$.

---

### B. Factor Quality Diagnostics ([`stratex_more_integrations/factors.py`](file:///d:/FRIDAY%20Universe/Stratex/stratex_more_integrations/factors.py))

#### 1. Information Coefficient (IC)
Measures the correlation between alpha factor values and forward returns:
$$\text{IC} = \text{Corr}(f_t, r_{t+1})$$
$$\text{Rank IC} = \text{Corr}(\text{Rank}(f_t), \text{Rank}(r_{t+1}))$$

#### 2. Quantile Attribution & Monotonic Spread
Sorts factor values into $q$ quantiles and measures the monotonic forward return spread:
$$\Delta_{\text{spread}} = \bar{r}_{Q_q} - \bar{r}_{Q_1}$$
A healthy alpha factor demonstrates $\text{Rank IC} > 0.03$ and $\Delta_{\text{spread}} > 0$.

---

### C. Conditional Volatility Forecasting ([`stratex_more_integrations/volatility.py`](file:///d:/FRIDAY%20Universe/Stratex/stratex_more_integrations/volatility.py))

Fits a GARCH(1,1)-$t$ model for asset returns $r_t$:
$$r_t = \mu + \epsilon_t, \quad \epsilon_t = \sigma_t z_t, \quad z_t \sim t_\nu$$
$$\sigma_t^2 = \omega + \alpha \epsilon_{t-1}^2 + \beta \sigma_{t-1}^2$$
When `arch` package fitting fails or sample size is $< 50$, the system automatically engages an Exponentially Weighted Moving Average (EWMA) fallback:
$$\sigma_t^2 = \lambda \sigma_{t-1}^2 + (1 - \lambda) r_t^2$$

---

### D. Institutional Performance Analytics ([`stratex_more_integrations/performance.py`](file:///d:/FRIDAY%20Universe/Stratex/stratex_more_integrations/performance.py))

- **Sharpe Ratio**: $\frac{\mathbb{E}[r] - r_f}{\sigma_{\text{ann}}}$
- **Sortino Ratio**: $\frac{\mathbb{E}[r] - r_f}{\sigma_{\text{downside}}}$
- **Calmar Ratio**: $\frac{\text{CAGR}}{|\text{Max Drawdown}|}$
- **Drawdown Series**: $D_t = \frac{E_t}{\max_{s \le t} E_s} - 1$

---

### E. Portfolio Risk Overlay ([`stratex_more_integrations/risk_overlay.py`](file:///d:/FRIDAY%20Universe/Stratex/stratex_more_integrations/risk_overlay.py))

Before execution targets reach the authoritative Stratex `RiskGate`:
1. **Single-Asset Cap**: $|w_i| \le w_{\text{single\_max}}$
2. **Gross Exposure Cap**: $\sum |w_i| \le w_{\text{gross\_max}}$
3. **Correlation Penalty**: For pairs with correlation $\rho_{ij} > 0.7$, weights are scaled by:
   $$w_i \leftarrow w_i \cdot \frac{1}{1 + \bar{\rho}_i}$$
4. **Non-Finite Value Guard**: Raises `ValueError` on non-finite (NaN, $\pm\infty$) allocations.

---

## 3. Operational REST APIs

| Endpoint | Method | Description |
| :--- | :---: | :--- |
| `/api/portfolio-optimization` | GET | Returns Minimum Variance, Risk Parity, and Risk-Overlaid weights |
| `/api/volatility-forecast` | GET | Returns 1-step forward GARCH / EWMA conditional volatility forecast |
| `/api/factor-diagnostics` | GET | Returns composite factor IC, rank IC, and quantile return tables |
| `/api/performance-analytics` | GET | Returns institutional Sharpe, Sortino, Calmar, and max drawdown |

---

## 4. Safety Guarantees

1. **Simulation-Only PAPER Mode**: Optimization and analytics layers do not issue live or paper exchange orders directly.
2. **Hard-Blocked LIVE Mode**: `ExecutionPolicy.can_place_order()` strictly maintains `LIVE_FORBIDDEN_BY_DESIGN`.
3. **Authoritative Gates**: Stratex `ProfitabilityGate`, `RiskGate`, and `PositionProtection` maintain absolute authority over all order routing.
