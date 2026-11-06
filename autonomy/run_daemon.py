"""
autonomy/run_daemon.py — Autonomous 24/7 Stratex Production Daemon Runner.

Orchestrates:
1. Real-time bidirectional Telegram polling listener with Gemini AI chat.
2. 24/7 Multi-asset multi-timeframe quantitative scanning loop with strategy execution.
3. Execution on Binance Futures (Crypto) & OANDA (Forex/Indices/Commodities).
"""
from __future__ import annotations

import os
import sys
import time
import signal
import json
from typing import Dict, Optional

from autonomy.multi_venue_engine import create_multi_venue_engine
from strategies.strategy_registry import strategy_registry
from autonomy.cloud_data_feed import get_cloud_data_feed
from logger import get_logger
from notifications.telegram_bot import get_telegram_bot

logger = get_logger("stratex_daemon")


class StratexTradingEngine:
    """Main trading engine that coordinates signal generation and execution.
    ALL RESTRICTIONS REMOVED FOR DEMO TESTING - UNLIMITED TRADING.
    """

    def __init__(self, multi_venue_engine):
        self.engine = multi_venue_engine
        self.cloud_feed = get_cloud_data_feed()
        # Continuous scanning - no interval delay
        self.scan_interval = float(os.getenv("SCAN_INTERVAL_SECONDS", "0"))
        # No position limits
        self.max_positions = int(os.getenv("STRATEX_MAX_TOTAL_POSITIONS", "9999"))
        # No confidence filter
        self.min_confidence = float(os.getenv("STRATEX_MIN_CONFIDENCE", "0.0"))
        # No minimum R:R filter
        self.min_rr = float(os.getenv("STRATEX_MIN_RR", "0.0"))
        # Higher risk per trade for demo
        self.risk_per_trade = float(os.getenv("STRATEX_RISK_PER_TRADE_PCT", "0.02"))
        # No notional cap per position
        self.max_position_pct = float(os.getenv("STRATEX_MAX_POSITION_PCT", "1.0"))
        # No stop distance limits
        self.min_stop_pct = float(os.getenv("STRATEX_MIN_STOP_PCT", "0.0"))
        self.max_stop_pct = float(os.getenv("STRATEX_MAX_STOP_PCT", "1.0"))
        # No confluence requirement - single strategy can trade
        self.entry_confluence = int(os.getenv("STRATEX_ENTRY_CONFLUENCE", "1"))
        # No ADX filter
        self.adx_threshold = float(os.getenv("STRATEX_ADX_THRESHOLD", "0"))
        # Fees: Binance USD-M taker is 0.04% per side (0.08% round trip).
        self.fee_rate_roundtrip = float(os.getenv("STRATEX_FEE_ROUNDTRIP", "0.0008"))
        # No expectancy filter
        self.min_expectancy_r = float(os.getenv("STRATEX_MIN_EXPECTANCY_R", "-1.0"))
        # Track confluenced signals per symbol this cycle.
        self._confluence_signals: dict[tuple[str, str, str], list[tuple[str, dict, str]]] = {}
        # Latched closed bar per (venue, symbol, strategy, tf)
        self._last_signal_bar: dict[tuple[str, str, str, str], object] = {}
        # ADX only for trend/breakout (disabled with threshold=0)
        self.adx_strategies = {
            s.strip()
            for s in os.getenv(
                "STRATEX_ADX_STRATEGIES",
                "strategy_fast_ema_cross,strategy_trend_daily,strategy_scalping_breakout,"
                "strategy_momentum,strategy_adx_trend,trend_continuation,channel_breakout,"
                "atr_breakout,volume_breakout,higher_highs",
            ).split(",")
            if s.strip()
        }

        # No denylist - all strategies allowed
        self.strategy_denylist = set()
        self.strategy_allowlist = {
            s.strip()
            for s in os.getenv("STRATEX_STRATEGY_ALLOWLIST", "").split(",")
            if s.strip()
        }
        # Measured win rates per strategy (from the backtest), which replace the
        # module constants that were never evidence.
        self.strategy_priors: Dict[str, float] = {}
        priors_file = os.getenv(
            "STRATEX_STRATEGY_PRIORS_FILE",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "state", "strategy_priors.json"),
        )
        try:
            with open(priors_file, "r", encoding="utf-8") as fh:
                raw_priors = json.load(fh)
            self.strategy_priors = {
                k: float(v) for k, v in raw_priors.items() if isinstance(v, (int, float))
            }
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"[STRATEX] Could not load strategy priors: {e}")

        # Crypto: the registry only validates BTC/ETH/SOL, so scanning the other
        # twelve symbols was pure API load with zero possible trades.
        crypto_env = os.getenv("STRATEX_CRYPTO_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,LTCUSDT,DOTUSDT,BCHUSDT,ATOMUSDT,UNIUSDT,TRXUSDT,NEARUSDT,SUIUSDT,AAVEUSDT,ENAUSDT,WLDUSDT,ZECUSDT,HYPEUSDT,PAXGUSDT,TRUMPUSDT,PEPEUSDT")
        # Symbol universe per venue
        self.symbols = {
            "binance_futures": [s.strip() for s in crypto_env.split(",") if s.strip()],
            "oanda": [
                "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
                "EURGBP", "EURJPY", "EURAUD", "EURCAD", "EURCHF",
                "GBPJPY", "GBPAUD", "GBPCAD", "GBPCHF",
                "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
                "XAUUSD", "XAGUSD", "US30", "US500", "USTEC", "DE30", "UK100",
                "USOIL", "UKOIL", "BCOUSD", "NATGAS",
            ],
        }

        tf_env = os.getenv("STRATEX_TIMEFRAMES", "1d,4h")
        self.timeframes = [t.strip() for t in tf_env.split(",") if t.strip()] or ["1d"]
        self.running = True
        self.last_scan = 0
        self.scan_count = 0
        self._last_trend_manage = 0.0
        self._last_summary_date = ""

    def scan_and_execute(self) -> dict:
        """Run one scan cycle across all symbols and timeframes."""
        results = {"scanned": 0, "signals": 0, "executed": 0, "errors": 0}
        scanned_symbols = set()
        scanned_1d = 0
        self._confluence_signals = {}

        # Detect broker-side exits first: a position closed by SL/TP must be
        # booked even when the daily loss limit stops us from scanning.
        self._reconcile_positions()

        # Daily trend positions exit on an EMA20 close cross, not a fixed target.
        # Runs before the loss-limit gate so a losing book can still be exited.
        self._manage_trend_positions()

        # Check daily loss limit
        risk = self.engine.get_risk_summary()
        if risk.get("daily_loss_limit_hit", False):
            logger.warning("[STRATEX] Daily loss limit hit, skipping scan")
            return results

        # Prefetch candles for all symbols
        self._prefetch_candles()

        for venue, symbols in self.symbols.items():
            if venue not in self.engine.venues:
                continue
                
            for symbol in symbols:
                for tf in self.timeframes:
                    try:
                        # Get candles. The daily trend strategy needs 200 bars for
                        # its EMA200 regime filter; intraday strategies keep 100.
                        candle_limit = 300 if tf == "1d" else 100
                        min_bars = 220 if tf == "1d" else 50
                        candles = self.engine.get_candles(venue, symbol, tf, candle_limit)
                        if not candles or len(candles) < min_bars:
                            continue

                        # Convert to DataFrame
                        df = self._candles_to_df(candles)
                        if df is None or len(df) < min_bars:
                            continue

                        # Track symbols actually scanned this cycle.
                        scanned_symbols.add((venue, symbol))
                        if tf == "1d":
                            scanned_1d += 1

                        # Run all strategies
                        for strat_name in strategy_registry.get_all_strategy_names():
                            # Measured-loser and structurally broken strategies are
                            # removed before execution: they cost CPU and lose money.
                            if self.strategy_allowlist:
                                if strat_name not in self.strategy_allowlist:
                                    continue
                            elif strat_name in self.strategy_denylist:
                                continue

                            ok, reason = strategy_registry.executable(strat_name, tf, symbol)
                            if not ok:
                                continue

                            # Static geometry that can never satisfy the R:R gate
                            # is skipped before running it: on 15 symbols x 3
                            # timeframes these executions dominated scan time.
                            spec = strategy_registry.get(strat_name)
                            if (
                                spec is not None
                                and spec.rr_ratio
                                and spec.rr_ratio < self.min_rr
                            ):
                                continue

                            result = strategy_registry.execute_strategy(strat_name, df, tf, symbol)
                            results["scanned"] += 1

                            logger.info(f"[STRATEX] Strategy {strat_name} on {venue}:{symbol} {tf}: {result.get('status')} - {result.get('side', 'N/A')}")

                            if result.get("status") == "SIGNAL" and result.get("side", "HOLD") != "HOLD":
                                results["signals"] += 1

                                # Fail-closed quality gate. Missing metrics used to
                                # pass because `if win_rate and ...` treats 0 as
                                # "no opinion"; that let strategies with a 0.0
                                # prior trade on R:R alone.
                                rr_raw = result.get("rr_ratio")
                                wr_raw = self.strategy_priors.get(strat_name)
                                if wr_raw is None:
                                    wr_raw = result.get("win_rate_prior")
                                if wr_raw is None:
                                    wr_raw = result.get("confidence")

                                try:
                                    rr = float(rr_raw)
                                except (TypeError, ValueError):
                                    rr = 0.0
                                try:
                                    win_rate = float(wr_raw)
                                except (TypeError, ValueError):
                                    win_rate = 0.0

                                if rr <= 0:
                                    logger.info(
                                        f"[STRATEX] Skip {strat_name} {symbol}: "
                                        f"no R:R metric"
                                    )
                                    continue
                                if rr < self.min_rr:
                                    logger.info(
                                        f"[STRATEX] Skip {strat_name} {symbol}: "
                                        f"R:R {rr:.2f} < {self.min_rr}"
                                    )
                                    continue

                                sl_dbg = float(result.get("stop_loss") or 0)
                                tp_dbg = float(result.get("take_profit") or 0)

                                logger.info(
                                    f"[STRATEX] Signal detected: {strat_name} "
                                    f"{result['side']} {venue}:{symbol} {tf} "
                                    f"@ SL={sl_dbg:.5f} TP={tp_dbg:.5f}"
                                )
                                # Confluence is keyed by venue+symbol+side and
                                # counted by DISTINCT strategy. One oscillator
                                # firing on 3 timeframes is one opinion, not 3.
                                key_side = (venue, symbol, result["side"])
                                if key_side not in self._confluence_signals:
                                    self._confluence_signals[key_side] = []
                                self._confluence_signals[key_side].append((strat_name, result, tf))

                    except Exception as e:
                        results["errors"] += 1
                        logger.warning(f"[STRATEX] Error scanning {venue}:{symbol} {tf}: {e}")

        results["symbols"] = len(scanned_symbols)
        results["symbols_1d"] = scanned_1d

        # Confluence filtering: require N DISTINCT strategies on the same
        # venue+symbol+side. Deduplicate by strategy name before counting.
        confluenced = []
        for (c_venue, c_symbol, c_side), sigs in self._confluence_signals.items():
            distinct = {s[0] for s in sigs}
            if len(distinct) >= self.entry_confluence:
                confluenced.append((c_venue, c_symbol, c_side, sigs, distinct))
        self._confluence_signals = {}

        # Contradiction guard: a symbol with confluenced BUY *and* SELL in the
        # same cycle has no edge, only noise. Skip it instead of guessing.
        sides: dict[tuple[str, str], set[str]] = {}
        for c_venue, c_symbol, c_side, _sigs, _distinct in confluenced:
            sides.setdefault((c_venue, c_symbol), set()).add(c_side)
        conflicted = {key for key, value in sides.items() if len(value) > 1}
        if conflicted:
            logger.info(
                f"[STRATEX] Conflict: {len(conflicted)} symbol(s) produced both "
                f"BUY and SELL confluence; skipped"
            )
            confluenced = [
                c for c in confluenced if (c[0], c[1]) not in conflicted
            ]

        if confluenced:
            logger.info(
                f"[STRATEX] Confluence: {len(confluenced)} symbol(s) met "
                f"{self.entry_confluence}+ distinct strategy agreement"
            )

        # Execute confluenced signals. Rank candidates by their own R:R first,
        # then by the shortest timeframe: a scalp should not wait on 1h context.
        tf_order = {"1m": 0, "5m": 1, "15m": 2, "1h": 3, "4h": 4, "1d": 5}
        for c_venue, c_symbol, c_side, sigs, distinct in confluenced:
            can_open, reason = self.engine.can_open_position(c_venue)
            if not can_open:
                logger.info(
                    f"[STRATEX] Cannot open {c_venue}:{c_symbol}: {reason}"
                )
                continue

            ranked = sorted(
                sigs,
                key=lambda s: (
                    -float(s[1].get("rr_ratio") or 0),
                    tf_order.get(s[2], 9),
                ),
            )

            executed = False
            for strat_name, result, tf in ranked:
                # Copy so per-candidate stamping cannot leak between attempts.
                payload = dict(result)
                payload["strategy"] = strat_name
                payload["venue"] = c_venue
                payload["symbol"] = c_symbol
                payload["timeframe"] = tf
                if self._execute_signal(c_venue, c_symbol, payload, tf):
                    results["executed"] += 1
                    executed = True
                    logger.info(
                        f"[STRATEX] Executed {strat_name} on {c_venue}:{c_symbol} "
                        f"{c_side} {tf} (confluence: "
                        f"{','.join(sorted(distinct))})"
                    )
                    break

            if not executed:
                logger.info(
                    f"[STRATEX] No executable candidate for {c_venue}:{c_symbol} "
                    f"{c_side} (evaluated {len(ranked)})"
                )

        self._maybe_send_daily_summary(results)

        # Periodic auto-prune (once per day, on the same day boundary as summary)
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        if hasattr(self, "_last_prune_date"):
            if self._last_prune_date != today:
                self._maybe_prune_strategies()
                self._last_prune_date = today
        else:
            self._last_prune_date = today

        self.scan_count += 1
        return results

    def _maybe_send_daily_summary(self, results: dict) -> None:
        """Send a Telegram daily summary once per UTC day (throttled)."""
        try:
            import datetime

            today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            if self._last_summary_date == today:
                return
            self._last_summary_date = today

            bot = getattr(self.engine, "telegram_bot", None)
            if bot is None:
                try:
                    from notifications.telegram_bot import get_telegram_bot
                    bot = get_telegram_bot()
                except Exception:
                    return
            if not getattr(bot, "is_configured", False):
                return

            risk = self.engine.get_risk_summary() or {}
            equity = float(risk.get("total_equity", 0) or 0)
            realized = float(risk.get("realized_pnl", 0) or self.engine.realized_pnl or 0)
            positions_open = len(self.engine.positions)
            daily_loss_hit = bool(risk.get("daily_loss_limit_hit", False))

            paused = False
            try:
                from trading_pause import is_trading_paused
                paused = bool(is_trading_paused())
            except Exception:
                paused = False

            symbols_scanned = int(results.get("symbols", 0) or 0)
            signals_found = int(results.get("signals", 0) or 0)

            msg = (
                f"📊 <b>DAILY SUMMARY — {today}</b>\n\n"
                f"• <b>Date</b>: <code>{today}</code>\n"
                f"• <b>Symbols scanned</b>: <code>{symbols_scanned}</code>\n"
                f"• <b>Signals found</b>: <code>{signals_found}</code>\n"
                f"• <b>Positions open</b>: <code>{positions_open}</code>\n"
                f"• <b>Equity</b>: <code>${equity:,.2f}</code>\n"
                f"• <b>Realized PnL</b>: <code>${realized:+,.2f}</code>\n"
                f"• <b>Daily loss limit hit</b>: <code>{daily_loss_hit}</code>\n"
                f"• <b>Paused</b>: <code>{paused}</code>"
            )
            bot.send_message(msg)
        except Exception as e:
            logger.debug(f"[STRATEX] Daily summary failed: {e}")

    def _reconcile_positions(self):
        """Detect broker-side (SL/TP) exits and book the realized PnL once."""
        live_cache: Dict[str, Optional[list]] = {}
        try:
            for key, pos in list(self.engine.positions.items()):
                venue, _, symbol = key.partition(":")
                if not venue or not symbol:
                    logger.debug(f"[STRATEX] Malformed tracked position key: {key}")
                    continue

                adapter = self.engine.venues.get(venue)
                if adapter is None:
                    continue

                try:
                    # One live-position fetch per venue per reconciliation pass.
                    if venue not in live_cache:
                        try:
                            if hasattr(adapter, "get_positions_or_none"):
                                live_cache[venue] = adapter.get_positions_or_none()
                            else:
                                live_cache[venue] = adapter.get_positions()
                        except Exception as e:
                            logger.debug(
                                f"[STRATEX] Live position fetch failed for {venue}: {e}"
                            )
                            live_cache[venue] = None

                    live_positions = live_cache[venue]
                    if live_positions is None:
                        # Unknown state (API failure) is NOT flat. Never book on it.
                        logger.debug(
                            f"[STRATEX] Unknown position state for {venue}; "
                            f"skipping reconciliation"
                        )
                        continue

                    if any(p.symbol == symbol for p in live_positions):
                        continue

                    opened_at = self.engine.position_opened_at.get(key)
                    pnl_info: Optional[dict] = None
                    if hasattr(adapter, "get_realized_pnl"):
                        try:
                            pnl_info = adapter.get_realized_pnl(symbol, since_ms=opened_at)
                        except TypeError:
                            pnl_info = adapter.get_realized_pnl(symbol)
                        except Exception as e:
                            logger.debug(
                                f"[STRATEX] Realized PnL lookup failed for {key}: {e}"
                            )
                            pnl_info = None

                    if pnl_info:
                        net = float(pnl_info.get("net", 0.0) or 0.0)
                        gross = float(pnl_info.get("gross", net) or 0.0)
                        fees = float(pnl_info.get("fees", 0.0) or 0.0)
                        trades = int(pnl_info.get("trades", 0) or 0)
                        exit_price = float(pnl_info.get("last_price", 0.0) or 0.0)
                    else:
                        # Fallback estimate from ticker (fee-blind).
                        ticker = self.engine.get_ticker(venue, symbol)
                        exit_price = ticker.last if ticker and ticker.last > 0 else pos.mark_price
                        gross = (exit_price - pos.entry_price) * pos.quantity
                        if pos.side in ("SHORT", "SELL"):
                            gross = -gross
                        fees = 0.0
                        net = gross
                        trades = 1

                    if exit_price <= 0:
                        exit_price = pos.mark_price

                    # Pop first: at most one booking per position, even if the
                    # booking or the Telegram send below raises.
                    self.engine.positions.pop(key, None)
                    self.engine.position_opened_at.pop(key, None)

                    self.engine.realized_pnl += net
                    self.engine.daily_realized_pnl += net
                    self.engine.save_state()

                    # Write per-strategy ledger entry for analytics and auto-pruning
                    try:
                        strat = self.engine.position_strategy.pop(key, "unknown")
                        tf = self.engine.position_timeframe.pop(key, "n/a")
                        entry_price = pos.entry_price
                        risk_per_unit = abs(entry_price - pos.mark_price) if pos.mark_price else 0.0
                        r_multiple = round(net / risk_per_unit, 2) if risk_per_unit > 0 else 0.0
                        ledger_entry = {
                            "symbol": symbol,
                            "strategy": strat,
                            "timeframe": tf,
                            "side": pos.side,
                            "quantity": pos.quantity,
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "gross_pnl": gross,
                            "fees": fees,
                            "net_pnl": net,
                            "r_multiple": r_multiple,
                            "trades": trades,
                            "exit_reason": "SL_TP_BROKER",
                            "source": "MULTI_VENUE_RECONCILIATION",
                            "entry_timestamp": self.engine.position_opened_at.get(key, 0) / 1000.0,
                            "exit_timestamp": time.time(),
                            "timestamp": time.time(),
                        }
                        ledger_path = os.path.join(
                            os.path.dirname(os.path.dirname(__file__)), "testnet_trade_ledger.jsonl"
                        )
                        with open(ledger_path, "a", encoding="utf-8") as fh:
                            fh.write(json.dumps(ledger_entry) + "\n")
                    except Exception as e:
                        logger.debug(f"Ledger write failed for {key}: {e}")

                    # Protective orders outlive a broker-side SL/TP hit; clear them.
                    if hasattr(adapter, "cancel_protective_orders"):
                        try:
                            cancelled = adapter.cancel_protective_orders(symbol)
                            if cancelled:
                                logger.info(
                                    f"[STRATEX] Cancelled {cancelled} leftover "
                                    f"protective order(s) on {symbol}"
                                )
                        except Exception as e:
                            logger.debug(
                                f"[STRATEX] Protective order cleanup failed for {key}: {e}"
                            )

                    logger.info(
                        f"[STRATEX] Position closed at broker: {key} "
                        f"net=${net:+.2f} gross=${gross:+.2f} fees=${fees:.2f} "
                        f"trades={trades}"
                    )

                    # Send Telegram
                    if self.engine.telegram_bot and self.engine.telegram_bot.is_configured:
                        emoji = "💰" if net >= 0 else "🔻"
                        self.engine.telegram_bot.send_message(
                            f"{emoji} <b>POSITION CLOSED</b>\n\n"
                            f"• <b>Venue</b>: <code>{venue}</code>\n"
                            f"• <b>Symbol</b>: <code>{symbol}</code>\n"
                            f"• <b>Side</b>: <code>{pos.side}</code>\n"
                            f"• <b>Qty</b>: <code>{pos.quantity}</code>\n"
                            f"• <b>Entry</b>: <code>{pos.entry_price:.5f}</code>\n"
                            f"• <b>Exit</b>: <code>{exit_price:.5f}</code>\n"
                            f"• <b>Realized PnL</b>: {emoji} <code>${net:+.2f}</code>\n"
                            f"• <b>Fees</b>: <code>${fees:.2f}</code>\n"
                            f"• <b>Trades</b>: <code>{trades}</code>\n"
                            f"• <b>Ticket</b>: <code>{pos.position_id}</code>"
                        )
                except Exception as e:
                    logger.debug(f"Reconciliation error for {key}: {e}")
        except Exception as e:
            logger.debug(f"Reconciliation error: {e}")

    def _manage_trend_positions(self) -> None:
        """Exit a trend position when the completed daily close crosses EMA(20).

        The 2019-2026 study showed the exit rule carries the edge: a fixed 3R
        target was breakeven over seven years and negative since 2024, while
        the EMA20 exit stayed positive (PF 1.95 full sample, 1.09 OOS). The
        exchange-side stop stays armed as hard protection until the close fills.

        Only the market order is sent here. The reconciler sees the now-flat
        position on its next pass, books realized PnL from userTrades and sends
        the POSITION CLOSED alert, so PnL can never be double counted.
        """
        now = time.time()
        if now - self._last_trend_manage < 60:
            return
        self._last_trend_manage = now

        for key, pos in list(self.engine.positions.items()):
            venue, _, symbol = key.partition(":")
            if not venue or not symbol:
                continue
            adapter = self.engine.venues.get(venue)
            if adapter is None:
                continue
            try:
                candles = self.engine.get_candles(venue, symbol, "1d", 60)
                if not candles:
                    continue
                # Keep only completed daily bars: the last one is still forming.
                closed = [
                    c for c in candles
                    if float(c.get("timestamp", 0)) + 86400 <= now
                ]
                if len(closed) < 25:
                    continue

                closes = [float(c["close"]) for c in closed]
                ema20 = closes[0]
                alpha = 2.0 / 21.0
                for px in closes[1:]:
                    ema20 = alpha * px + (1.0 - alpha) * ema20
                last_close = closes[-1]

                is_long = str(pos.side).upper() in ("LONG", "BUY")
                trigger = (
                    last_close < ema20 if is_long else last_close > ema20
                )
                if not trigger:
                    continue

                close_side = "SELL" if is_long else "BUY"
                result = adapter.place_order(
                    symbol, close_side, "MARKET", pos.quantity
                )
                if result is None:
                    logger.warning(
                        f"[STRATEX] Trend exit rejected for {venue}:{symbol}"
                    )
                    continue
                if hasattr(adapter, "cancel_protective_orders"):
                    adapter.cancel_protective_orders(symbol)
                logger.info(
                    f"[STRATEX] Trend exit {venue}:{symbol} ({pos.side}): "
                    f"daily close {last_close:.4f} crossed EMA20 {ema20:.4f}"
                )
            except Exception as e:
                logger.warning(f"[STRATEX] Trend exit check failed for {key}: {e}")

    def _prefetch_candles(self):
        """Pre-fetch candles for all symbols/timeframes."""
        try:
            pairs = []
            for venue, symbols in self.symbols.items():
                for symbol in symbols:
                    for tf in self.timeframes:
                        pairs.append((venue, symbol, tf))
            
            # The cloud feed caches automatically, just prime it
            for venue, symbol, tf in pairs[:20]:  # Limit to avoid rate limits
                try:
                    self.engine.get_candles(venue, symbol, tf, 300 if tf == "1d" else 100)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Prefetch error: {e}")

    def _candles_to_df(self, candles: list) -> "pd.DataFrame | None":
        """Convert candle list to DataFrame."""
        try:
            import pandas as pd
            df = pd.DataFrame(candles)
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception:
            return None

    def _calc_adx(self, highs: list, lows: list, closes: list, period: int = 14) -> float | None:
        """Calculate ADX (Average Directional Index) for trend strength."""
        try:
            n = len(closes)
            if n < period + 2:
                return None

            # True Range
            tr = []
            plus_dm = []
            minus_dm = []
            for i in range(1, n):
                high_diff = highs[i] - highs[i-1]
                low_diff = lows[i-1] - lows[i]
                tr_val = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i] - closes[i-1]),
                )
                tr.append(tr_val)

                # +DM: high up move greater than low down move
                if high_diff > low_diff and high_diff > 0:
                    plus_dm.append(high_diff)
                else:
                    plus_dm.append(0.0)

                # -DM: low down move greater than high up move
                if low_diff > high_diff and low_diff > 0:
                    minus_dm.append(low_diff)
                else:
                    minus_dm.append(0.0)

            # Wilder smoothing
            def wilder_smooth(values, period):
                if len(values) < period:
                    return None
                smoothed = [sum(values[:period])]
                for i in range(period, len(values)):
                    smoothed.append(smoothed[-1] - smoothed[-1] / period + values[i])
                return smoothed

            tr_smooth = wilder_smooth(tr, period)
            plus_smooth = wilder_smooth(plus_dm, period)
            minus_smooth = wilder_smooth(minus_dm, period)

            if tr_smooth is None or plus_smooth is None or minus_smooth is None:
                return None
            if len(tr_smooth) < 2:
                return None

            # +DI and -DI
            plus_di = [100 * p / t if t > 0 else 0 for p, t in zip(plus_smooth, tr_smooth)]
            minus_di = [100 * m / t if t > 0 else 0 for m, t in zip(minus_smooth, tr_smooth)]

            # DX
            dx = []
            for pdi, mdi in zip(plus_di, minus_di):
                denom = pdi + mdi
                dx.append(100 * abs(pdi - mdi) / denom if denom > 0 else 0)

            # ADX = smoothed DX
            adx_values = wilder_smooth(dx, period)
            if adx_values is None or len(adx_values) == 0:
                return None

            return adx_values[-1]
        except Exception:
            return None

    def _execute_signal(self, venue: str, symbol: str, signal: dict, timeframe: str) -> bool:
        """Execute a trading signal on the venue."""
        try:
            # Cross-process safety controls: the Telegram bot and the control API
            # write flags in separate processes, so the daemon must read them
            # from disk or /pause and /halt would be decorative.
            try:
                from trading_pause import is_trading_paused

                if is_trading_paused():
                    logger.info(f"[STRATEX] Entries paused; {symbol} signal ignored")
                    return False
            except Exception as e:
                logger.warning(f"[STRATEX] Pause-state read failed: {e}")

            try:
                from panic_state import is_panic_active, is_kill_switch_locked

                if is_panic_active() or is_kill_switch_locked():
                    logger.info(f"[STRATEX] Panic/kill-switch active; {symbol} signal ignored")
                    return False
            except Exception as e:
                logger.warning(f"[STRATEX] Panic-state read failed: {e}")

            # Check position limits
            can_open, reason = self.engine.can_open_position(venue)
            if not can_open:
                logger.debug(f"Cannot open position on {venue}: {reason}")
                return False

            # Get current price for sizing
            ticker = self.engine.get_ticker(venue, symbol)
            if not ticker or ticker.last <= 0:
                return False

            current_price = ticker.last
            side = signal["side"]
            stop_loss = signal.get("stop_loss", signal.get("sl", 0))
            take_profit = signal.get("take_profit", signal.get("tp", 0))

            # Re-anchor stops to the live entry price when the strategy supplies
            # distances. The backtest entered at the next bar's open, but the
            # daemon can fire hours later; re-anchoring keeps the tested risk
            # geometry (e.g. 2.5xATR) instead of a stale signal-bar level.
            sl_dist = signal.get("sl_dist")
            tp_dist = signal.get("tp_dist")
            if sl_dist and tp_dist:
                sign = 1 if str(side).upper() in ("BUY", "LONG") else -1
                try:
                    stop_loss = current_price - sign * float(sl_dist)
                    take_profit = current_price + sign * float(tp_dist)
                except (TypeError, ValueError):
                    pass

            if stop_loss <= 0 or take_profit <= 0:
                return False

            # A stop tighter than the min distance means taker fees eat a large
            # share of every R. At 0.8% stop the round-trip is ~0.10R; at the
            # 0.23% stops the backtest saw it was ~0.35R, more than the edge.
            stop_pct = abs(current_price - stop_loss) / current_price
            if stop_pct < self.min_stop_pct:
                logger.info(
                    f"[STRATEX] Skip {symbol}: stop {stop_pct*100:.2f}% "
                    f"< min {self.min_stop_pct*100:.2f}% (fee drag)"
                )
                return False

            # Max stop cap: never risk more than 3% on a single trade.
            if stop_pct > self.max_stop_pct:
                logger.info(
                    f"[STRATEX] Skip {symbol}: stop {stop_pct*100:.2f}% "
                    f"> max {self.max_stop_pct*100:.2f}% (risk too large)"
                )
                return False

            # Direction sanity: an inverted SL/TP pair must never reach the venue.
            side_up = str(side).upper() in ("BUY", "LONG")
            if side_up and not (stop_loss < current_price < take_profit):
                logger.warning(f"[STRATEX] Skip {symbol}: invalid BUY SL/TP geometry")
                return False
            if not side_up and not (take_profit < current_price < stop_loss):
                logger.warning(f"[STRATEX] Skip {symbol}: invalid SELL SL/TP geometry")
                return False

            # Net expectancy gate. Declared R:R alone is not evidence: the
            # backtest showed 0.78-win-rate strategies losing money after fees.
            strategy_name = signal.get("strategy") or signal.get("strategy_name") or ""
            win_rate = self.strategy_priors.get(strategy_name)
            if win_rate is None:
                win_rate = signal.get("win_rate_prior")
            if win_rate is None:
                win_rate = signal.get("confidence")
            try:
                win_rate = float(win_rate or 0)
            except (TypeError, ValueError):
                win_rate = 0.0

            if win_rate <= 0:
                logger.info(
                    f"[STRATEX] Skip {symbol}: {strategy_name or 'unknown'} has "
                    f"no measured win-rate"
                )
                return False

            # ADX trend filter: only trade trending markets (ADX > threshold).
            # Skip choppy/rangebound markets where breakout strategies fail.
            try:
                candles = self.engine.get_candles(venue, symbol, timeframe, 30)
                if candles and len(candles) >= 30:
                    closes = [float(c["close"]) for c in candles]
                    highs = [float(c["high"]) for c in candles]
                    lows = [float(c["low"]) for c in candles]
                    adx = self._calc_adx(highs, lows, closes, 14)
                    if adx is not None and adx < self.adx_threshold:
                        logger.info(
                            f"[STRATEX] Skip {symbol}: ADX {adx:.1f} < "
                            f"{self.adx_threshold} (choppy market, no trend)"
                        )
                        return False
            except Exception as e:
                logger.debug(f"[STRATEX] ADX calc failed for {symbol}: {e}")

            target_pct = abs(take_profit - current_price) / current_price
            rr_actual = target_pct / stop_pct if stop_pct > 0 else 0.0
            fee_r = self.fee_rate_roundtrip / stop_pct
            expectancy_r = win_rate * rr_actual - (1.0 - win_rate) - fee_r

            if expectancy_r < self.min_expectancy_r:
                logger.info(
                    f"[STRATEX] Skip {symbol}: {strategy_name} expectancy "
                    f"{expectancy_r:+.3f}R < {self.min_expectancy_r:+.3f}R "
                    f"(p={win_rate:.2f} rr={rr_actual:.2f} fee={fee_r:.2f}R)"
                )
                return False

            # Calculate position size based on risk
            risk_summary = self.engine.get_risk_summary()
            equity = float(risk_summary.get("total_equity", 0) or 0)
            if equity <= 0:
                logger.warning(f"[STRATEX] Equity unavailable ({equity}); skipping {symbol}")
                return False

            risk_amount = equity * self.risk_per_trade
            risk_per_unit = abs(current_price - stop_loss)
            if risk_per_unit <= 0:
                logger.warning(f"[STRATEX] Invalid stop distance for {symbol}")
                return False

            position_size = risk_amount / risk_per_unit

            # Hard exposure cap: one position may never exceed max_position_pct of
            # equity in notional terms. Without this a tight stop produces an
            # enormous size (risk $53 with a 1% stop on BTC = $5,300 notional).
            max_notional = equity * self.max_position_pct
            if current_price > 0:
                position_size = min(position_size, max_notional / current_price)

            if venue == "binance_futures":
                adapter = self.engine.venues.get("binance_futures")
                if adapter:
                    position_size = adapter.round_quantity(symbol, position_size)
                    min_notional = adapter.min_notional(symbol)
                    notional = position_size * current_price
                    if min_notional > 0 and notional < min_notional:
                        bumped = adapter.round_quantity(
                            symbol, (min_notional / current_price) * 1.01
                        )
                        if bumped * current_price <= max_notional:
                            position_size = bumped
                        else:
                            logger.info(
                                f"[STRATEX] Skip {symbol}: venue min notional "
                                f"{min_notional:.2f} > exposure cap {max_notional:.2f}"
                            )
                            return False
                if position_size <= 0:
                    return False
            elif venue == "oanda":
                units = max(1, round(position_size))
                notional = units * current_price
                if notional > max_notional:
                    logger.info(
                        f"[STRATEX] Skip {symbol}: minimum OANDA unit notional "
                        f"${notional:,.2f} > exposure cap ${max_notional:,.2f}"
                    )
                    return False
                position_size = units

            # Execute
            result = self.engine.place_order(
                venue=venue,
                symbol=symbol,
                side=side,
                order_type="MARKET",
                quantity=position_size,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy_name=strategy_name,
                timeframe=timeframe,
            )

            if result is None:
                return False
            if result.status == "FILLED":
                return True
            # A PENDING order is not tracked by the engine, so counting it as
            # executed would orphan its SL/TP and lose the PnL attribution.
            logger.warning(
                f"[STRATEX] Order for {symbol} ended {result.status}; not tracked"
            )
            return False

        except Exception as e:
            logger.error(f"Execution error: {e}")
            return False

    def _maybe_prune_strategies(self) -> None:
        """Move strategies with negative expectancy to denylist."""
        try:
            from trading_report import metrics
            stats = metrics()  # reads testnet_trade_ledger.jsonl
            for strat, bucket in stats.get("by_strategy", {}).items():
                if bucket.get("trades", 0) >= 10 and bucket.get("net_pnl", 0) < 0:
                    # Negative expectancy after 10+ trades -> denylist
                    if strat not in self.strategy_denylist:
                        self.strategy_denylist.add(strat)
                        logger.warning(
                            f"[STRATEX] Auto-pruned losing strategy: {strat} "
                            f"(trades={bucket['trades']}, net=${bucket['net_pnl']:.2f})"
                        )
                        # Persist denylist
                        self._persist_denylist()
        except Exception as e:
            logger.debug(f"Strategy pruning failed: {e}")

    def _persist_denylist(self) -> None:
        """Write current denylist to STRATEX_STRATEGY_DENYLIST_FILE."""
        try:
            path = os.getenv(
                "STRATEX_STRATEGY_DENYLIST_FILE",
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "state", "strategy_denylist.json"),
            )
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(sorted(self.strategy_denylist), fh, indent=2)
        except Exception as e:
            logger.debug(f"Denylist persist failed: {e}")

    def run(self):
        """Main trading loop."""
        logger.info(f"[STRATEX] Trading engine started - scanning every {self.scan_interval}s")
        
        while self.running:
            try:
                cycle_start = time.time()
                
                # Run scan and execute
                results = self.scan_and_execute()
                
                if results["executed"] > 0:
                    logger.info(f"[STRATEX] Cycle {self.scan_count}: {results}")
                
                # Sleep for remainder of interval
                elapsed = time.time() - cycle_start
                sleep_time = max(1, self.scan_interval - elapsed)
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                logger.info("[STRATEX] Shutdown signal received")
                break
            except Exception as e:
                logger.error(f"[STRATEX] Loop error: {e}", exc_info=True)
                time.sleep(5)


