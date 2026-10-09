"""
research/strategy_factory/mass_backtester.py

Fast vectorized/event backtester for all strategy variations in strategy_variations.json:
- Downloads historical data for BTCUSDT, ETHUSDT, SOLUSDT (5m, 15m, 1h)
- Evaluates signals across all 204 variations
- Applies 8 bps futures round-trip friction (0.02% maker entry + 0.04% taker exit + 2 bps slippage)
- Ranks by Net Profit Factor
- Outputs research/strategy_factory/factory_results.md
"""

import json
import hashlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from atomic_io import atomic_write_bytes, atomic_write_text, locked_path
from data_client import MarketDataClient


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))


def compute_stoch(df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth: int = 3):
    low_min = df['low'].rolling(window=k_period).min()
    high_max = df['high'].rolling(window=k_period).max()
    fast_k = 100 * (df['close'] - low_min) / (high_max - low_min + 1e-12)
    k = fast_k.rolling(window=smooth).mean()
    d = k.rolling(window=d_period).mean()
    return k, d


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def load_market_data(symbols, timeframes, start_str="2025-01-01"):
    client = MarketDataClient()
    cache_dir = Path("data_cache/factory_data")
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = {}
    required = {"open", "high", "low", "close", "volume"}

    for sym in symbols:
        for tf in timeframes:
            cache_file = cache_dir / f"{sym}_{tf}.csv"
            provenance_file = cache_file.with_suffix(cache_file.suffix + ".json")
            if cache_file.exists():
                if not provenance_file.exists():
                    raise RuntimeError(
                        f"Unverified research cache {cache_file}: missing provenance sidecar. "
                        "Move it aside and fetch real exchange candles again before backtesting."
                    )
                try:
                    metadata = json.loads(provenance_file.read_text(encoding="utf-8"))
                    digest = hashlib.sha256(cache_file.read_bytes()).hexdigest()
                    if metadata.get("sha256") != digest:
                        raise RuntimeError(f"Research cache hash mismatch for {cache_file}")
                    if metadata.get("symbol") != sym or metadata.get("timeframe") != tf:
                        raise RuntimeError(f"Research cache identity mismatch for {cache_file}")
                    if metadata.get("requested_start") != start_str:
                        raise RuntimeError(f"Research cache start-date mismatch for {cache_file}")
                    if not metadata.get("source"):
                        raise RuntimeError(f"Research cache source is missing for {cache_file}")
                    df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                    # Canonical index resolution: pandas may parse CSV dates at a
                    # different unit (us/ns) than the fetch path (ms). Normalize
                    # so cache round-trips compare equal and hashes stay stable.
                    if hasattr(df.index, "as_unit"):
                        df.index = df.index.as_unit("us")
                    if metadata.get("rows") != len(df):
                        raise RuntimeError(f"Research cache row-count mismatch for {cache_file}")
                    print(
                        f"Loaded verified {sym} {tf} ({len(df)} bars; "
                        f"source={metadata['source']}; sha256={digest[:12]})"
                    )
                except (OSError, ValueError, KeyError) as exc:
                    raise RuntimeError(f"Could not verify research cache {cache_file}: {exc}") from exc
            else:
                print(f"Fetching {sym} {tf} from {start_str}...")
                try:
                    raw_klines = client.futures_historical_klines(sym, tf, start_str=start_str)
                    if not raw_klines:
                        raise RuntimeError("exchange returned no historical candles")
                    df = pd.DataFrame(raw_klines, columns=[
                        'timestamp', 'open', 'high', 'low', 'close', 'volume',
                        'close_time', 'quote_asset_volume', 'number_of_trades',
                        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
                    ])
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = df[col].astype(float)
                    df.set_index('timestamp', inplace=True)
                    # Match the cache-read path's canonical resolution (see note
                    # there) so first-load and cached loads are byte-comparable.
                    if hasattr(df.index, "as_unit"):
                        df.index = df.index.as_unit("us")
                except Exception as e:
                    raise RuntimeError(
                        f"Cannot run a research backtest for {sym} {tf}: real exchange candles "
                        f"could not be fetched ({e}). No substitute data was generated."
                    ) from e

                missing = required.difference(df.columns)
                if df.empty or missing:
                    raise RuntimeError(
                        f"Invalid real candle response for {sym} {tf}: "
                        f"rows={len(df)}, missing_columns={sorted(missing)}"
                    )
                csv_bytes = df.to_csv().encode("utf-8")
                digest = hashlib.sha256(csv_bytes).hexdigest()
                source = getattr(client, "data_source", None)
                if not source:
                    raise RuntimeError(
                        f"Cannot save {sym} {tf} candles without an identified exchange data source"
                    )
                metadata = {
                    "symbol": sym,
                    "timeframe": tf,
                    "requested_start": start_str,
                    "source": source,
                    "fetched_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                    "rows": len(df),
                    "sha256": digest,
                }
                # Unique temp files + one lock per cache entry: concurrent
                # fetchers can no longer interleave writes to a shared .tmp.
                # A crash between the two replaces leaves a sha256 mismatch,
                # which the read path above rejects (fail closed).
                with locked_path(cache_file):
                    atomic_write_bytes(cache_file, csv_bytes)
                    atomic_write_text(provenance_file, json.dumps(metadata, indent=2))
                print(
                    f"Downloaded & saved verified {sym} {tf} ({len(df)} bars; "
                    f"source={source}; sha256={digest[:12]})"
                )

            missing = required.difference(df.columns)
            if df.empty or missing:
                raise RuntimeError(
                    f"Invalid candle cache for {sym} {tf}: rows={len(df)}, "
                    f"missing_columns={sorted(missing)}"
                )
            # Keep a stable, minimal schema independent of the exchange SDK's
            # inferred types for unused kline columns.
            df = df.loc[:, ["open", "high", "low", "close", "volume"]].astype(float)
            data[(sym, tf)] = df
    return data


