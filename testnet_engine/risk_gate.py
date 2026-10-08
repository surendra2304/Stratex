"""testnet_engine/risk_gate.py — pre-trade risk gate and position sizing.

Every numeric input is validated with :mod:`numeric_safety` and every invalid
input fails CLOSED. The previous implementation compared raw floats, so:

* a position record with a ``NaN`` quantity made total exposure ``NaN`` and
  ``NaN > limit`` is False — the exposure, single-asset and net-directional
  limits all silently passed;
* a non-numeric position field was *skipped* ("ignoring invalid record"),
  undercounting exposure;
* ``update_after_trade(NaN, ...)`` poisoned ``daily_realized_loss`` so the daily
  loss limit could never trigger again that day, and a NaN loss did not count
  toward the consecutive-loss limit;
* ``calculate_position_size`` crashed in ``math.floor(nan)`` for a NaN entry or
  stop, returned a *negative* quantity for negative prices, divided by zero for
  a zero step size, and sized a long whose stop sat above the entry.
"""

from __future__ import annotations

import datetime
import os
from typing import Any

import config
from logger import get_logger
from numeric_safety import finite_float, floor_to_step, positive_float, step_precision

logger = get_logger("risk_gate")

_LONG_SIDES = ("LONG", "BUY")
_SHORT_SIDES = ("SHORT", "SELL")


def _config_fraction(name: str, default: float) -> float:
    """A risk limit from config; invalid values fall back to the safe default."""
    value = finite_float(getattr(config, name, default))
    if value is None or value < 0:
        logger.error(f"[RISKGATE] Invalid config {name}={getattr(config, name, None)!r}; using {default}")
        return default
    return value


