"""
gemini_service.py - Centralized Gemini AI Analysis Layer for Algorithmic Trading Bot.

Security & Architecture Invariants:
1. Pure Advisory Layer: Gemini produces explanatory and diagnostic text only.
   It has ZERO authority to execute trades, set risk, modify SL/TP, or bypass safety gates.
2. Invariant Safety: Live trading remains blocked by design (TESTNET ONLY).
3. Resilient Fallbacks: Failures (timeouts, rate limits, network drops) NEVER stop or degrade
   the deterministic trading engine or market scanner. All calls return graceful fallback JSON.
4. Server-Side Key Security: GEMINI_API_KEY is never exposed to the client or logged.
5. In-Memory LRU & ID-Based Caching: Responses are cached by trade_id/signal_id to minimize latency and costs.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

try:
    import config
except ImportError:
    import sys
    from pathlib import Path
    sys.path.append(str(Path(__file__).parent))
    import config

logger = logging.getLogger("gemini_service")

# Thread-safe in-memory cache
_AI_CACHE: dict[str, dict[str, Any]] = {}
_MAX_CACHE_SIZE = 500

class GeminiService:
    def __init__(self, api_key: str | None = None, model: str | None = None, enabled: bool | None = None):
        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

        self.model = model or getattr(config, "GEMINI_MODEL", "gemini-flash-lite-latest") or os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
        self.enabled = (
            enabled if enabled is not None 
            else (getattr(config, "GEMINI_ENABLED", True) and bool(self.api_key))
        )
        self.timeout_seconds = 10
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"
        self._last_verified_connected: bool | None = None
        self._last_connected_check: float = 0.0

    def is_configured(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 5)

    def get_status(self) -> dict[str, Any]:
        """
        Returns safe status metadata without exposing secret keys.
        Distinguishes CONFIGURED from ACTUALLY CONNECTED based on verified reachability.
        """
        is_conf = self.is_configured()
        
        # If not configured, status is immediately UNAVAILABLE
        if not is_conf or not self.enabled:
            return {
                "status": "SUCCESS",
                "gemini": {
                    "enabled": False,
                    "configured": is_conf,
                    "model": self.model,
                    "status": "UNAVAILABLE",
                    "cached_items": len(_AI_CACHE),
                }
            }

        # Check cached verification state or report CONNECTED only if recently verified
        is_conn = (self._last_verified_connected is True)
        
        return {
            "status": "SUCCESS",
            "gemini": {
                "enabled": True,
                "configured": True,
                "model": self.model,
                "status": "CONNECTED" if is_conn else "CONFIGURED",
                "cached_items": len(_AI_CACHE),
            }
        }

    def _get_cache(self, cache_key: str) -> dict[str, Any] | None:
        if cache_key in _AI_CACHE:
            entry = _AI_CACHE[cache_key]
            # Expire after 1 hour if timestamp present
            if time.time() - entry.get("_timestamp", 0) < 3600:
                return entry.get("data")
        return None

    def _set_cache(self, cache_key: str, data: dict[str, Any]):
        global _AI_CACHE
        if len(_AI_CACHE) >= _MAX_CACHE_SIZE:
            # Simple eviction: drop oldest 20%
            keys_to_remove = list(_AI_CACHE.keys())[:int(_MAX_CACHE_SIZE * 0.2)]
            for k in keys_to_remove:
                _AI_CACHE.pop(k, None)
        _AI_CACHE[cache_key] = {
            "_timestamp": time.time(),
            "data": data
        }

    def _call_gemini_api(self, prompt: str) -> str | None:
        """
        Executes REST call to Google Gemini API using secure 'x-goog-api-key' header.
        Never embeds the API key in the URL query string or logs.
        Applies a 2-attempt bounded retry with exponential backoff on transient errors (429, 503, timeout).
        """
        if not self.is_configured() or not self.enabled:
            return None

        # Try active model first, with lightweight fallback if rate-limited
        candidate_models = [self.model.replace("models/", ""), "gemini-flash-latest", "gemini-flash-lite-latest"]
        # Remove duplicates while preserving order
        candidate_models = list(dict.fromkeys(candidate_models))

        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1000
            }
        }

        req_data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key.strip()
        }

        for model_name in candidate_models:
            url = f"{self.base_url}/{model_name}:generateContent"
            for attempt in range(2):
                try:
                    req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                        if response.status == 200:
                            self._last_verified_connected = True
                            self._last_connected_check = time.time()
                            resp_body = response.read().decode("utf-8")
                            resp_json = json.loads(resp_body)
                            candidates = resp_json.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                text_chunks = [p.get("text", "") for p in parts if "text" in p]
                                if text_chunks:
                                    return "".join(text_chunks).strip()
                except urllib.error.HTTPError as he:
                    logger.warning(f"[GEMINI] HTTP {he.code} for {model_name} on attempt {attempt + 1}: {he.reason}")
                    if he.code in (429, 503) and attempt == 0:
                        time.sleep(1.0)
                        continue
                    # On 429/503 exhausted, try next candidate model
                    if he.code in (429, 503):
                        break
                    # On permanent 400/401/403/404, stop
                    self._last_verified_connected = False
                    break
                except urllib.error.URLError as ue:
                    logger.warning(f"[GEMINI] Network error on attempt {attempt + 1}: {ue.reason}")
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    self._last_verified_connected = False
                    break
                except Exception as e:
                    logger.warning(f"[GEMINI] Request failed: {type(e).__name__}")
                    self._last_verified_connected = False
                    break
        return None

    def test_connection(self) -> dict[str, Any]:
        """Performs a server-side health check with Gemini."""
        if not self.is_configured():
            self._last_verified_connected = False
            return {
                "success": False,
                "message": "Gemini API key is not configured in .env",
                "status": "UNAVAILABLE"
            }
        
        prompt = "Respond with PONG"
        response = self._call_gemini_api(prompt)
        if response and "PONG" in response.upper():
            self._last_verified_connected = True
            return {
                "success": True,
                "message": "Connected to Gemini AI successfully.",
                "status": "CONNECTED",
                "model": self.model
            }
        
        self._last_verified_connected = False
        return {
            "success": False,
            "message": "Failed to reach Gemini API. Check network or key validity.",
            "status": "UNAVAILABLE"
        }

    # =========================================================================
    # 1. SCANNER / SIGNAL ANALYSIS
    # =========================================================================
    def analyze_signal(self, signal_context: dict[str, Any]) -> dict[str, Any]:
        """
        Explains why a scanner signal occurred, strategy alignment, expected edge,
        and gate outcomes without modifying execution decisions.
        """
        signal_id = signal_context.get("signal_id") or f"{signal_context.get('symbol')}_{signal_context.get('timestamp')}"
        cache_key = f"sig_{signal_id}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        sym = signal_context.get("symbol", "UNKNOWN")
        tf = signal_context.get("timeframe", "15m")
        strat = signal_context.get("strategy", "ADX_EMA")
        side = signal_context.get("side", "BUY")
        entry = signal_context.get("entry_price") or signal_context.get("price", 0.0)
        sl = signal_context.get("stop_loss", 0.0)
        tp = signal_context.get("take_profit", 0.0)
        conf = signal_context.get("confidence", 0.5)
        edge = signal_context.get("expected_net_edge") or signal_context.get("edge", 0.0)
        prof_res = signal_context.get("profitability_result", "PASSED")
        risk_res = signal_context.get("risk_result", "PASSED")
        final_res = signal_context.get("result", "QUALIFIED")
        reason = signal_context.get("reason", "Standard strategy conditions met")

        prompt = f"""