def backtest_strategy_on_df(df: pd.DataFrame, strat: dict, friction_bps: float = 8.0):
    stype = strat["type"]
    params = strat["params"]
    exits = strat["exits"]
    sl_mult = exits["sl_atr"]
    tp_mult = exits["tp_atr"]

    df = df.copy()
    df['atr'] = compute_atr(df, 14)

    # Compute indicator signals
    long_signals = pd.Series(False, index=df.index)
    short_signals = pd.Series(False, index=df.index)

    if stype == "ema_crossover":
        f_ema = df['close'].ewm(span=params["fast_ema"], adjust=False).mean()
        s_ema = df['close'].ewm(span=params["slow_ema"], adjust=False).mean()
        long_signals = (f_ema > s_ema) & (f_ema.shift(1) <= s_ema.shift(1))
        short_signals = (f_ema < s_ema) & (f_ema.shift(1) >= s_ema.shift(1))

    elif stype == "rsi_mean_reversion":
        rsi = compute_rsi(df['close'], params["rsi_period"])
        long_signals = (rsi.shift(1) < params["rsi_lower"]) & (rsi >= params["rsi_lower"])
        short_signals = (rsi.shift(1) > params["rsi_upper"]) & (rsi <= params["rsi_upper"])

    elif stype == "bb_break_reenter":
        mid = df['close'].rolling(window=params["bb_period"]).mean()
        std = df['close'].rolling(window=params["bb_period"]).std()
        upper = mid + (params["bb_std"] * std)
        lower = mid - (params["bb_std"] * std)
        long_signals = (df['low'].shift(1) < lower.shift(1)) & (df['close'] > lower)
        short_signals = (df['high'].shift(1) > upper.shift(1)) & (df['close'] < upper)

    elif stype == "macd_crossover":
        macd, sig = compute_macd(df['close'], params["macd_fast"], params["macd_slow"], params["macd_signal"])
        long_signals = (macd > sig) & (macd.shift(1) <= sig.shift(1))
        short_signals = (macd < sig) & (macd.shift(1) >= sig.shift(1))

    elif stype == "stoch_crossover":
        k, d = compute_stoch(df, params["stoch_k"], params["stoch_d"], params["stoch_smooth"])
        long_signals = (k > d) & (k.shift(1) <= d.shift(1)) & (k < params["stoch_upper"])
        short_signals = (k < d) & (k.shift(1) >= d.shift(1)) & (k > params["stoch_lower"])

    elif stype == "ema_rsi_confluence":
        f_ema = df['close'].ewm(span=params["fast_ema"], adjust=False).mean()
        s_ema = df['close'].ewm(span=params["slow_ema"], adjust=False).mean()
        rsi = compute_rsi(df['close'], params["rsi_period"])
        long_signals = (f_ema > s_ema) & (f_ema.shift(1) <= s_ema.shift(1)) & (rsi < params["rsi_long_max"])
        short_signals = (f_ema < s_ema) & (f_ema.shift(1) >= s_ema.shift(1)) & (rsi > params["rsi_short_min"])

    elif stype == "macd_bb_confluence":
        macd, sig = compute_macd(df['close'], params["macd_fast"], params["macd_slow"], params["macd_signal"])
        mid = df['close'].rolling(window=params["bb_period"]).mean()
        std = df['close'].rolling(window=params["bb_period"]).std()
        upper = mid + (params["bb_std"] * std)
        lower = mid - (params["bb_std"] * std)
        long_signals = (macd > sig) & (macd.shift(1) <= sig.shift(1)) & (df['close'] < mid)
        short_signals = (macd < sig) & (macd.shift(1) >= sig.shift(1)) & (df['close'] > mid)

    elif stype == "stoch_ema_confluence":
        f_ema = df['close'].ewm(span=params["fast_ema"], adjust=False).mean()
        s_ema = df['close'].ewm(span=params["slow_ema"], adjust=False).mean()
        k, d = compute_stoch(df, params["stoch_k"], params["stoch_d"], params["stoch_smooth"])
        long_signals = (k > d) & (k.shift(1) <= d.shift(1)) & (df['close'] > s_ema)
        short_signals = (k < d) & (k.shift(1) >= d.shift(1)) & (df['close'] < s_ema)

    # Iterate signals and simulate intrabar trades
    trades = []
    in_pos = False
    entry_p, sl_p, tp_p, side = 0.0, 0.0, 0.0, ""

    friction_rate = friction_bps / 10000.0

    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    atrs = df['atr'].values
    longs = long_signals.values
    shorts = short_signals.values

    n = len(df)
    for i in range(30, n):
        if not in_pos:
            if longs[i - 1] and atrs[i - 1] > 0:
                in_pos = True
                side = "LONG"
                entry_p = closes[i]
                sl_p = entry_p - (sl_mult * atrs[i - 1])
                tp_p = entry_p + (tp_mult * atrs[i - 1])
            elif shorts[i - 1] and atrs[i - 1] > 0:
                in_pos = True
                side = "SHORT"
                entry_p = closes[i]
                sl_p = entry_p + (sl_mult * atrs[i - 1])
                tp_p = entry_p - (tp_mult * atrs[i - 1])
        else:
            # Check exit
            h = highs[i]
            l = lows[i]
            closed = False
            pnl_pct = 0.0

            if side == "LONG":
                if l <= sl_p:
                    pnl_pct = (sl_p - entry_p) / entry_p - friction_rate
                    closed = True
                elif h >= tp_p:
                    pnl_pct = (tp_p - entry_p) / entry_p - friction_rate
                    closed = True
            elif side == "SHORT":
                if h >= sl_p:
                    pnl_pct = (entry_p - sl_p) / entry_p - friction_rate
                    closed = True
                elif l <= tp_p:
                    pnl_pct = (entry_p - tp_p) / entry_p - friction_rate
                    closed = True

            if closed:
                trades.append(pnl_pct)
                in_pos = False

    return trades


