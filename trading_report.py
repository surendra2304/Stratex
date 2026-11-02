"""
trading_report.py — Realized-performance analytics for the Stratex trade ledger.

Single source of truth for every performance number Stratex reports (Telegram,
dashboards, daily summaries). Everything here is derived from the append-only
ledger written by the execution engine:

    testnet_trade_ledger.jsonl

Record shape (one closed round-trip per line):
    symbol, action, direction, quantity, entry_price, exit_price,
    gross_pnl, pnl, net_pnl, fees, timestamp, exit_timestamp,
    exit_reason, strategy, source

No synthetic data, no fills: unreadable lines are skipped and counted.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
from typing import Any, Iterable

DEFAULT_LEDGER = os.getenv("TESTNET_LEDGER_FILE", "testnet_trade_ledger.jsonl")

# Administrative / scratch sources that must never enter performance stats.
_NON_TRADING_SOURCES = {"TEST", "SCRATCH", "RECOVERED"}


# ---------------------------------------------------------------------------
# Ledger loading
# ---------------------------------------------------------------------------
def _parse_ts(value: Any) -> _dt.datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return _dt.datetime.utcfromtimestamp(float(value))
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = _dt.datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return dt


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def load_trades(ledger_path: str | None = None) -> list[dict[str, Any]]:
    """Return normalized closed trades, oldest first."""
    path = ledger_path or DEFAULT_LEDGER
    if not os.path.exists(path):
        return []

    trades: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            if str(raw.get("source", "")).upper() in _NON_TRADING_SOURCES:
                continue
            if str(raw.get("strategy", "")).upper() == "TEST":
                continue

            net = raw.get("net_pnl")
            if net is None:
                net = _finite(raw.get("pnl"), 0.0) - _finite(raw.get("fees"), 0.0)
            closed_at = _parse_ts(raw.get("exit_timestamp") or raw.get("timestamp"))

            trades.append(
                {
                    "symbol": str(raw.get("symbol", "")).upper() or "UNKNOWN",
                    "side": str(raw.get("direction") or raw.get("action") or "").upper() or "LONG",
                    "strategy": str(raw.get("strategy", "unknown")).lower(),
                    "quantity": _finite(raw.get("quantity")),
                    "entry_price": _finite(raw.get("entry_price")),
                    "exit_price": _finite(raw.get("exit_price")),
                    "gross_pnl": _finite(raw.get("gross_pnl"), _finite(raw.get("pnl"))),
                    "fees": abs(_finite(raw.get("fees"))),
                    "net_pnl": _finite(net),
                    "exit_reason": str(raw.get("exit_reason", "")).upper(),
                    "closed_at": closed_at,
                    "source": str(raw.get("source", "")),
                }
            )

    trades.sort(key=lambda t: t["closed_at"] or _dt.datetime.min)
    return trades


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _streaks(pnls: Iterable[float]) -> tuple[int, int]:
    best = worst = 0
    cur_win = cur_loss = 0
    for pnl in pnls:
        if pnl > 0:
            cur_win += 1
            cur_loss = 0
        elif pnl < 0:
            cur_loss += 1
            cur_win = 0
        else:
            continue
        best = max(best, cur_win)
        worst = max(worst, cur_loss)
    return best, worst


def _max_drawdown(equity: list[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def compute_metrics(
    trades: list[dict[str, Any]],
    *,
    starting_balance: float = 10000.0,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Aggregate performance statistics over a trade list."""
    now = now or _dt.datetime.utcnow()
    pnls = [t["net_pnl"] for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakeven = len(pnls) - len(wins) - len(losses)

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)
    fees = sum(t["fees"] for t in trades)

    win_rate = len(wins) / len(pnls) if pnls else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = net / len(pnls) if pnls else 0.0

    equity = [starting_balance]
    for pnl in pnls:
        equity.append(equity[-1] + pnl)

    # Per-trade Sharpe (annualisation is meaningless at this sample size).
    sharpe = 0.0
    if len(pnls) > 1:
        mean = net / len(pnls)
        var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(var)
        if std > 0:
            sharpe = mean / std

    best_streak, worst_streak = _streaks(pnls)

    today = now.date()
    week_start = today - _dt.timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    def _sum_since(cutoff_date: _dt.date) -> tuple[float, int]:
        total = 0.0
        count = 0
        for trade in trades:
            ts = trade.get("closed_at")
            if ts and ts.date() >= cutoff_date:
                total += trade["net_pnl"]
                count += 1
        return round(total, 2), count

    today_pnl, today_trades = _sum_since(today)
    week_pnl, week_trades = _sum_since(week_start)
    month_pnl, month_trades = _sum_since(month_start)

    by_strategy: dict[str, dict[str, Any]] = {}
    for trade in trades:
        bucket = by_strategy.setdefault(trade["strategy"], {"trades": 0, "wins": 0, "net_pnl": 0.0})
        bucket["trades"] += 1
        bucket["wins"] += 1 if trade["net_pnl"] > 0 else 0
        bucket["net_pnl"] = round(bucket["net_pnl"] + trade["net_pnl"], 2)
    for bucket in by_strategy.values():
        bucket["win_rate"] = round(bucket["wins"] / bucket["trades"], 4) if bucket["trades"] else 0.0
    by_strategy = dict(sorted(by_strategy.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True))

    by_symbol: dict[str, dict[str, Any]] = {}
    for trade in trades:
        bucket = by_symbol.setdefault(trade["symbol"], {"trades": 0, "wins": 0, "net_pnl": 0.0})
        bucket["trades"] += 1
        bucket["wins"] += 1 if trade["net_pnl"] > 0 else 0
        bucket["net_pnl"] = round(bucket["net_pnl"] + trade["net_pnl"], 2)
    for bucket in by_symbol.values():
        bucket["win_rate"] = round(bucket["wins"] / bucket["trades"], 4) if bucket["trades"] else 0.0
    by_symbol = dict(sorted(by_symbol.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True))

    return {
        "generated_at": now,
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": breakeven,
        "win_rate": round(win_rate, 4),
        "net_pnl": round(net, 2),
        "gross_pnl": round(sum(t["gross_pnl"] for t in trades), 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "fees": round(fees, 2),
        "profit_factor": round(profit_factor, 2) if math.isfinite(profit_factor) else None,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 4),
        "sharpe_per_trade": round(sharpe, 3),
        "max_drawdown_pct": round(_max_drawdown(equity) * 100, 2),
        "best_streak": best_streak,
        "worst_streak": worst_streak,
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "starting_balance": round(starting_balance, 2),
        "ending_equity": round(equity[-1], 2),
        "return_pct": round((equity[-1] - starting_balance) / starting_balance * 100, 2) if starting_balance else 0.0,
        "today_pnl": today_pnl,
        "today_trades": today_trades,
        "week_pnl": week_pnl,
        "week_trades": week_trades,
        "month_pnl": month_pnl,
        "month_trades": month_trades,
        "by_strategy": by_strategy,
        "by_symbol": by_symbol,
        "equity_curve": [round(v, 2) for v in equity],
        "first_trade_at": trades[0]["closed_at"] if trades else None,
        "last_trade_at": trades[-1]["closed_at"] if trades else None,
    }


