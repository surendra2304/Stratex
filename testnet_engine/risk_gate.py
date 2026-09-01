import datetime
import math

import config
from logger import get_logger

logger = get_logger("risk_gate")

class RiskGate:
    def __init__(self, starting_balance=10000.0):
        self.starting_balance = starting_balance
        self.consecutive_losses = 0
        self.max_consecutive_losses = 3
        
        # State tracking for limits
        self.daily_realized_loss = 0.0
        self.peak_equity = starting_balance
        self.current_trading_day = datetime.datetime.now(datetime.timezone.utc).date()

    def _check_daily_boundary(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if today != self.current_trading_day:
            logger.info(f"[RISKGATE] 🌅 Crossing UTC Daily Boundary ({self.current_trading_day} -> {today}). Resetting daily PnL.")
            self.daily_realized_loss = 0.0
            self.current_trading_day = today

    def evaluate_risk(self, symbol, side, current_equity, active_positions, proposed_qty, entry_price, data_health_status):
        """
        Evaluates systemic and local risk before executing a signal.
        Returns (is_allowed, reason, details)
        """
        self._check_daily_boundary()
        
        # 0. Capital / Equity Guard
        try:
            c_eq = float(current_equity)
            if c_eq <= 0 or math.isnan(c_eq) or math.isinf(c_eq):
                logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: INSUFFICIENT_EQUITY | Equity: {current_equity}")
                return False, "INSUFFICIENT_EQUITY", f"Current equity (${c_eq:.2f}) is zero or negative."
        except (ValueError, TypeError):
            return False, "INSUFFICIENT_EQUITY", "Invalid equity value passed to risk evaluation."

        # 0b. Numerical input sanity validation
        try:
            p_qty = float(proposed_qty)
            e_price = float(entry_price)
            if p_qty <= 0 or e_price <= 0 or math.isnan(p_qty) or math.isnan(e_price) or math.isinf(p_qty) or math.isinf(e_price):
                logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: INVALID_INPUT | Qty: {proposed_qty}, Price: {entry_price}")
                return False, "INVALID_INPUT", "Price or quantity is non-positive or NaN/Inf."
        except (ValueError, TypeError):
            return False, "INVALID_INPUT", "Invalid numeric value passed to risk evaluation."

        # 1. Data Health Check
        if data_health_status != "OK":
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: DATA_DEGRADED | Status: {data_health_status}")
            return False, "DATA_DEGRADED", f"Data health is {data_health_status}"

        # 2. Consecutive Losses
        is_aggressive = getattr(config, "BYPASS_PROFITABILITY_GATE", False) or getattr(config, "UNLIMITED_POSITIONS", False) or (getattr(config, "MAX_OPEN_POSITIONS", 5) >= 999)
        if not is_aggressive and self.consecutive_losses >= self.max_consecutive_losses:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: CONSECUTIVE_LOSS_LIMIT | Losses: {self.consecutive_losses}")
            return False, "CONSECUTIVE_LOSS_LIMIT", f"Hit {self.max_consecutive_losses} consecutive losses."

        # 3. Open Positions Limit
        max_pos = 999 if is_aggressive else int(getattr(config, "MAX_OPEN_POSITIONS", 50))
        if len(active_positions) >= max_pos:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_OPEN_POSITIONS | Open: {len(active_positions)}")
            return False, "MAX_OPEN_POSITIONS", f"Currently at limit of {max_pos} open positions."

        # Compute exposures
        current_exposure = 0.0
        for p in active_positions.values():
            if isinstance(p, dict):
                try:
                    q = float(p.get('quantity', 0.0))
                    ep = float(p.get('entry_price', 0.0))
                    current_exposure += (q * ep)
                except (ValueError, TypeError, Exception):
                    continue
                    
        new_trade_value = proposed_qty * entry_price
        total_exposure = current_exposure + new_trade_value
        total_exposure_pct = total_exposure / current_equity
        
        # 4. Total Exposure Limit
        max_exp = 999.0 if is_aggressive else config.MAX_TESTNET_EXPOSURE
        if total_exposure_pct > max_exp:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_EXPOSURE_REACHED | Total: {total_exposure_pct:.2%} > {max_exp:.2%}")
            return False, "MAX_EXPOSURE_REACHED", f"New exposure {total_exposure_pct:.2%} exceeds {max_exp:.2%}"
            
        # 5. Single Asset Exposure
        existing_asset_exposure = 0.0
        for pos_k, p in active_positions.items():
            if isinstance(p, dict) and (pos_k == symbol or p.get('symbol') == symbol):
                try:
                    q = float(p.get('quantity', 0.0))
                    ep = float(p.get('entry_price', 0.0))
                    existing_asset_exposure += (q * ep)
                except (ValueError, TypeError, Exception):
                    continue
                    
        single_asset_exposure = existing_asset_exposure + new_trade_value
        single_asset_pct = single_asset_exposure / current_equity
        max_asset_exp = 999.0 if is_aggressive else config.MAX_SINGLE_ASSET_EXPOSURE
        if single_asset_pct > max_asset_exp:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_SINGLE_ASSET_EXPOSURE | Asset: {single_asset_pct:.2%} > {max_asset_exp:.2%}")
            return False, "MAX_SINGLE_ASSET_EXPOSURE", f"Asset exposure {single_asset_pct:.2%} exceeds {max_asset_exp:.2%}"

        # 6. Directional Correlation Limit (Net Directional Exposure)
        net_exposure = 0.0
        for p in active_positions.values():
            if not isinstance(p, dict):
                continue
            try:
                qty = float(p.get('quantity', 0))
                ep = float(p.get('entry_price', 0))
                val = qty * ep
                p_side = str(p.get('side', '')).upper()
                if p_side in ("LONG", "BUY"):
                    net_exposure += val
                elif p_side in ("SHORT", "SELL"):
                    net_exposure -= val
            except (ValueError, TypeError, Exception):
                continue
                
        req_side = str(side).upper()
        if req_side in ("LONG", "BUY"):
            net_exposure += new_trade_value
        else:
            net_exposure -= new_trade_value
            
        net_directional_pct = abs(net_exposure) / current_equity
        max_dir_exp = 999.0 if is_aggressive else config.MAX_NET_DIRECTIONAL_EXPOSURE
        if net_directional_pct > max_dir_exp:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_CORRELATION_EXPOSURE | NetDir: {net_directional_pct:.2%} > {max_dir_exp:.2%}")
            return False, "MAX_CORRELATION_EXPOSURE", f"Net directional {net_directional_pct:.2%} exceeds {max_dir_exp:.2%}"

        # 7. Drawdown Limit
        # Update peak equity if current equity establishes a new watermark
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        max_dd = 999.0 if is_aggressive else config.MAX_TESTNET_DRAWDOWN_PCT
        if drawdown_pct >= max_dd:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_DRAWDOWN_BREACH | DD: {drawdown_pct:.2%} >= {max_dd:.2%}")
            return False, "MAX_DRAWDOWN_BREACH", f"Current drawdown {drawdown_pct:.2%} >= {max_dd:.2%}"

        daily_loss_pct = abs(self.daily_realized_loss) / current_equity if self.daily_realized_loss < 0 else 0
        max_daily_loss = 999.0 if is_aggressive else config.MAX_DAILY_LOSS_PCT
        if daily_loss_pct >= max_daily_loss:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: DAILY_LOSS_LIMIT | Loss: {daily_loss_pct:.2%} >= {max_daily_loss:.2%}")
            return False, "DAILY_LOSS_LIMIT", f"Daily loss {daily_loss_pct:.2%} >= {max_daily_loss:.2%}"

        logger.info(f"[RISK_ACCEPTED] {symbol} {side} | Proposed Qty: {proposed_qty} | Value: ${new_trade_value:.2f} | Total Exposure: {total_exposure_pct:.2%}")
        return True, "RISK_OK", ""

    def update_after_trade(self, net_pnl, current_equity):
        """Update risk limits based on latest trade results."""
        self._check_daily_boundary()
        
        # Accumulate net realized P&L (used to determine self.daily_realized_loss)
        self.daily_realized_loss += net_pnl
        
        if net_pnl < 0:
            self.consecutive_losses += 1
        elif net_pnl > 0:
            self.consecutive_losses = 0
            
        self.peak_equity = max(self.peak_equity, current_equity)

    def calculate_position_size(self, current_equity, entry_price, sl_price, filters=None, confidence=None, tp_price=None):
        """
        Calculates position size strictly capped at MAX_TESTNET_RISK_PER_TRADE,
        with optional Half-Kelly dynamic scaling when calibrated confidence and targets are provided.
        Floors the value strictly to Binance's LOT_SIZE stepSize.
        Returns 0.0 if the rounded size is below MIN_NOTIONAL.
        """
        if filters is None:
            filters = {"stepSize": 0.00001, "minNotional": 10.0}
        risk_per_unit = abs(entry_price - sl_price)
        if risk_per_unit == 0:
            return 0.0

        # Base risk fraction
        risk_pct = config.MAX_TESTNET_RISK_PER_TRADE
        
        # Adaptive Half-Kelly sizing when confidence & TP are available
        if confidence is not None and tp_price is not None and 0.5 < confidence < 1.0:
            reward_per_unit = abs(tp_price - entry_price)
            if risk_per_unit > 0 and reward_per_unit > 0:
                b = reward_per_unit / risk_per_unit  # reward-to-risk ratio
                p = float(confidence)
                q = 1.0 - p
                kelly_f = (p * b - q) / b if b > 0 else 0.0
                if kelly_f > 0:
                    half_kelly = kelly_f * 0.5
                    # Bound between 0.002 (0.2%) and MAX_TESTNET_RISK_PER_TRADE
                    risk_pct = max(0.002, min(config.MAX_TESTNET_RISK_PER_TRADE, half_kelly))

        max_risk_amount = current_equity * risk_pct
        quantity = max_risk_amount / risk_per_unit
        
        # Also cap by max single asset absolute size
        is_aggressive = getattr(config, "BYPASS_PROFITABILITY_GATE", False) or getattr(config, "UNLIMITED_POSITIONS", False)
        max_single_exp = 999.0 if is_aggressive else config.MAX_SINGLE_ASSET_EXPOSURE
        max_position_value = current_equity * max_single_exp
        max_quantity_by_exposure = max_position_value / entry_price
        
        final_quantity = min(quantity, max_quantity_by_exposure)
        
        # Apply LOT_SIZE stepSize precision
        step_size = filters.get("stepSize", 1.0)
        # Floor to nearest step_size
        precision = max(0, round(-math.log10(step_size))) if step_size < 1 else 0
        stepped_quantity = math.floor(final_quantity / step_size) * step_size
        stepped_quantity = round(stepped_quantity, precision)
        
        # Apply MIN_NOTIONAL filter check
        min_notional = filters.get("minNotional", 10.0)
        notional_value = stepped_quantity * entry_price
        
        if notional_value < min_notional:
            logger.info(f"[RISKGATE] Rejecting trade: Notional value ${notional_value:.2f} < MIN_NOTIONAL ${min_notional:.1f}")
            return 0.0
            
        return stepped_quantity