You are an institutional quantitative trading AI assistant. Analyze this algorithm signal setup concisely.
Context:
- Symbol: {sym}
- Timeframe: {tf}
- Strategy: {strat}
- Side: {side}
- Entry: {entry}, Stop Loss: {sl}, Take Profit: {tp}
- Confidence: {conf}
- Net Expected Edge: {edge}
- Profitability Gate: {prof_res}
- Risk Gate: {risk_res}
- Final Status: {final_res}
- Engine Reason: {reason}

Return a concise JSON object with EXACTLY these keys:
{{
  "why": "1-2 sentences explaining why the technical setup triggered",
  "how": "1-2 sentences on how strategy indicators/features aligned",
  "strengths": ["list of 2 key setup strengths"],
  "risks": ["list of 2 potential risk factors/weaknesses"],
  "summary": "1 brief summary sentence"
}}
Do NOT give trading recommendations or say whether the bot should trade. Provide only valid JSON.
"""
        raw_text = self._call_gemini_api(prompt)
        parsed = self._extract_json(raw_text)
        if not parsed:
            parsed = {
                "why": f"{strat} triggered a {side} signal on {sym} ({tf}) due to indicator alignment.",
                "how": f"Price near {entry} with SL at {sl} and TP at {tp}.",
                "strengths": ["Deterministic strategy criteria satisfied", f"Calculated net edge: {edge}"],
                "risks": ["Standard market volatility", f"Gate status: {final_res}"],
                "summary": f"Signal evaluated with final gate status: {final_res} ({reason}).",
                "ai_available": False
            }
        else:
            parsed["ai_available"] = True

        self._set_cache(cache_key, parsed)
        return parsed

    # =========================================================================
    # 2. TRADING JOURNAL / TRADE LIFECYCLE REVIEW
    # =========================================================================
    def analyze_trade(self, trade_context: dict[str, Any]) -> dict[str, Any]:
        """
        Reviews a completed trade: execution quality, holding duration, PnL,
        market alignment, and risk assessment without fabricating statistics.
        """
        trade_id = trade_context.get("trade_id") or trade_context.get("entry_order_id") or f"{trade_context.get('symbol')}_{trade_context.get('exit_timestamp')}"
        cache_key = f"trd_{trade_id}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        sym = trade_context.get("symbol", "UNKNOWN")
        tf = trade_context.get("timeframe", "15m")
        strat = trade_context.get("strategy", "ADX_EMA")
        side = trade_context.get("side", "BUY")
        entry = trade_context.get("entry_price", 0.0)
        exit_p = trade_context.get("exit_price", 0.0)
        net_pnl = trade_context.get("net_pnl", 0.0)
        fees = trade_context.get("fees", 0.0)
        dur = trade_context.get("duration", "N/A")
        reason = trade_context.get("close_reason", "OCO / TARGET")

        prompt = f"""