def run_mass_backtest():
    var_file = Path("research/strategy_factory/strategy_variations.json")
    with open(var_file, "r") as f:
        variations = json.load(f)

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    timeframes = ["5m", "15m", "1h"]

    print(f"Loading data for symbols {symbols} on timeframes {timeframes}...")
    market_data = load_market_data(symbols, timeframes, start_str="2024-01-01")

    results = []
    print(f"Running mass backtest for {len(variations)} strategy variations...")

    for idx, strat in enumerate(variations, 1):
        tf = strat["timeframe"]
        all_trades = []
        for sym in symbols:
            df = market_data.get((sym, tf))
            if df is not None and len(df) > 50:
                trades = backtest_strategy_on_df(df, strat, friction_bps=8.0)
                all_trades.extend(trades)

        if not all_trades:
            continue

        wins = [t for t in all_trades if t > 0]
        losses = [t for t in all_trades if t < 0]
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 1e-9
        net_pf = gross_profit / gross_loss if gross_loss > 0 else 0.0
        win_rate = len(wins) / len(all_trades) if all_trades else 0.0

        res = {
            "id": strat["id"],
            "name": strat["name"],
            "type": strat["type"],
            "timeframe": strat["timeframe"],
            "params": strat["params"],
            "exits": strat["exits"],
            "total_trades": len(all_trades),
            "win_rate": round(win_rate, 4),
            "net_pf": round(net_pf, 3),
            "total_return_pct": round(sum(all_trades) * 100, 2),
            "entry_logic": strat["entry_logic"]
        }
        results.append(res)
        if idx % 25 == 0:
            print(f"Evaluated {idx}/{len(variations)} strategies...")

    # Rank by Net Profit Factor
    results.sort(key=lambda x: (x["net_pf"], x["win_rate"]), reverse=True)

    # Save Markdown report
    report_file = Path("research/strategy_factory/factory_results.md")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# Strategy Factory Mass Backtest Report\n\n")
        f.write(f"**Total Variations Tested**: {len(variations)}\n")
        f.write("**Assets**: BTCUSDT, ETHUSDT, SOLUSDT\n")
        f.write("**Friction Model**: 8 bps round-trip Maker/Taker Futures model\n\n")
        f.write("## Top 10 Winning Strategies\n\n")
        f.write("| Rank | Strategy Name | Timeframe | Type | Trades | Win Rate | Net PF | Net Return % |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")
        f.writelines(f"| {rank} | `{r['name']}` | {r['timeframe']} | {r['type']} | {r['total_trades']} | {r['win_rate']*100:.1f}% | **{r['net_pf']:.2f}** | {r['total_return_pct']:+.1f}% |\n" for rank, r in enumerate(results[:10], 1))

        f.write("\n\n## Top 5 Candidates for Further Validation\n\n")
        f.write("These are in-sample rankings only. They are not approved for deployment and do not establish profitability.\n\n")
        for rank, r in enumerate(results[:5], 1):
            f.write(f"### Winner #{rank}: `{r['name']}`\n")
            f.write(f"- **Timeframe**: {r['timeframe']}\n")
            f.write(f"- **Logic**: {r['entry_logic']}\n")
            f.write(f"- **Parameters**: `{json.dumps(r['params'])}`\n")
            f.write(f"- **Exits**: SL = {r['exits']['sl_atr']}x ATR, TP = {r['exits']['tp_atr']}x ATR (RR = {r['exits']['rr_ratio']})\n")
            f.write(f"- **Performance**: Net PF = **{r['net_pf']}**, Win Rate = **{r['win_rate']*100:.1f}%**, Trades = **{r['total_trades']}**\n\n")

    # Save top winners to JSON
    with open("research/strategy_factory/top_winners.json", "w") as f:
        json.dump(results[:10], f, indent=2)

    print(f"\nMass Backtest Complete! Report saved to {report_file}")
    print("\nTop 5 Winners:")
    for rank, r in enumerate(results[:5], 1):
        print(f"#{rank} {r['name']} ({r['timeframe']}): Net PF {r['net_pf']} | Win Rate {r['win_rate']*100:.1f}% | Trades {r['total_trades']}")

    return results[:5]


if __name__ == "__main__":
    run_mass_backtest()