def metrics(ledger_path: str | None = None, *, starting_balance: float = 10000.0) -> dict[str, Any]:
    return compute_metrics(load_trades(ledger_path), starting_balance=starting_balance)


# ---------------------------------------------------------------------------
# Telegram-ready text
# ---------------------------------------------------------------------------
def _money(value: float) -> str:
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{sign}${abs(value):,.2f}"


def _badge(value: float) -> str:
    return "🟢" if value > 0 else ("🔴" if value < 0 else "⚪")


def format_daily_summary(stats: dict[str, Any], *, mode: str = "", venue: str = "") -> str:
    """Daily PnL summary message."""
    pf = stats["profit_factor"]
    pf_text = f"{pf:.2f}" if pf is not None else "∞"
    header_meta = " · ".join(part for part in (mode, venue) if part)
    lines = [
        "📊 <b>STRATEX DAILY PERFORMANCE</b>",
        f"<i>{stats['generated_at'].strftime('%Y-%m-%d %H:%M UTC')}"
        + (f" · {header_meta}" if header_meta else "")
        + "</i>",
        "",
        f"{_badge(stats['today_pnl'])} <b>Today's PnL</b>: <code>{_money(stats['today_pnl'])}</code>"
        f"  ({stats['today_trades']} closed)",
        f"{_badge(stats['week_pnl'])} <b>This week</b>: <code>{_money(stats['week_pnl'])}</code>"
        f"  ({stats['week_trades']} closed)",
        f"{_badge(stats['month_pnl'])} <b>This month</b>: <code>{_money(stats['month_pnl'])}</code>"
        f"  ({stats['month_trades']} closed)",
        "",
        "<b>Lifetime</b>",
        f"• Net PnL: <code>{_money(stats['net_pnl'])}</code> (fees <code>${stats['fees']:,.2f}</code>)",
        f"• Trades: <code>{stats['trades']}</code> · W/L: <code>{stats['wins']}/{stats['losses']}</code>",
        f"• Win rate: <code>{stats['win_rate'] * 100:.1f}%</code> · Profit factor: <code>{pf_text}</code>",
        f"• Avg win: <code>${stats['avg_win']:,.2f}</code> · Avg loss: <code>${stats['avg_loss']:,.2f}</code>",
        f"• Max drawdown: <code>{stats['max_drawdown_pct']:.2f}%</code>",
        f"• Equity: <code>${stats['ending_equity']:,.2f}</code>"
        f" ({stats['return_pct']:+.2f}% from ${stats['starting_balance']:,.0f})",
    ]

    if stats["by_strategy"]:
        lines += ["", "<b>By strategy</b>"]
        for name, bucket in list(stats["by_strategy"].items())[:6]:
            lines.append(
                f"  • <code>{name}</code>: {_money(bucket['net_pnl'])} "
                f"({bucket['trades']} trades, {bucket['win_rate'] * 100:.0f}% win)"
            )
    return "\n".join(lines)