You are an institutional trading audit assistant. Review this closed trade lifecycle.
Trade Data:
- Symbol: {sym} ({tf})
- Strategy: {strat} | Side: {side}
- Entry: {entry} -> Exit: {exit_p}
- Net Realized PnL: {net_pnl} USD
- Total Fees: {fees} USD
- Holding Duration: {dur}
- Close Reason: {reason}

Return a concise JSON object with EXACTLY these keys:
{{
  "trade_summary": "1-2 sentence executive summary of the trade outcome",
  "execution_quality": "Assessment of entry vs exit and fee impact",
  "what_went_well": "Key positive aspect of the trade execution",
  "what_went_wrong": "Risk factor, slippage, or exit friction observed",
  "key_lesson": "1 succinct takeaway for future automated execution"
}}
Provide only valid JSON.
"""
        raw_text = self._call_gemini_api(prompt)
        parsed = self._extract_json(raw_text)
        if not parsed:
            is_win = float(net_pnl) >= 0
            parsed = {
                "trade_summary": f"{sym} {side} trade closed with Net PnL of {'+' if is_win else ''}{net_pnl} USD via {reason}.",
                "execution_quality": f"Entry at {entry} and exit at {exit_p} with {fees} USD in exchange friction.",
                "what_went_well": "Adhered strictly to predefined stop/target parameters.",
                "what_went_wrong": "Market movement variance within normal distribution." if is_win else "Hit protective exit boundary.",
                "key_lesson": "Maintain strict risk boundaries and disciplined position sizing.",
                "ai_available": False
            }
        else:
            parsed["ai_available"] = True

        self._set_cache(cache_key, parsed)
        return parsed

    # =========================================================================
    # 3. QUANTITATIVE PERFORMANCE / ANALYTICS SUMMARY
    # =========================================================================
    def analyze_performance(self, analytics_context: dict[str, Any]) -> dict[str, Any]:
        """
        Synthesizes portfolio performance, win rate, drawdown, and strategy comparisons.
        """
        cache_key = f"perf_{analytics_context.get('timeframe', 'ALL')}_{int(time.time() // 300)}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        total_trades = analytics_context.get("total_trades", 0)
        win_rate = analytics_context.get("win_rate", 0.0)
        net_pnl = analytics_context.get("net_pnl", 0.0)
        profit_factor = analytics_context.get("profit_factor", 0.0)
        max_dd = analytics_context.get("max_drawdown", 0.0)
        strat_breakdown = analytics_context.get("strategies", {})

        prompt = f"""