def main() -> None:
    logger.info("=== STARTING STRATEX 24/7 AUTONOMOUS AI TRADING ENGINE ===")
    
    # Initialize Telegram
    telegram = get_telegram_bot()
    
    # Initialize multi-venue engine with Telegram bot
    engine = create_multi_venue_engine(
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_secret=os.getenv("BINANCE_API_SECRET", ""),
        binance_testnet=os.getenv("BINANCE_TESTNET", "true").lower() in ("1", "true", "yes"),
        oanda_api_key=os.getenv("OANDA_API_KEY", ""),
        oanda_account_id=os.getenv("OANDA_ACCOUNT_ID", ""),
        oanda_practice=os.getenv("OANDA_PRACTICE", "true").lower() in ("1", "true", "yes"),
        starting_balance=float(os.getenv("STRATEX_INITIAL_BALANCE", "1000.0")),
        max_positions_per_venue=int(os.getenv("STRATEX_MAX_POSITIONS_PER_VENUE", "5")),
        max_total_positions=int(os.getenv("STRATEX_MAX_TOTAL_POSITIONS", "10")),
        risk_per_trade_pct=float(os.getenv("STRATEX_RISK_PER_TRADE_PCT", "0.005")),
        max_daily_loss_pct=float(os.getenv("STRATEX_MAX_DAILY_LOSS_PCT", "0.03")),
        telegram_bot=telegram,
    )

    # Track positions that were already open before this process started, so a
    # broker-side SL/TP fill is still detected and reported after a restart.
    try:
        engine.adopt_existing_positions()
    except Exception as e:
        logger.warning(f"[STRATEX_DAEMON] Position adoption failed: {e}")

    # Initialize trading engine
    trading_engine = StratexTradingEngine(engine)

    # Setup Telegram
    if not telegram.is_configured:
        logger.warning("[STRATEX_DAEMON] Telegram bot is not configured. Outbound alerts will be suppressed.")
    else:
        logger.info("[STRATEX_DAEMON] Starting Telegram bot background listener daemon...")
        telegram.start_polling_in_background()
        telegram.send_message(
            "🚀 <b>Stratex Online — Multi-Venue</b>\n\n"
            "• Venues: Binance Futures (Crypto) + OANDA (Forex/Indices/Commodities)\n"
            "• Balance: Real-time from venues\n"
            "• SL/TP: Native on orders\n"
            "• Strategies: 6 active (Trend, Mean Reversion, VWAP, Scalping, Pairs, ADX)\n"
            "• Risk: 0.5% per trade, 3% daily loss limit\n\n"
            "<i>Send /status, /positions, /balance, /scan for details.</i>"
        )

    # Handle shutdown gracefully
    def signal_handler(signum, frame):
        logger.info("[STRATEX_DAEMON] Shutdown signal received")
        trading_engine.running = False
        telegram.stop_polling()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Send startup heartbeat
    try:
        risk = engine.get_risk_summary()
        telegram.send_message(
            f"📡 <b>Stratex Started</b>\n"
            f"💰 Balance: <code>${risk['total_balance']:,.2f}</code>\n"
            f"📈 Max Positions: <code>{risk['max_positions']}</code>\n"
            f"⚠️ Daily Loss Limit: <code>{risk.get('daily_loss_limit_hit', False)}</code>"
        )
    except Exception:
        pass

    # Run main trading loop
    trading_engine.run()
    
    logger.info("[STRATEX_DAEMON] Shutdown complete")


if __name__ == "__main__":
    main()