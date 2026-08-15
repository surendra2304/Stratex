from logger import get_logger
import datetime
import config
logger = get_logger("risk_gate")

class RiskGate:
    def __init__(self, starting_balance=10000.0):
        self.starting_balance = starting_balance
        self.consecutive_losses = 0
        self.max_consecutive_losses = 3
        
        # State tracking for limits
        self.daily_realized_loss = 0.0
        self.peak_equity = starting_balance
        self.current_trading_day = datetime.datetime.utcnow().date()

    def _check_daily_boundary(self):
        today = datetime.datetime.utcnow().date()
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
        
        # 1. Data Health Check
        if data_health_status != "OK":
            return False, "DATA_DEGRADED", f"Data health is {data_health_status}"

        # 2. Consecutive Losses
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, "CONSECUTIVE_LOSS_LIMIT", f"Hit {self.max_consecutive_losses} consecutive losses."

        # 3. Open Positions Limit
        if len(active_positions) >= config.MAX_OPEN_POSITIONS:
            return False, "MAX_OPEN_POSITIONS", f"Currently at limit of {config.MAX_OPEN_POSITIONS} open positions."

        # Compute exposures
        current_exposure = sum([p.get('quantity', 0) * p.get('entry_price', 0) for p in active_positions.values()])
        new_trade_value = proposed_qty * entry_price
        total_exposure = current_exposure + new_trade_value
        total_exposure_pct = total_exposure / current_equity
        
        # 4. Total Exposure Limit
        if total_exposure_pct > config.MAX_TESTNET_EXPOSURE:
            return False, "MAX_EXPOSURE_REACHED", f"New exposure {total_exposure_pct:.2%} exceeds {config.MAX_TESTNET_EXPOSURE:.2%}"
            
        # 5. Single Asset Exposure
        # Existing plus new if same symbol, but OCO normally prevents duplicate symbols. Checked anyway for safety.
        existing_symbol_value = active_positions.get(symbol, {}).get('quantity', 0) * active_positions.get(symbol, {}).get('entry_price', 0)
        single_asset_exposure_pct = (existing_symbol_value + new_trade_value) / current_equity
        if single_asset_exposure_pct > config.MAX_SINGLE_ASSET_EXPOSURE:
            return False, "MAX_SINGLE_ASSET_EXPOSURE", f"Asset exposure {single_asset_exposure_pct:.2%} exceeds {config.MAX_SINGLE_ASSET_EXPOSURE:.2%}"

        # 6. Correlated / Net Directional Exposure
        net_exposure = 0.0
        for p in active_positions.values():
            val = p.get('quantity', 0) * p.get('entry_price', 0)
            if p.get('side') == "LONG":
                net_exposure += val
            elif p.get('side') == "SHORT":
                net_exposure -= val
                
        if side == "LONG":
            net_exposure += new_trade_value
        else:
            net_exposure -= new_trade_value
            
        net_directional_pct = abs(net_exposure) / current_equity
        if net_directional_pct > config.MAX_NET_DIRECTIONAL_EXPOSURE:
            return False, "MAX_CORRELATION_EXPOSURE", f"Net directional {net_directional_pct:.2%} exceeds {config.MAX_NET_DIRECTIONAL_EXPOSURE:.2%}"

        # 7. Drawdown Limit
        drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity
        if drawdown_pct >= config.MAX_TESTNET_DRAWDOWN_PCT:
            return False, "MAX_DRAWDOWN_BREACH", f"Current drawdown {drawdown_pct:.2%} >= {config.MAX_TESTNET_DRAWDOWN_PCT:.2%}"

        # 8. Daily Loss Limit
        daily_loss_pct = abs(self.daily_realized_loss) / current_equity if self.daily_realized_loss < 0 else 0
        if daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
            return False, "DAILY_LOSS_LIMIT", f"Daily loss {daily_loss_pct:.2%} >= {config.MAX_DAILY_LOSS_PCT:.2%}"

        return True, "RISK_OK", ""

    def update_after_trade(self, net_pnl, current_equity):
        """Update risk limits based on latest trade results."""
        self._check_daily_boundary()
        
        if net_pnl < 0:
            self.consecutive_losses += 1
            self.daily_realized_loss += net_pnl
        elif net_pnl > 0:
            self.consecutive_losses = 0
            self.daily_realized_loss += net_pnl
            
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

    def calculate_position_size(self, current_equity, entry_price, sl_price, filters=None):
        """
        Calculates position size strictly capped at MAX_TESTNET_RISK_PER_TRADE,
        and floors the value strictly to Binance's LOT_SIZE stepSize.
        Returns 0.0 if the rounded size is below MIN_NOTIONAL.
        """
        if filters is None:
            filters = {"stepSize": 0.00001, "minNotional": 10.0}
        risk_per_unit = abs(entry_price - sl_price)
        if risk_per_unit == 0:
            return 0.0

        max_risk_amount = current_equity * config.MAX_TESTNET_RISK_PER_TRADE
        quantity = max_risk_amount / risk_per_unit
        
        # Also cap by max single asset absolute size
        max_position_value = current_equity * config.MAX_SINGLE_ASSET_EXPOSURE
        max_quantity_by_exposure = max_position_value / entry_price
        
        final_quantity = min(quantity, max_quantity_by_exposure)
        
        # Apply LOT_SIZE stepSize precision
        step_size = filters.get("stepSize", 1.0)
        import math
        # Floor to nearest step_size
        rounded_qty = math.floor(final_quantity / step_size) * step_size
        
        # Handle floating point inaccuracies
        # We find how many decimals are in the step size and round to that.
        step_str = f"{step_size:f}".rstrip('0')
        decimals = len(step_str.split('.')[1]) if '.' in step_str else 0
        rounded_qty = round(rounded_qty, decimals)
        
        # Apply MIN_NOTIONAL check
        min_notional = filters.get("minNotional", 10.0)
        if rounded_qty * entry_price < min_notional:
            logger.info(f"[RISKGATE] Rejecting trade: Notional value ${rounded_qty * entry_price:.2f} < MIN_NOTIONAL ${min_notional}")
            return 0.0
            
        return rounded_qty