def _int_setting(env_name: str, config_name: str, default: int) -> int:
    raw = os.getenv(env_name, getattr(config, config_name, default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.error(f"[RISKGATE] Invalid {env_name}={raw!r}; using {default}")
        return default
    return value if value >= 0 else default


class RiskGate:
    def __init__(self, starting_balance=10000.0):
        balance = positive_float(starting_balance)
        if balance is None:
            raise ValueError(f"starting_balance must be a finite number > 0, got {starting_balance!r}")
        self.starting_balance = balance
        self.consecutive_losses = 0
        self.max_consecutive_losses = _int_setting("MAX_CONSECUTIVE_LOSSES", "MAX_CONSECUTIVE_LOSSES", 3)

        # State tracking for limits
        self.daily_realized_loss = 0.0
        self.peak_equity = balance
        self.current_trading_day = datetime.datetime.now(datetime.timezone.utc).date()
        # Set when a trade result could not be accounted (non-finite PnL).
        # While set, new entries are refused: the loss limits are unknowable.
        self.accounting_fault: str | None = None

    def _check_daily_boundary(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if today != self.current_trading_day:
            logger.info(f"[RISKGATE] 🌅 Crossing UTC Daily Boundary ({self.current_trading_day} -> {today}). Resetting daily PnL.")
            self.daily_realized_loss = 0.0
            self.current_trading_day = today

    @staticmethod
    def _position_exposure(position: Any) -> tuple[float, str] | None:
        """(notional, side) of an active position, or ``None`` if unreadable.

        Positions whose status is explicitly not OPEN carry no exposure.
        """
        if not isinstance(position, dict):
            return None
        status = position.get("status")
        if status is not None and str(status).upper() != "OPEN":
            return 0.0, ""
        qty = positive_float(position.get("quantity", 0.0))
        price = positive_float(position.get("entry_price", 0.0))
        if qty is None or price is None:
            return None
        return qty * price, str(position.get("side", "")).upper()

    def evaluate_risk(self, symbol, side, current_equity, active_positions, proposed_qty, entry_price, data_health_status):
        """
        Evaluates systemic and local risk before executing a signal.
        Returns (is_allowed, reason, details)
        """
        self._check_daily_boundary()

        # 0. Capital / Equity Guard
        c_eq = positive_float(current_equity)
        if c_eq is None:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: INSUFFICIENT_EQUITY | Equity: {current_equity}")
            return False, "INSUFFICIENT_EQUITY", f"Current equity ({current_equity!r}) is not a finite positive number."

        # 0b. Numerical input sanity validation
        p_qty = positive_float(proposed_qty)
        e_price = positive_float(entry_price)
        if p_qty is None or e_price is None:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: INVALID_INPUT | Qty: {proposed_qty}, Price: {entry_price}")
            return False, "INVALID_INPUT", "Price or quantity is non-positive or NaN/Inf."

        req_side = str(side).upper()
        if req_side not in _LONG_SIDES + _SHORT_SIDES:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: INVALID_INPUT | Unknown side")
            return False, "INVALID_INPUT", f"Unknown order side {side!r}."

        if self.accounting_fault:
            return False, "ACCOUNTING_FAULT", self.accounting_fault

        # 1. Data Health Check
        if data_health_status != "OK":
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: DATA_DEGRADED | Status: {data_health_status}")
            return False, "DATA_DEGRADED", f"Data health is {data_health_status}"

        # 2. Consecutive Losses
        if self.consecutive_losses >= self.max_consecutive_losses:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: CONSECUTIVE_LOSS_LIMIT | Losses: {self.consecutive_losses}")
            return False, "CONSECUTIVE_LOSS_LIMIT", f"Hit {self.max_consecutive_losses} consecutive losses."

        if not isinstance(active_positions, dict):
            return False, "INVALID_POSITION_STATE", "Active positions state is not a mapping."

        # 3. Open Positions Limit (records of unknown shape count as open)
        max_pos = _int_setting("MAX_OPEN_POSITIONS", "MAX_OPEN_POSITIONS", 5)
        open_count = sum(
            1 for p in active_positions.values()
            if not isinstance(p, dict) or p.get("status") is None or str(p.get("status")).upper() == "OPEN"
        )
        if open_count >= max_pos:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_OPEN_POSITIONS | Open: {open_count}")
            return False, "MAX_OPEN_POSITIONS", f"Currently at limit of {max_pos} open positions."

        # Compute exposures; an unreadable record makes exposure unknowable,
        # so the gate refuses rather than undercounting.
        current_exposure = 0.0
        existing_asset_exposure = 0.0
        net_exposure = 0.0
        for pos_key, position in active_positions.items():
            measured = self._position_exposure(position)
            if measured is None:
                logger.warning(f"[RISK_REJECTED] {symbol} {side} | Reason: INVALID_POSITION_STATE | Record: {pos_key}")
                return False, "INVALID_POSITION_STATE", f"Active position {pos_key!r} has no valid quantity/entry price."
            notional, p_side = measured
            if notional == 0.0 and p_side == "":
                continue  # explicitly non-OPEN record
            current_exposure += notional
            if pos_key == symbol or position.get("symbol") == symbol:
                existing_asset_exposure += notional
            if p_side in _LONG_SIDES:
                net_exposure += notional
            elif p_side in _SHORT_SIDES:
                net_exposure -= notional
            else:
                return False, "INVALID_POSITION_STATE", f"Active position {pos_key!r} has unknown side {p_side!r}."

        new_trade_value = p_qty * e_price
        total_exposure_pct = (current_exposure + new_trade_value) / c_eq

        # 4. Total Exposure Limit
        max_exp = _config_fraction("MAX_TESTNET_EXPOSURE", 0.0)
        if not total_exposure_pct <= max_exp:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_EXPOSURE_REACHED | Total: {total_exposure_pct:.2%} > {max_exp:.2%}")
            return False, "MAX_EXPOSURE_REACHED", f"New exposure {total_exposure_pct:.2%} exceeds {max_exp:.2%}"

        # 5. Single Asset Exposure
        single_asset_pct = (existing_asset_exposure + new_trade_value) / c_eq
        max_asset_exp = _config_fraction("MAX_SINGLE_ASSET_EXPOSURE", 0.0)
        if not single_asset_pct <= max_asset_exp:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_SINGLE_ASSET_EXPOSURE | Asset: {single_asset_pct:.2%} > {max_asset_exp:.2%}")
            return False, "MAX_SINGLE_ASSET_EXPOSURE", f"Asset exposure {single_asset_pct:.2%} exceeds {max_asset_exp:.2%}"

        # 6. Directional Correlation Limit (Net Directional Exposure)
        net_exposure += new_trade_value if req_side in _LONG_SIDES else -new_trade_value
        net_directional_pct = abs(net_exposure) / c_eq
        max_dir_exp = _config_fraction("MAX_NET_DIRECTIONAL_EXPOSURE", 0.0)
        if not net_directional_pct <= max_dir_exp:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_CORRELATION_EXPOSURE | NetDir: {net_directional_pct:.2%} > {max_dir_exp:.2%}")
            return False, "MAX_CORRELATION_EXPOSURE", f"Net directional {net_directional_pct:.2%} exceeds {max_dir_exp:.2%}"

        # 7. Drawdown Limit
        if c_eq > self.peak_equity:
            self.peak_equity = c_eq

        drawdown_pct = (self.peak_equity - c_eq) / self.peak_equity if self.peak_equity > 0 else 0.0
        max_dd = _config_fraction("MAX_TESTNET_DRAWDOWN_PCT", 0.0)
        if not drawdown_pct < max_dd:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: MAX_DRAWDOWN_BREACH | DD: {drawdown_pct:.2%} >= {max_dd:.2%}")
            return False, "MAX_DRAWDOWN_BREACH", f"Current drawdown {drawdown_pct:.2%} >= {max_dd:.2%}"

        daily_loss_pct = abs(self.daily_realized_loss) / c_eq if self.daily_realized_loss < 0 else 0.0
        max_daily_loss = _config_fraction("MAX_DAILY_LOSS_PCT", 0.0)
        if not daily_loss_pct < max_daily_loss:
            logger.info(f"[RISK_REJECTED] {symbol} {side} | Reason: DAILY_LOSS_LIMIT | Loss: {daily_loss_pct:.2%} >= {max_daily_loss:.2%}")
            return False, "DAILY_LOSS_LIMIT", f"Daily loss {daily_loss_pct:.2%} >= {max_daily_loss:.2%}"

        logger.info(f"[RISK_ACCEPTED] {symbol} {side} | Proposed Qty: {p_qty} | Value: ${new_trade_value:.2f} | Total Exposure: {total_exposure_pct:.2%}")
        return True, "RISK_OK", ""

    def update_after_trade(self, net_pnl, current_equity):
        """Update risk limits based on latest trade results.

        A non-finite PnL cannot be booked; it is treated as a loss for the
        consecutive-loss counter and latches ``accounting_fault`` so no new
        entry is accepted until an operator reconciles (``clear_accounting_fault``).
        """
        self._check_daily_boundary()

        pnl = finite_float(net_pnl)
        if pnl is None:
            self.consecutive_losses += 1
            self.accounting_fault = (
                f"Trade result PnL {net_pnl!r} is not a finite number; daily-loss accounting is "
                "unknown. New entries are blocked until the ledger is reconciled."
            )
            logger.critical(f"[RISKGATE] {self.accounting_fault}")
        else:
            # Accumulate net realized P&L (used to determine self.daily_realized_loss)
            self.daily_realized_loss += pnl
            if pnl < 0:
                self.consecutive_losses += 1
            elif pnl > 0:
                self.consecutive_losses = 0

        equity = finite_float(current_equity)
        if equity is not None:
            self.peak_equity = max(self.peak_equity, equity)

    def clear_accounting_fault(self, operator: str) -> None:
        """Operator acknowledgement after reconciling an unbookable trade result."""
        if not isinstance(operator, str) or not operator.strip():
            raise ValueError("A named operator must clear an accounting fault")
        logger.warning(f"[RISKGATE] Accounting fault cleared by {operator}: {self.accounting_fault}")
        self.accounting_fault = None

    def calculate_position_size(self, current_equity, entry_price, sl_price, filters=None, confidence=None, tp_price=None,
                                side=None):
        """
        Calculates position size strictly capped at MAX_TESTNET_RISK_PER_TRADE,
        with optional Half-Kelly dynamic scaling when calibrated confidence and targets are provided.
        Floors the value strictly to Binance's LOT_SIZE stepSize.
        Returns 0.0 (no trade) if any input is invalid, the stop sits on the
        wrong side of the entry for ``side``, or the rounded size is below
        MIN_NOTIONAL.
        """
        if filters is None:
            filters = {"stepSize": 0.00001, "minNotional": 10.0}
        equity = positive_float(current_equity)
        entry = positive_float(entry_price)
        stop = positive_float(sl_price)
        if equity is None or entry is None or stop is None:
            logger.info(
                f"[RISKGATE] Rejecting sizing: invalid inputs equity={current_equity!r} "
                f"entry={entry_price!r} stop={sl_price!r}"
            )
            return 0.0
        if side is not None:
            s = str(side).upper()
            if (s in _LONG_SIDES and not stop < entry) or (s in _SHORT_SIDES and not stop > entry):
                logger.info(f"[RISKGATE] Rejecting sizing: stop {stop} on wrong side of entry {entry} for {s}")
                return 0.0
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            return 0.0

        # Base risk fraction
        max_risk_pct = _config_fraction("MAX_TESTNET_RISK_PER_TRADE", 0.0)
        risk_pct = max_risk_pct

        # Adaptive Half-Kelly sizing when confidence & TP are available
        p = finite_float(confidence)
        target = positive_float(tp_price)
        if p is not None and target is not None and 0.5 < p < 1.0:
            reward_per_unit = abs(target - entry)
            if reward_per_unit > 0:
                b = reward_per_unit / risk_per_unit  # reward-to-risk ratio
                kelly_f = (p * b - (1.0 - p)) / b
                if kelly_f > 0:
                    # Bound between 0.002 (0.2%) and MAX_TESTNET_RISK_PER_TRADE
                    risk_pct = max(min(0.002, max_risk_pct), min(max_risk_pct, kelly_f * 0.5))

        quantity = (equity * risk_pct) / risk_per_unit

        # Strictly cap by max single asset exposure limit
        max_quantity_by_exposure = (equity * _config_fraction("MAX_SINGLE_ASSET_EXPOSURE", 0.0)) / entry
        final_quantity = min(quantity, max_quantity_by_exposure)

        # Apply LOT_SIZE stepSize precision
        step_size = positive_float(filters.get("stepSize", 1.0))
        if step_size is None:
            logger.error(f"[RISKGATE] Rejecting sizing: invalid LOT_SIZE stepSize {filters.get('stepSize')!r}")
            return 0.0
        stepped_quantity = round(floor_to_step(final_quantity, step_size), step_precision(step_size))

        # Apply MIN_NOTIONAL filter check
        min_notional = finite_float(filters.get("minNotional", 10.0))
        if min_notional is None or min_notional < 0:
            logger.error(f"[RISKGATE] Rejecting sizing: invalid minNotional {filters.get('minNotional')!r}")
            return 0.0
        notional_value = stepped_quantity * entry

        if stepped_quantity <= 0 or notional_value < min_notional:
            logger.info(f"[RISKGATE] Rejecting trade: Notional value ${notional_value:.2f} < MIN_NOTIONAL ${min_notional:.1f}")
            return 0.0

        return stepped_quantity
