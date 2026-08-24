# Strategy Factory Mass Backtest Report

**Total Variations Tested**: 204
**Assets**: BTCUSDT, ETHUSDT, SOLUSDT
**Friction Model**: 8 bps round-trip Maker/Taker Futures model

## Top 10 Winning Strategies

| Rank | Strategy Name | Timeframe | Type | Trades | Win Rate | Net PF | Net Return % |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | `factory_macd_bb_confluence_5m_182` | 5m | macd_bb_confluence | 5403 | 43.1% | **1.48** | +1794.5% |
| 2 | `factory_macd_bb_confluence_5m_181` | 5m | macd_bb_confluence | 9264 | 41.3% | **1.45** | +1793.4% |
| 3 | `factory_macd_bb_confluence_5m_183` | 5m | macd_bb_confluence | 11338 | 33.4% | **1.43** | +1370.1% |
| 4 | `factory_macd_bb_confluence_5m_184` | 5m | macd_bb_confluence | 3731 | 41.0% | **1.39** | +1381.5% |
| 5 | `factory_macd_bb_confluence_15m_187` | 15m | macd_bb_confluence | 3907 | 30.5% | **1.36** | +652.9% |
| 6 | `factory_macd_bb_confluence_15m_186` | 15m | macd_bb_confluence | 2408 | 37.1% | **1.22** | +565.0% |
| 7 | `factory_stoch_crossover_15m_139` | 15m | stoch_crossover | 25412 | 28.3% | **1.19** | +2848.2% |
| 8 | `factory_macd_bb_confluence_15m_185` | 15m | macd_bb_confluence | 3302 | 37.5% | **1.18** | +466.2% |
| 9 | `factory_stoch_crossover_15m_151` | 15m | stoch_crossover | 27394 | 28.0% | **1.18** | +2815.2% |
| 10 | `factory_macd_bb_confluence_15m_188` | 15m | macd_bb_confluence | 1730 | 36.4% | **1.15** | +373.9% |


## Top 5 Strategies Selected for Live Deployment

### Winner #1: `factory_macd_bb_confluence_5m_182`
- **Timeframe**: 5m
- **Logic**: Confluence macd_bb_confluence on 5m with SL 1.5x ATR and TP 3.0x ATR
- **Parameters**: `{"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "bb_period": 20, "bb_std": 2.0}`
- **Exits**: SL = 1.5x ATR, TP = 3.0x ATR (RR = 2.0)
- **Performance**: Net PF = **1.481**, Win Rate = **43.1%**, Trades = **5403**

### Winner #2: `factory_macd_bb_confluence_5m_181`
- **Timeframe**: 5m
- **Logic**: Confluence macd_bb_confluence on 5m with SL 1.0x ATR and TP 2.0x ATR
- **Parameters**: `{"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "bb_period": 20, "bb_std": 2.0}`
- **Exits**: SL = 1.0x ATR, TP = 2.0x ATR (RR = 2.0)
- **Performance**: Net PF = **1.449**, Win Rate = **41.3%**, Trades = **9264**

### Winner #3: `factory_macd_bb_confluence_5m_183`
- **Timeframe**: 5m
- **Logic**: Confluence macd_bb_confluence on 5m with SL 0.5x ATR and TP 1.5x ATR
- **Parameters**: `{"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "bb_period": 20, "bb_std": 2.0}`
- **Exits**: SL = 0.5x ATR, TP = 1.5x ATR (RR = 3.0)
- **Performance**: Net PF = **1.433**, Win Rate = **33.4%**, Trades = **11338**

### Winner #4: `factory_macd_bb_confluence_5m_184`
- **Timeframe**: 5m
- **Logic**: Confluence macd_bb_confluence on 5m with SL 2.0x ATR and TP 4.0x ATR
- **Parameters**: `{"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "bb_period": 20, "bb_std": 2.0}`
- **Exits**: SL = 2.0x ATR, TP = 4.0x ATR (RR = 2.0)
- **Performance**: Net PF = **1.39**, Win Rate = **41.0%**, Trades = **3731**

### Winner #5: `factory_macd_bb_confluence_15m_187`
- **Timeframe**: 15m
- **Logic**: Confluence macd_bb_confluence on 15m with SL 0.5x ATR and TP 1.5x ATR
- **Parameters**: `{"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "bb_period": 20, "bb_std": 2.0}`
- **Exits**: SL = 0.5x ATR, TP = 1.5x ATR (RR = 3.0)
- **Performance**: Net PF = **1.361**, Win Rate = **30.5%**, Trades = **3907**