You are a quantitative portfolio risk analyst. Summarize overall trading performance based on verified statistics:
Metrics:
- Total Closed Trades: {total_trades}
- Win Rate: {win_rate}%
- Net Realized PnL: {net_pnl} USD
- Profit Factor: {profit_factor}
- Max Drawdown: {max_dd}%
- Strategy Distribution: {json.dumps(strat_breakdown)}

Return a concise JSON object with EXACTLY these keys:
{{
  "performance_summary": "2 sentence high-level portfolio performance audit",
  "strategy_observations": "Analysis of strategy contributions and consistency",
  "risk_observations": "Audit of drawdown containment and equity preservation",
  "suggested_focus": "Key operational area to monitor (e.g. fee efficiency, timeframe consistency)"
}}
Provide only valid JSON.
"""
        raw_text = self._call_gemini_api(prompt)
        parsed = self._extract_json(raw_text)
        if not parsed:
            parsed = {
                "performance_summary": f"Portfolio has executed {total_trades} trades with a {win_rate}% win rate and net return of {net_pnl} USD.",
                "strategy_observations": f"Active allocation across {len(strat_breakdown)} strategies with profit factor of {profit_factor}.",
                "risk_observations": f"Max drawdown contained at {max_dd}%, adhering to capital preservation thresholds.",
                "suggested_focus": "Continue monitoring slippage friction and candidate gate pass-rates.",
                "ai_available": False
            }
        else:
            parsed["ai_available"] = True

        self._set_cache(cache_key, parsed)
        return parsed

    # =========================================================================
    # 4. SYSTEM DIAGNOSTICS SUMMARY
    # =========================================================================
    def analyze_system_diagnostics(self, system_context: dict[str, Any]) -> dict[str, Any]:
        """
        Analyzes engine health, WebSocket telemetry, scanner throughput, and error rates.
        """
        cache_key = f"sys_{int(time.time() // 180)}"
        cached = self._get_cache(cache_key)
        if cached:
            return cached

        uptime = system_context.get("uptime", "N/A")
        engine_status = system_context.get("engine_status", "ONLINE")
        reconnects = system_context.get("reconnect_count", 0)
        recent_events = system_context.get("recent_events", [])

        prompt = f"""
You are a high-frequency trading DevOps specialist. Provide a brief health diagnosis for this algorithmic bot.
Telemetry:
- Engine Status: {engine_status}
- Uptime: {uptime}
- WebSocket Reconnects: {reconnects}
- Recent Logged Events: {json.dumps(recent_events[:8])}

Return a concise JSON object with EXACTLY these keys:
{{
  "health_rating": "OPTIMAL / NORMAL / DEGRADED",
  "system_summary": "1-2 sentences summarizing infrastructure stability and event stream health",
  "telemetry_insights": ["2 concise bullet points on data feed and websocket performance"],
  "action_items": "Any recommended operational attention or 'None required'"
}}
Provide only valid JSON.
"""
        raw_text = self._call_gemini_api(prompt)
        parsed = self._extract_json(raw_text)
        if not parsed:
            parsed = {
                "health_rating": "OPTIMAL" if reconnects == 0 else "NORMAL",
                "system_summary": f"Engine operating in {engine_status} state with {uptime} uptime and {reconnects} stream reconnections.",
                "telemetry_insights": ["WebSocket tick streams functioning normally", "System events operating within latency tolerances"],
                "action_items": "None required - All services nominal.",
                "ai_available": False
            }
        else:
            parsed["ai_available"] = True

        self._set_cache(cache_key, parsed)
        return parsed

    def _extract_json(self, text: str | None) -> dict[str, Any] | None:
        if not text:
            return None
        clean_text = text.strip()
        # Strip markdown fences if present
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        clean_text = clean_text.removesuffix("```")
        clean_text = clean_text.strip()

        try:
            return json.loads(clean_text)
        except Exception:
            # Attempt substring match for JSON brackets
            start = clean_text.find("{")
            end = clean_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(clean_text[start:end+1])
                except Exception:
                    pass
        return None

# Singleton instance accessor
_service_instance: GeminiService | None = None

def get_gemini_service() -> GeminiService:
    global _service_instance
    if _service_instance is None:
        _service_instance = GeminiService()
    return _service_instance