def format_risk_dashboard(stats: dict[str, Any], risk: dict[str, Any]) -> str:
    """Risk metrics dashboard message."""
    equity = float(risk.get("equity") or stats["ending_equity"])
    day_limit = float(risk.get("max_daily_loss_pct") or 0.03) * equity
    dd_limit = float(risk.get("max_drawdown_pct") or 0.08) * equity
    day_used = abs(min(0.0, stats["today_pnl"]))
    dd_used = stats["max_drawdown_pct"] / 100.0 * equity
    open_positions = risk.get("open_positions", [])
    max_positions = int(risk.get("max_open_positions") or 8)
    exposure = float(risk.get("exposure") or 0.0)
    max_exposure = float(risk.get("max_exposure_pct") or 0.10) * equity

    def _bar(used: float, limit: float) -> str:
        if limit <= 0:
            return "n/a"
        ratio = min(1.0, max(0.0, used / limit))
        filled = int(round(ratio * 10))
        return "█" * filled + "░" * (10 - filled) + f" {ratio * 100:.0f}%"

    return "\n".join(
        [
            "🛡️ <b>STRATEX RISK DASHBOARD</b>",
            "<i>Live limits vs. current utilisation</i>",
            "",
            f"💰 <b>Equity</b>: <code>${equity:,.2f}</code>",
            f"📉 <b>Daily loss</b>: {_bar(day_used, day_limit)}",
            f"    used <code>${day_used:,.2f}</code> / limit <code>${day_limit:,.2f}</code>",
            f"📉 <b>Max drawdown</b>: <code>{stats['max_drawdown_pct']:.2f}%</code> of {float(risk.get('max_drawdown_pct') or 0.08) * 100:.0f}% limit",
            f"    used <code>${dd_used:,.2f}</code> / limit <code>${dd_limit:,.2f}</code>",
            f"📊 <b>Exposure</b>: {_bar(exposure, max_exposure)}",
            f"    <code>${exposure:,.2f}</code> / <code>${max_exposure:,.2f}</code>",
            f"🔢 <b>Open positions</b>: <code>{len(open_positions)}/{max_positions}</code>",
            "",
            "<b>Edge quality</b>",
            f"• Expectancy/trade: <code>{_money(stats['expectancy'])}</code>",
            f"• Profit factor: <code>{stats['profit_factor'] if stats['profit_factor'] is not None else '∞'}</code>",
            f"• Sharpe/trade: <code>{stats['sharpe_per_trade']}</code>",
            f"• Streaks: best <code>{stats['best_streak']}</code> / worst <code>{stats['worst_streak']}</code>",
            "",
            f"🚦 <b>State</b>: {risk.get('state', 'ACTIVE')}",
        ]
    )


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------
def equity_curve_png(stats: dict[str, Any], out_path: str) -> str | None:
    """Render the equity curve + rolling drawdown to a PNG. Returns path or None."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except Exception:
        return None

    curve = stats.get("equity_curve") or []
    if len(curve) < 2:
        return None

    try:
        fig, (ax_eq, ax_dd) = plt.subplots(
            2, 1, figsize=(8, 4.8), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
        )
        fig.patch.set_facecolor("#0b0f14")
        for ax in (ax_eq, ax_dd):
            ax.set_facecolor("#0b0f14")
            ax.tick_params(colors="#8b98a5", labelsize=8)
            for spine in ax.spines.values():
                spine.set_color("#1e2733")

        x = list(range(len(curve)))
        ax_eq.plot(x, curve, color="#c8ff3d", linewidth=1.8)
        ax_eq.fill_between(x, curve, min(curve), color="#c8ff3d", alpha=0.10)
        ax_eq.axhline(stats["starting_balance"], color="#3d4a5a", linestyle="--", linewidth=0.8)
        ax_eq.set_title(
            f"Stratex equity · {stats['trades']} trades · net {_money(stats['net_pnl'])}",
            color="#e6edf3",
            fontsize=10,
            loc="left",
        )
        ax_eq.grid(color="#1e2733", linewidth=0.5, alpha=0.6)

        peak = curve[0]
        drawdowns = []
        for value in curve:
            peak = max(peak, value)
            drawdowns.append((value - peak) / peak * 100 if peak else 0.0)
        ax_dd.fill_between(x, drawdowns, 0, color="#ff5c5c", alpha=0.45)
        ax_dd.set_ylabel("DD %", color="#8b98a5", fontsize=8)
        ax_dd.grid(color="#1e2733", linewidth=0.5, alpha=0.6)

        fig.tight_layout()
        fig.savefig(out_path, dpi=130, facecolor=fig.get_facecolor())
        plt.close(fig)
        return out_path
    except Exception:
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def equity_sparkline(stats: dict[str, Any], width: int = 40) -> str:
    """Unicode-block sparkline of the equity curve (no matplotlib needed)."""
    curve = stats.get("equity_curve") or []
    if len(curve) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(curve), max(curve)
    span = hi - lo if hi != lo else 1.0
    # Downsample to width
    if len(curve) > width:
        step = len(curve) / width
        sampled = [curve[int(i * step)] for i in range(width)]
    else:
        sampled = curve
    return "".join(blocks[min(8, int((v - lo) / span * 8))] for v in sampled)
    data = metrics()
    print(format_daily_summary(data))
    print()
    print(format_risk_dashboard(data, {"equity": data["ending_equity"]}))
