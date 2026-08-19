import re

with open('static/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

settings_html = """            <!-- ═══════════════════════════════════════════════════════════════
                 VIEW: SETTINGS
            ═══════════════════════════════════════════════════════════════ -->
            <div class="view-container" id="view-settings">
                <div class="page-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <h1 class="page-title">SETTINGS</h1>
                    <div class="scanner-toolbar" style="display: flex; gap: 12px; align-items: center;">
                        <button class="btn-terminal" onclick="saveSettings()" style="border: 1px solid var(--accent-primary); background: var(--accent-primary-dim); padding: 4px 16px; font-size: 11px; font-family: var(--font-mono); color: var(--accent-primary); cursor: pointer; font-weight: 700;">SAVE</button>
                        <button class="btn-terminal" onclick="resetSettings()" style="border: 1px solid var(--loss-red); background: rgba(239, 68, 68, 0.1); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--loss-red); cursor: pointer;">RESET</button>
                        <button class="btn-terminal" onclick="fetchSettings()" style="border: 1px solid var(--border-medium); background: var(--bg-input); padding: 4px 12px; font-size: 11px; font-family: var(--font-mono); color: var(--text-secondary); cursor: pointer;">↻</button>
                    </div>
                </div>

                <!-- TRADING MODE -->
                <div class="panel-card" style="margin-bottom: 16px; padding: 16px;">
                    <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 16px; letter-spacing: 1px;">
                        TRADING MODE
                    </div>
                    <div style="display: flex; gap: 32px; margin-bottom: 16px; flex-wrap: wrap;">
                        <div class="kv-row" style="flex: 1;"><span>ENVIRONMENT</span><span class="mono text-secondary">TESTNET</span></div>
                        <div class="kv-row" style="flex: 1;"><span>EXCHANGE</span><span class="mono text-secondary">BINANCE</span></div>
                        <div class="kv-row" style="flex: 1;"><span>MODE</span><span class="mono text-secondary">AUTOMATIC</span></div>
                        <div class="kv-row" style="flex: 1;"><span>EXECUTION</span><span class="mono text-secondary">SPOT</span></div>
                        <div class="kv-row" style="flex: 1;"><span>LIVE TRADING</span><span class="mono text-muted">● DISABLED</span></div>
                    </div>
                    
                    <div style="border: 1px solid var(--border-medium); background: var(--bg-base); padding: 16px; position: relative;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: #F59E0B;">MANUAL TRADING MODE</div>
                            <label class="switch-terminal" style="cursor: pointer; display: flex; align-items: center; gap: 8px;">
                                <input type="checkbox" id="set-manual-trade" onchange="toggleManualTrading()" style="accent-color: #F59E0B;">
                                <span class="mono" id="lbl-manual-trade" style="color: var(--text-muted);">○ OFF</span>
                            </label>
                        </div>
                        <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 12px;">
                            Enable manual control of TESTNET positions and orders.<br>
                            When enabled: BUY · SELL · CLOSE POSITION · CLOSE BOT TRADE · CANCEL ORDER
                        </div>
                        <div id="manual-actions-row" style="display: flex; gap: 12px; opacity: 0.5; pointer-events: none;">
                            <button class="btn-terminal" style="border: 1px solid var(--profit-green); background: rgba(16, 185, 129, 0.1); color: var(--profit-green); padding: 4px 16px;">BUY</button>
                            <button class="btn-terminal" style="border: 1px solid var(--loss-red); background: rgba(239, 68, 68, 0.1); color: var(--loss-red); padding: 4px 16px;">SELL</button>
                            <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); color: var(--text-primary); padding: 4px 16px;">CLOSE POSITION</button>
                            <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); color: var(--text-primary); padding: 4px 16px;">CLOSE BOT TRADE</button>
                            <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); color: var(--text-primary); padding: 4px 16px;">CANCEL ORDER</button>
                        </div>
                    </div>
                </div>

                <!-- LIMITS -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px;">
                    <div class="panel-card" style="padding: 16px;">
                        <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 16px; letter-spacing: 1px;">
                            TRADE LIMITS
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 12px;">
                            <div class="kv-row" style="align-items: center;"><span>MAX OPEN TRADES</span><input type="number" id="set-max-open" class="input-terminal mono text-right" value="5" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>MAX TRADES / DAY</span><input type="number" id="set-max-day" class="input-terminal mono text-right" value="50" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>MAX TRADES / SYMBOL</span><input type="number" id="set-max-sym" class="input-terminal mono text-right" value="1" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>MAX TRADES / STRATEGY</span><input type="number" id="set-max-strat" class="input-terminal mono text-right" value="3" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>COOLDOWN AFTER TRADE</span><input type="text" id="set-cd-trade" class="input-terminal mono text-right" value="5m" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>SYMBOL COOLDOWN</span><input type="text" id="set-cd-sym" class="input-terminal mono text-right" value="5m" style="width: 80px;"></div>
                        </div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 16px; letter-spacing: 1px;">
                            RISK LIMITS
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 12px;">
                            <div class="kv-row" style="align-items: center;"><span>RISK PER TRADE</span><input type="text" id="set-risk-trade" class="input-terminal mono text-right" value="0.50%" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>MAX PORTFOLIO RISK</span><input type="text" id="set-risk-port" class="input-terminal mono text-right" value="5.00%" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>MAX PORTFOLIO EXPOSURE</span><input type="text" id="set-exp-port" class="input-terminal mono text-right" value="5.00%" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>MAX DRAWDOWN</span><input type="text" id="set-max-dd" class="input-terminal mono text-right" value="5.00%" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>DAILY LOSS LIMIT</span><input type="text" id="set-daily-loss" class="input-terminal mono text-right" value="$100" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>MAX OPEN POSITIONS</span><input type="number" id="set-max-pos" class="input-terminal mono text-right" value="5" style="width: 80px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>DUPLICATE PROTECTION</span><select id="set-dup-prot" class="select-terminal mono text-right" style="width: 80px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>SAFETY HALT</span><select id="set-safe-halt" class="select-terminal mono text-right" style="width: 80px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        </div>
                    </div>
                </div>

                <!-- STRATEGIES -->
                <div class="panel-card table-card" style="margin-bottom: 16px;">
                    <div style="padding: 12px 16px; font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); border-bottom: 1px solid var(--border-medium); letter-spacing: 1px;">
                        STRATEGIES & TIMEFRAMES
                    </div>
                    <div class="panel-table-wrap">
                        <table class="terminal-table table-dense" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th style="width: 25%;">STRATEGY</th>
                                    <th style="width: 15%;">STATUS</th>
                                    <th style="width: 60%;">ACTIVE TIMEFRAMES</th>
                                </tr>
                            </thead>
                            <tbody id="set-strat-body">
                                <tr>
                                    <td class="td-strong">ADX_EMA</td>
                                    <td class="mono profit">● ON</td>
                                    <td class="mono">5m · 15m · 30m · 1h · 2h · 4h</td>
                                </tr>
                                <tr>
                                    <td class="td-strong">ML</td>
                                    <td class="mono profit">● ON</td>
                                    <td class="mono">5m · 15m</td>
                                </tr>
                                <tr>
                                    <td class="td-strong">SCALPER</td>
                                    <td class="mono profit">● ON</td>
                                    <td class="mono">5m</td>
                                </tr>
                                <tr>
                                    <td class="td-strong">AGGRESSOR</td>
                                    <td class="mono profit">● ON</td>
                                    <td class="mono">5m · 15m</td>
                                </tr>
                                <tr>
                                    <td class="td-strong">SWING</td>
                                    <td class="mono profit">● ON</td>
                                    <td class="mono">30m · 1h</td>
                                </tr>
                                <tr>
                                    <td class="td-strong text-muted">SUPERTREND</td>
                                    <td class="mono text-muted">● OFF</td>
                                    <td class="mono text-muted">15m · 1h</td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <div style="padding: 12px 16px; border-top: 1px solid var(--border-medium); display: flex; gap: 12px;">
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); color: var(--text-primary); padding: 4px 12px;">ENABLE / DISABLE STRATEGY</button>
                        <button class="btn-terminal" style="border: 1px solid var(--border-medium); background: var(--bg-input); color: var(--text-primary); padding: 4px 12px;">ADD / REMOVE TIMEFRAME</button>
                    </div>
                </div>

                <!-- PROFITABILITY & EXECUTION -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px;">
                    <div class="panel-card" style="padding: 16px;">
                        <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 16px; letter-spacing: 1px;">
                            PROFITABILITY
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 12px;">
                            <div class="kv-row" style="align-items: center;"><span>MIN EXPECTED NET RETURN</span><input type="text" id="set-min-net" class="input-terminal mono text-right" value="0.05%" style="width: 100px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>MINIMUM EDGE</span><input type="text" id="set-min-edge" class="input-terminal mono text-right" value="0.10%" style="width: 100px;"></div>
                            <div class="kv-row" style="align-items: center;"><span>PROFITABILITY GATE</span><select id="set-prof-gate" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>FEE MODEL</span><select id="set-fee-mod" class="select-terminal mono text-right" style="width: 100px;"><option value="BINANCE">BINANCE</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>PROFITABILITY BUFFER</span><input type="text" id="set-prof-buf" class="input-terminal mono text-right" value="0.00%" style="width: 100px;"></div>
                        </div>
                    </div>
                    <div class="panel-card" style="padding: 16px;">
                        <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 16px; letter-spacing: 1px;">
                            EXECUTION
                        </div>
                        <div style="display: flex; flex-direction: column; gap: 12px;">
                            <div class="kv-row" style="align-items: center;"><span>EXECUTION MODE</span><select id="set-exec-mod" class="select-terminal mono text-right" style="width: 100px;"><option value="AUTO">AUTO</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>ORDER TYPE</span><select id="set-ord-typ" class="select-terminal mono text-right" style="width: 100px;"><option value="MARKET">MARKET</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>SLIPPAGE MODEL</span><select id="set-slip-mod" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>SPREAD MODEL</span><select id="set-spread-mod" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>BINANCE FILTER CHECK</span><select id="set-bin-fil" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>MIN NOTIONAL CHECK</span><select id="set-min-not" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>STOP LOSS</span><select id="set-exec-sl" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>TAKE PROFIT</span><select id="set-exec-tp" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>PROTECTION</span><select id="set-exec-prot" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                            <div class="kv-row" style="align-items: center;"><span>AUTO RETRY</span><select id="set-exec-retry" class="select-terminal mono text-right" style="width: 100px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        </div>
                    </div>
                </div>

                <!-- POSITION & PROTECTION SETTINGS -->
                <div class="panel-card" style="margin-bottom: 16px; padding: 16px;">
                    <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 16px; letter-spacing: 1px;">
                        POSITION & PROTECTION SETTINGS
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
                        <div class="kv-row" style="align-items: center;"><span>DEFAULT STOP LOSS</span><select id="set-def-sl" class="select-terminal mono text-right" style="width: 120px;"><option value="STRATEGY">STRATEGY</option></select></div>
                        <div class="kv-row" style="align-items: center;"><span>DEFAULT TAKE PROFIT</span><select id="set-def-tp" class="select-terminal mono text-right" style="width: 120px;"><option value="STRATEGY">STRATEGY</option></select></div>
                        <div class="kv-row" style="align-items: center;"><span>MIN RISK / REWARD</span><input type="text" id="set-min-rr" class="input-terminal mono text-right" value="1.50" style="width: 120px;"></div>
                        <div class="kv-row" style="align-items: center;"><span>MAX POSITION DURATION</span><input type="text" id="set-max-dur" class="input-terminal mono text-right" value="0" style="width: 120px;"></div>
                        <div class="kv-row" style="align-items: center;"><span>TRAILING STOP</span><select id="set-trail-stop" class="select-terminal mono text-right" style="width: 120px;"><option value="OFF">OFF</option></select></div>
                        <div class="kv-row" style="align-items: center;"><span>BREAK-EVEN</span><select id="set-break-even" class="select-terminal mono text-right" style="width: 120px;"><option value="OFF">OFF</option></select></div>
                        <div class="kv-row" style="align-items: center;"><span>PARTIAL CLOSE</span><select id="set-part-close" class="select-terminal mono text-right" style="width: 120px;"><option value="OFF">OFF</option></select></div>
                        <div class="kv-row" style="align-items: center;"><span>SESSION AUTO CLOSE</span><select id="set-auto-close" class="select-terminal mono text-right" style="width: 120px;"><option value="OFF">OFF</option></select></div>
                    </div>
                </div>

                <!-- NOTIFICATIONS & SOUND -->
                <div class="panel-card" style="margin-bottom: 16px; padding: 16px;">
                    <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 16px; letter-spacing: 1px;">
                        NOTIFICATIONS & SOUND
                    </div>
                    <div style="display: flex; gap: 32px; margin-bottom: 16px; align-items: center; border-bottom: 1px solid var(--border-medium); padding-bottom: 16px;">
                        <div class="kv-row" style="width: 200px;"><span>NOTIFICATIONS</span><select id="set-glob-notif" class="select-terminal mono text-right" style="width: 80px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row" style="width: 180px;"><span>SOUND</span><select id="set-glob-sound" class="select-terminal mono text-right" style="width: 80px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row" style="width: 200px;"><span>SOUND VOLUME</span><input type="text" id="set-vol" class="input-terminal mono text-right" value="70%" style="width: 80px;"></div>
                        <div class="kv-row" style="width: 150px;"><span>TEST SOUND</span><button class="btn-terminal mono" style="padding: 4px 12px;" onclick="playAudioAlert('success')">▶</button></div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px;">
                        <div class="kv-row"><span>TRADE OPENED</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row"><span>TRADE CLOSED</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row"><span>TAKE PROFIT</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row"><span>STOP LOSS</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row"><span>ORDER FAILED</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row"><span>NEW SIGNAL</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row"><span>ENGINE OFFLINE</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row"><span>ENGINE RECOVERED</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                        <div class="kv-row"><span>SAFETY HALT</span><select class="select-terminal mono text-right" style="width: 60px;"><option value="ON">ON</option><option value="OFF">OFF</option></select></div>
                    </div>
                </div>

                <!-- TRADING UNIVERSE -->
                <div class="panel-card" style="margin-bottom: 16px; padding: 16px;">
                    <div style="font-family: var(--font-heading); font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 16px; letter-spacing: 1px;">
                        TRADING UNIVERSE
                    </div>
                    <div style="margin-bottom: 12px;">
                        <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 8px;">SYMBOLS</div>
                        <div style="display: flex; gap: 16px; flex-wrap: wrap;">
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> BTCUSDT</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> ETHUSDT</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> LINKUSDT</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> SOLUSDT</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> DOGEUSDT</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> BNBUSDT</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> XRPUSDT</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> ADAUSDT</label>
                        </div>
                    </div>
                    <div style="margin-bottom: 8px;">
                        <div style="font-family: var(--font-heading); font-size: 10px; font-weight: 700; color: var(--text-muted); margin-bottom: 8px;">TIMEFRAMES</div>
                        <div style="display: flex; gap: 16px; flex-wrap: wrap;">
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> 5m</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> 15m</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> 30m</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> 1h</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> 2h</label>
                            <label style="color: var(--text-primary); font-size: 11px;" class="mono"><input type="checkbox" checked style="accent-color: var(--accent-primary);"> 4h</label>
                        </div>
                    </div>
                </div>
            </div>
"""

start_marker = '<div class="view-container" id="view-settings">'
end_marker = '</main>'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker, start_idx)

if start_idx != -1 and end_idx != -1:
    sep_marker = '<!-- ════'
    sep_idx = content.rfind(sep_marker, 0, start_idx)
    if sep_idx != -1:
        start_idx = sep_idx

    final_html = content[:start_idx] + settings_html + '\n        ' + content[end_idx:]
    with open('static/index.html', 'w', encoding='utf-8') as f:
        f.write(final_html)
    print("Settings HTML successfully appended.")
else:
    print("Markers not found.")
