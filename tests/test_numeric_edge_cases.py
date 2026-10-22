"""Item 7 — execution/risk numeric edge cases.

Every test here drives the real sizing / protection / risk-gate code with
adversarial numbers (NaN, ±inf, zero, negative, huge, strings, booleans) and
pins the fail-closed behaviour. Each case below reproduced a defect before the
fix (NaN comparisons are False, so most guards silently passed).
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

import numeric_safety as ns

NAN = float("nan")
INF = float("inf")
BAD_NUMBERS = [NAN, INF, -INF, None, "abc", True, [], {}]


# ── numeric_safety ───────────────────────────────────────────────────────────

class TestNumericSafety:
    @pytest.mark.parametrize("value, expected", [(1, 1.0), ("2.5", 2.5), (np.float64(3.0), 3.0), (Decimal("4"), 4.0)])
    def test_finite_float_accepts(self, value, expected):
        assert ns.finite_float(value) == expected

    @pytest.mark.parametrize("value", BAD_NUMBERS + ["nan", "inf", "1e999", Decimal("NaN")])
    def test_finite_float_rejects(self, value):
        assert ns.finite_float(value) is None

    def test_positive_and_non_negative(self):
        assert ns.positive_float(0) is None
        assert ns.positive_float(-1) is None
        assert ns.positive_float(1e-12) == 1e-12
        assert ns.non_negative_float(0) == 0.0
        assert ns.non_negative_float(-0.1) is None

    def test_require_helpers(self):
        with pytest.raises(ns.InvalidNumericInput) as err:
            ns.require_positive("qty", NAN)
        assert err.value.name == "qty"
        assert ns.require_non_negative("fee", 0) == 0.0
        assert ns.require_finite("x", "-3") == -3.0
        assert ns.require_fraction("p", 1) == 1.0
        with pytest.raises(ns.InvalidNumericInput):
            ns.require_fraction("p", 1.01)
        with pytest.raises(ns.InvalidNumericInput):
            ns.require_fraction("p", 0, allow_zero=False)

    def test_safe_ratio_and_clamp(self):
        assert ns.safe_ratio(1, 0) is None
        assert ns.safe_ratio(1, NAN) is None
        assert ns.safe_ratio(1e308, 1e-308) is None  # overflow → inf → None
        assert ns.safe_ratio(3, 2) == 1.5
        assert ns.clamp(5, 0, 1) == 1
        with pytest.raises(ns.InvalidNumericInput):
            ns.clamp(NAN, 0, 1)

    @pytest.mark.parametrize("qty, step, expected", [
        (1.2349, 0.001, 1.234), (0.3, 0.1, 0.3), (5, 1, 5.0), (0.00001999, 0.00001, 0.00001), (-1, 0.1, 0.0), (NAN, 0.1, 0.0),
    ])
    def test_floor_to_step(self, qty, step, expected):
        assert ns.floor_to_step(qty, step) == pytest.approx(expected)

    @pytest.mark.parametrize("step", [0, -0.1, NAN, None])
    def test_floor_to_step_rejects_bad_step(self, step):
        with pytest.raises(ns.InvalidNumericInput):
            ns.floor_to_step(1.0, step)

    def test_step_precision_and_collections(self):
        assert ns.step_precision(0.001) == 3
        assert ns.step_precision(1e-8) == 8
        assert ns.step_precision(10) == 0
        assert ns.finite_values([1, NAN, "2", None]) == [1.0, 2.0]
        assert ns.all_finite([1, 2]) and not ns.all_finite([1, NAN]) and not ns.all_finite([])
        assert ns.safe_quantity(NAN) == 0.0 and ns.safe_quantity(2) == 2.0
        assert ns.decimal_or_none("1.5") == Decimal("1.5")
        assert ns.decimal_or_none("NaN") is None and ns.decimal_or_none(True) is None


# ── testnet RiskGate ─────────────────────────────────────────────────────────

@pytest.fixture
def gate():
    from testnet_engine.risk_gate import RiskGate

    return RiskGate(starting_balance=10000.0)


def _eval(gate, positions, qty=0.001, price=50000.0, side="BUY", equity=10000.0):
    return gate.evaluate_risk("BTCUSDT", side, equity, positions, qty, price, "OK")


class TestRiskGate:
    def test_baseline_small_trade_is_accepted(self, gate):
        ok, reason, _ = _eval(gate, {})
        assert ok and reason == "RISK_OK"

    @pytest.mark.parametrize("bad_qty", [NAN, "lots", -1, INF])
    def test_corrupt_position_quantity_fails_closed(self, gate, bad_qty):
        """A NaN quantity used to make exposure NaN and every limit pass."""
        positions = {"ETHUSDT": {"status": "OPEN", "quantity": bad_qty, "entry_price": 3000.0, "side": "BUY"}}
        ok, reason, _ = _eval(gate, positions)
        assert not ok and reason == "INVALID_POSITION_STATE"

    def test_position_with_unknown_side_fails_closed(self, gate):
        positions = {"ETHUSDT": {"status": "OPEN", "quantity": 0.01, "entry_price": 3000.0, "side": "SIDEWAYS"}}
        assert _eval(gate, positions)[1] == "INVALID_POSITION_STATE"

    def test_closed_records_carry_no_exposure(self, gate):
        positions = {"ETHUSDT": {"status": "CLOSED", "quantity": 999, "entry_price": 3000.0, "side": "BUY"}}
        assert _eval(gate, positions)[0] is True

    @pytest.mark.parametrize("side", ["HOLD", "", None, 1])
    def test_unknown_order_side_is_rejected(self, gate, side):
        assert _eval(gate, {}, side=side)[1] == "INVALID_INPUT"

    @pytest.mark.parametrize("equity", BAD_NUMBERS + [0, -5])
    def test_invalid_equity_is_rejected(self, gate, equity):
        assert _eval(gate, {}, equity=equity)[1] == "INSUFFICIENT_EQUITY"

    def test_non_mapping_positions_rejected(self, gate):
        assert _eval(gate, [("ETH", {})])[1] == "INVALID_POSITION_STATE"

    def test_nan_trade_pnl_latches_accounting_fault(self, gate):
        """NaN PnL used to poison daily_realized_loss so the daily limit never fired."""
        gate.update_after_trade(NAN, 10000.0)
        assert gate.consecutive_losses == 1
        assert gate.daily_realized_loss == 0.0
        ok, reason, _ = _eval(gate, {})
        assert not ok and reason == "ACCOUNTING_FAULT"
        with pytest.raises(ValueError):
            gate.clear_accounting_fault("")
        gate.clear_accounting_fault("ops-oncall")
        assert _eval(gate, {})[0] is True

    def test_nan_equity_never_becomes_peak(self, gate):
        gate.update_after_trade(10.0, NAN)
        assert gate.peak_equity == 10000.0

    @pytest.mark.parametrize("equity, entry, stop", [
        (NAN, 100.0, 95.0), (10000.0, NAN, 95.0), (10000.0, 100.0, NAN), (INF, 100.0, 95.0),
        (10000.0, -100.0, -101.0), (10000.0, 100.0, 0), ("x", 100.0, 95.0),
    ])
    def test_position_size_invalid_inputs_return_zero(self, gate, equity, entry, stop):
        """NaN used to crash in math.floor; negative prices returned a NEGATIVE size."""
        assert gate.calculate_position_size(equity, entry, stop) == 0.0

    @pytest.mark.parametrize("step", [0, -0.001, NAN, "abc"])
    def test_position_size_invalid_step_returns_zero(self, gate, step):
        assert gate.calculate_position_size(10000.0, 100.0, 95.0, {"stepSize": step, "minNotional": 1}) == 0.0

    def test_wrong_side_stop_is_not_sized(self, gate):
        filters = {"stepSize": 0.001, "minNotional": 1.0}
        assert gate.calculate_position_size(10000.0, 100.0, 105.0, filters, side="BUY") == 0.0
        assert gate.calculate_position_size(10000.0, 100.0, 95.0, filters, side="SELL") == 0.0
        assert gate.calculate_position_size(10000.0, 100.0, 95.0, filters, side="BUY") > 0

    def test_size_is_floored_to_step_and_capped_by_exposure(self, gate):
        qty = gate.calculate_position_size(10000.0, 100.0, 99.999, {"stepSize": 0.001, "minNotional": 1.0})
        assert qty == pytest.approx(2.0)  # capped at 2% single-asset exposure (10000*0.02/100)
        assert round(qty / 0.001) * 0.001 == pytest.approx(qty)

    def test_nan_confidence_does_not_change_risk(self, gate):
        filters = {"stepSize": 0.0001, "minNotional": 1.0}
        base = gate.calculate_position_size(10000.0, 100.0, 90.0, filters)
        assert gate.calculate_position_size(10000.0, 100.0, 90.0, filters, confidence=NAN, tp_price=130.0) == base
        assert gate.calculate_position_size(10000.0, 100.0, 90.0, filters, confidence=0.7, tp_price=NAN) == base


# ── execution entry validation ───────────────────────────────────────────────

class TestExecutionEntryValidation:
    @pytest.mark.parametrize("kwargs", [
        {"side": "HOLD", "quantity": 1, "sl": 90, "tp": 110},
        {"side": "BUY", "quantity": NAN, "sl": 90, "tp": 110},
        {"side": "BUY", "quantity": -1, "sl": 90, "tp": 110},
        {"side": "BUY", "quantity": 1, "sl": NAN, "tp": 110},
        {"side": "BUY", "quantity": 1, "sl": 90, "tp": INF},
        {"side": "BUY", "quantity": 1, "sl": 110, "tp": 90},
        {"side": "SELL", "quantity": 1, "sl": 90, "tp": 110},
        {"side": "BUY", "quantity": 1, "sl": 90, "tp": None},
        {"side": "BUY", "quantity": 1, "sl": None, "tp": 110},
    ])
    def test_invalid_entries_are_refused(self, kwargs):
        from execution import InvalidOrderRequest, _validate_entry_request

        with pytest.raises(InvalidOrderRequest):
            _validate_entry_request(kwargs["side"], kwargs["quantity"], kwargs["sl"], kwargs["tp"])

    def test_valid_entry_normalises_strings(self):
        from execution import _validate_entry_request

        assert _validate_entry_request("BUY", "0.5", "90", 110) == (0.5, 90.0, 110.0)

    @pytest.mark.usefixtures("pinned_testnet_mode")
    def test_place_market_order_refuses_before_touching_the_venue(self, monkeypatch, tmp_path):
        """A NaN stop used to be discovered only after the entry filled."""
        import execution

        monkeypatch.setattr(execution, "ACTIVE_TRADES_FILE", str(tmp_path / "active.json"))
        client = MagicMock()
        monkeypatch.setattr(execution, "get_exchange_client", lambda: client)
        with pytest.raises(execution.InvalidOrderRequest):
            execution.place_market_order("s", "BUY", "BTCUSDT", 0.01, sl=NAN, tp=51000, client_order_id="cid-nan")
        client.create_order.assert_not_called()
        # The idempotency record was released so a corrected retry can proceed.
        from audit.audit_manager import get_idempotency_store

        assert "cid-nan" not in get_idempotency_store()._cache

    @pytest.mark.usefixtures("pinned_testnet_mode")
    @pytest.mark.parametrize("leverage", [0, -3, 2.5, NAN, 500, "x"])
    def test_futures_rejects_invalid_leverage(self, monkeypatch, tmp_path, leverage):
        import execution

        monkeypatch.setattr(execution, "ACTIVE_TRADES_FILE", str(tmp_path / "active.json"))
        client = MagicMock()
        monkeypatch.setattr(execution, "get_exchange_client", lambda: client)
        with pytest.raises(execution.InvalidOrderRequest):
            execution.place_futures_market_order("s", "BUY", "BTCUSDT", 0.01, sl=49000, tp=51000, leverage=leverage)
        client.futures_create_order.assert_not_called()


# ── protection layer ─────────────────────────────────────────────────────────

class TestProtection:
    @pytest.mark.parametrize("price, tick", [(NAN, 0.01), (INF, 0.01), (-1, 0.01), (100.0, 0), (100.0, NAN), (0.001, 0.01)])
    def test_round_price_rejects_invalid(self, price, tick):
        from testnet_engine.protection import round_price

        with pytest.raises(ValueError):
            round_price(price, tick, 2)

    def test_round_price_is_exact_decimal_floor(self):
        from testnet_engine.protection import round_price

        assert round_price(0.3, 0.1, 1) == "0.3"  # float floor gave 0.2
        assert round_price(64250.789, 0.01, 2) == "64250.78"

    @pytest.mark.parametrize("qty, step", [(NAN, 0.001), (INF, 0.001), (1.0, 0), (1.0, -1)])
    def test_round_qty_rejects_invalid(self, qty, step):
        from testnet_engine.protection import round_qty

        with pytest.raises(ValueError):
            round_qty(qty, step, 3)

    def test_round_qty_floors_and_zero_for_dust(self):
        from testnet_engine.protection import round_qty

        assert round_qty(0.0009, 0.001, 3) == 0.0
        assert round_qty(-5, 0.001, 3) == 0.0
        assert round_qty(1.2349, 0.001, 3) == 1.234

    @pytest.mark.parametrize("field, value", [("executed_qty", NAN), ("actual_fill_price", NAN),
                                              ("sl_price", NAN), ("tp_price", INF), ("sl_price", -1)])
    def test_oco_rejects_non_finite_inputs_before_any_call(self, field, value):
        from testnet_engine.protection import place_oco_protection

        args = {"executed_qty": 0.5, "actual_fill_price": 50000.0, "sl_price": 49000.0, "tp_price": 52000.0}
        args[field] = value
        client = MagicMock()
        with pytest.raises(ValueError):
            place_oco_protection(client=client, symbol="BTCUSDT", entry_side="BUY", **args)
        client.create_oco_order.assert_not_called()

    def test_oco_without_list_id_is_a_failure(self):
        from testnet_engine.protection import place_oco_protection

        client = MagicMock()
        client.get_symbol_info.return_value = {"filters": []}
        client.create_oco_order.return_value = {"orderReports": []}
        with pytest.raises(ValueError, match="orderListId"):
            place_oco_protection(client=client, symbol="BTCUSDT", entry_side="BUY", executed_qty=0.5,
                                 actual_fill_price=50000.0, sl_price=49000.0, tp_price=52000.0)

    @pytest.mark.parametrize("bad_filter", [
        {"filterType": "PRICE_FILTER", "tickSize": "0"},
        {"filterType": "LOT_SIZE", "stepSize": "nan"},
        {"filterType": "NOTIONAL", "minNotional": "-5"},
    ])
    def test_invalid_exchange_filters_are_rejected(self, bad_filter):
        from testnet_engine.protection import _get_symbol_filters

        client = MagicMock()
        client.get_symbol_info.return_value = {"filters": [bad_filter]}
        with pytest.raises(ValueError):
            _get_symbol_filters(client, "BTCUSDT")

    def test_futures_tp_failure_cancels_the_orphan_stop(self):
        from testnet_engine.protection import place_futures_bracket_protection

        client = MagicMock()
        client.futures_exchange_info.return_value = None
        client.futures_create_order.side_effect = [{"algoId": 77}, RuntimeError("TP rejected")]
        with pytest.raises(RuntimeError):
            place_futures_bracket_protection(client, "BTCUSDT", "BUY", 0.01, 50000.0, 49000.0, 52000.0)
        client.futures_cancel_algo_order.assert_called_once_with(algoId=77)

    def test_futures_bracket_rejects_unknown_side(self):
        from testnet_engine.protection import place_futures_bracket_protection

        with pytest.raises(ValueError):
            place_futures_bracket_protection(MagicMock(), "BTCUSDT", "HOLD", 0.01, 50000.0, 49000.0, 52000.0)

    def test_failed_position_query_never_reports_closed(self):
        """A failed algo query used to count as 'fired' and book the entry fill as the close."""
        from testnet_engine.protection import check_futures_bracket_status

        client = MagicMock()
        client.futures_position_information.side_effect = RuntimeError("timeout")
        client.futures_get_open_algo_orders.side_effect = RuntimeError("timeout")
        client.futures_account_trades.return_value = [{"price": "50000", "qty": "0.01", "realizedPnl": "0"}]
        assert check_futures_bracket_status(client, "BTCUSDT", 1, 2)["position_closed"] is False

    def test_open_hedge_leg_is_not_closed(self):
        from testnet_engine.protection import check_futures_bracket_status

        client = MagicMock()
        client.futures_position_information.return_value = [{"positionAmt": "0"}, {"positionAmt": "-0.5"}]
        assert check_futures_bracket_status(client, "BTCUSDT", 1, 2)["position_closed"] is False

    def test_flat_position_with_unreadable_trade_is_not_booked(self):
        from testnet_engine.protection import check_futures_bracket_status

        client = MagicMock()
        client.futures_position_information.return_value = [{"positionAmt": "0"}]
        client.futures_account_trades.return_value = [{"price": "0", "qty": "0.01", "realizedPnl": "5"}]
        assert check_futures_bracket_status(client, "BTCUSDT", 1, 2)["position_closed"] is False

    def test_flat_position_with_valid_trade_is_closed(self):
        from testnet_engine.protection import check_futures_bracket_status

        client = MagicMock()
        client.futures_position_information.return_value = [{"positionAmt": "0.000"}]
        client.futures_account_trades.return_value = [{"price": "52000", "qty": "0.01", "realizedPnl": "20"}]
        result = check_futures_bracket_status(client, "BTCUSDT", 1, 2)
        assert result["position_closed"] and result["tp_filled"] and result["close_avg_price"] == 52000.0

    def test_oco_all_done_without_filled_leg_raises(self):
        from testnet_engine.protection import check_oco_status

        client = MagicMock()
        client.v3_get_order_list.return_value = {"listOrderStatus": "ALL_DONE", "orders": [{"orderId": 1}]}
        client.get_order.return_value = {"type": "LIMIT_MAKER", "status": "EXPIRED"}
        with pytest.raises(ValueError, match="no leg reports FILLED"):
            check_oco_status(client, "BTCUSDT", 9)

    def test_oco_filled_leg_with_zero_fill_raises(self):
        from testnet_engine.protection import check_oco_status

        client = MagicMock()
        client.v3_get_order_list.return_value = {"listOrderStatus": "ALL_DONE", "orders": [{"orderId": 1}]}
        client.get_order.return_value = {"type": "STOP_LOSS_LIMIT", "status": "FILLED",
                                         "executedQty": "0", "cummulativeQuoteQty": "0"}
        with pytest.raises(ValueError, match="unreadable fill"):
            check_oco_status(client, "BTCUSDT", 9)

    @pytest.mark.parametrize("args", [
        ("BUY", NAN, 100.0, 0.0, 1.0, 110.0, 0.0), ("BUY", 1.0, 100.0, 0.0, 1.0, 0.0, 0.0),
        ("BUY", 1.0, 100.0, -1.0, 1.0, 110.0, 0.0), ("HOLD", 1.0, 100.0, 0.0, 1.0, 110.0, 0.0),
    ])
    def test_compute_net_pnl_rejects_invalid(self, args):
        from testnet_engine.protection import compute_net_pnl

        with pytest.raises(ValueError):
            compute_net_pnl(*args)

    def test_compute_net_pnl_values(self):
        from testnet_engine.protection import compute_net_pnl

        assert compute_net_pnl("BUY", 1.0, 100.0, 0.1, 1.0, 110.0, 0.1) == pytest.approx((10.0, 9.8))
        assert compute_net_pnl("SELL", 2.0, 100.0, 0.0, 1.0, 90.0, 0.0) == pytest.approx((10.0, 10.0))


# ── risk package ─────────────────────────────────────────────────────────────

class TestDynamicRiskManager:
    @pytest.fixture
    def mgr(self):
        from risk.dynamic_risk_manager import DynamicRiskManager

        return DynamicRiskManager()

    def test_nan_win_rate_is_no_trade_not_max_bet(self, mgr):
        """min(0.99, nan) is 0.99: NaN used to select the LARGEST Kelly bet."""
        assert mgr.calculate_kelly_size(10000, 100, NAN, 2.0) == 0.0
        assert mgr.calculate_kelly_size(10000, 100, 58, 2.0) == 0.0  # percent passed as fraction
        assert mgr.calculate_kelly_size(10000, 100, 0.6, INF) == 0.0
        assert mgr.calculate_kelly_size(10000, 100, 0.6, 2.0) > 0

    @pytest.mark.parametrize("args", [(INF, 100, 95), (NAN, 100, 95), (10000, NAN, 95), (10000, 100, NAN), (10000, 100, 100)])
    def test_fixed_fractional_invalid(self, mgr, args):
        assert mgr.calculate_fixed_fractional_size(*args) == 0.0

    def test_explicit_zero_or_negative_fraction_means_no_trade(self, mgr):
        assert mgr.calculate_fixed_fractional_size(10000, 100, 95, fraction_pct=0) == 0.0
        assert mgr.calculate_fixed_fractional_size(10000, 100, 95, fraction_pct=-0.5) == 0.0
        assert mgr.calculate_fixed_fractional_size(10000, 100, 95, fraction_pct=NAN) == 0.0
        assert mgr.calculate_fixed_fractional_size(10000, 100, 95, fraction_pct=0.5) == \
            mgr.calculate_fixed_fractional_size(10000, 100, 95)  # capped at budget

    @pytest.mark.parametrize("args", [(10000, 100, NAN), (10000, 100, 2.0, 0), (10000, 100, 2.0, -2), (10000, 100, 2.0, 2.0, -0.01)])
    def test_volatility_size_invalid(self, mgr, args):
        assert mgr.calculate_volatility_size(*args) == 0.0

    def test_unknown_drawdown_halts_sizing(self, mgr):
        assert mgr.adjust_size_for_drawdown(1.0, NAN) == (0.0, 0.0)
        assert mgr.adjust_size_for_drawdown(NAN, 1.0) == (0.0, 0.0)
        assert mgr.adjust_size_for_drawdown(1.0, 7.5) == (0.75, 0.75)

    def test_risk_parity_rejects_invalid_vols(self, mgr):
        with pytest.raises(ValueError):
            mgr.calculate_risk_parity_weights({"a": 0.2, "b": 0.0})
        with pytest.raises(ValueError):
            mgr.calculate_risk_parity_weights({"a": NAN})
        weights = mgr.calculate_risk_parity_weights({"a": 0.1, "b": 0.2})
        assert weights["a"] == pytest.approx(2 / 3, abs=1e-3)

    def test_var_reports_unknown_not_zero(self, mgr):
        assert all(math.isnan(v) for v in mgr.compute_var_cvar([0.01] * 5))
        with pytest.raises(ValueError):
            mgr.compute_var_cvar([0.01] * 20, confidence_level=1.5)
        var_pct, _, cvar_pct, _ = mgr.compute_var_cvar([0.01, -0.02, NAN] + [0.0] * 20)
        assert math.isfinite(var_pct) and cvar_pct >= var_pct

    def test_budget_validation(self):
        from risk.dynamic_risk_manager import RiskBudget

        with pytest.raises(ValueError):
            RiskBudget(max_risk_per_trade_pct=NAN)
        with pytest.raises(ValueError):
            RiskBudget(max_daily_loss_pct=3.0)  # percent passed as fraction


class TestVolatilitySizing:
    def test_unmeasurable_volatility_is_nan_not_default(self):
        from risk.volatility_sizing import VolatilitySizingEngine

        eng = VolatilitySizingEngine()
        assert math.isnan(eng.calculate_realized_volatility(pd.Series([100.0] * 5)))
        assert math.isnan(eng.calculate_realized_volatility(pd.Series([100.0, -1.0] * 15)))
        assert math.isnan(eng.calculate_realized_volatility(pd.Series([100.0, NAN] * 15)))
        prices = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, 40))))
        assert 0 < eng.calculate_realized_volatility(prices) < 1

    def test_regime_and_weight(self):
        from risk.volatility_sizing import VolatilitySizingEngine

        eng = VolatilitySizingEngine()
        assert eng.classify_volatility_regime(NAN) == "EXTREME_VOLATILITY"
        assert eng.classify_volatility_regime(0.25, baseline_vol=0) == "EXTREME_VOLATILITY"
        assert eng.classify_volatility_regime(0.1) == "LOW_VOLATILITY"
        assert eng.compute_vol_targeted_weight(NAN) == 0.0
        assert eng.compute_vol_targeted_weight(0.3, max_leverage=NAN) == 0.0
        assert eng.compute_vol_targeted_weight(0.3) == 0.5
        with pytest.raises(ValueError):
            VolatilitySizingEngine(target_annual_vol=0)


class TestDrawdownController:
    def _ctrl(self):
        from risk.drawdown_controller import DrawdownController

        return DrawdownController(initial_equity=1000.0)

    @pytest.mark.parametrize("bad", [NAN, INF, -INF, None, "x"])
    def test_unreadable_equity_is_critical(self, bad):
        ctrl = self._ctrl()
        status = ctrl.update_equity(bad)
        assert status.level == "CRITICAL_12PCT"
        assert status.allow_new_entries is False
        assert ctrl.peak_equity == 1000.0  # never poisoned by inf

    def test_recovery_cannot_be_bypassed_by_equity_bounce(self):
        ctrl = self._ctrl()
        ctrl.update_equity(850.0)  # 15% DD → CRITICAL
        status = ctrl.update_equity(1000.0)  # full recovery of equity
        assert status.level == "RECOVERY_PAPER_VALIDATION"
        assert status.allow_new_entries is False and status.position_size_multiplier == 0.0
        assert ctrl.advance_progressive_reentry() == 1.0  # not advanced during recovery
        assert ctrl.progress_recovery_paper_trading(24) == (False, 0.0)
        assert ctrl.progress_recovery_paper_trading(24) == (True, 0.25)
        status = ctrl.update_equity(1000.0)
        assert status.allow_new_entries is True and status.position_size_multiplier == 0.25

    @pytest.mark.parametrize("hours", [INF, NAN, -1, 1000])
    def test_recovery_hours_validated(self, hours):
        ctrl = self._ctrl()
        ctrl.update_equity(800.0)
        with pytest.raises(ValueError):
            ctrl.progress_recovery_paper_trading(hours)

    @pytest.mark.parametrize("kwargs", [
        {"warning_drawdown_pct": NAN}, {"critical_drawdown_pct": 0}, {"critical_drawdown_pct": 150},
        {"warning_drawdown_pct": 0.2, "critical_drawdown_pct": 0.1}, {"initial_equity": 0},
    ])
    def test_invalid_configuration_rejected(self, kwargs):
        from risk.drawdown_controller import DrawdownController

        with pytest.raises(ValueError):
            DrawdownController(**kwargs)

    def test_small_accounts_report_true_drawdown(self):
        from risk.drawdown_controller import DrawdownController

        ctrl = DrawdownController(initial_equity=0.5)
        assert ctrl.update_equity(0.25).drawdown_pct == 50.0  # max(peak, 1.0) reported 25%


class TestCircuitBreakers:
    def test_nan_slippage_never_resets_breaches(self):
        from risk.circuit_breakers import CircuitBreakerEngine

        cb = CircuitBreakerEngine()
        cb.record_order_execution_slippage(50)
        cb.record_order_execution_slippage(50)
        assert cb.record_order_execution_slippage(NAN) is True
        assert cb.breakers["execution_quality"].is_tripped
        with pytest.raises(ValueError):
            cb.record_order_execution_slippage(1.0, normal_slippage_bps=0)

    def test_nan_correlation_trips(self):
        from risk.circuit_breakers import CircuitBreakerEngine

        cb = CircuitBreakerEngine()
        assert cb.check_correlation_breakdown(NAN) is True
        assert cb.check_correlation_breakdown(5.0) is True
        assert cb.check_correlation_breakdown(0.5) is False

    def test_unreadable_latency_counts_as_slow(self):
        from risk.circuit_breakers import CircuitBreakerEngine

        cb = CircuitBreakerEngine()
        for _ in range(9):
            assert cb.record_api_latency(0.1) is False
        # One unreadable sample is recorded as infinitely slow but cannot move
        # the median of a mostly-healthy window (it used to make it NaN).
        assert cb.record_api_latency(NAN) is False
        for _ in range(10):
            cb.record_api_latency(-1)  # clock errors / timeouts
        assert cb.breakers["api_latency"].is_tripped
        assert all(math.isinf(v) or v == 0.1 for v in cb.recent_latencies)

    def test_unreadable_volatility_trips_and_history_nan_ignored(self):
        from risk.circuit_breakers import CircuitBreakerEngine

        cb = CircuitBreakerEngine()
        assert cb.check_volatility_circuit_breaker(NAN, [0.5] * 20) is True
        cb2 = CircuitBreakerEngine()
        history = [0.5 + 0.01 * (i % 3) for i in range(20)] + [NAN]
        assert cb2.check_volatility_circuit_breaker(0.51, history) is False
        assert cb2.check_volatility_circuit_breaker(5.0, history) is True

    def test_live_enforcer_is_reexported_once(self):
        import risk.circuit_breakers as cbm
        import risk.live_enforcer as lem

        assert cbm.LiveRiskEnforcer is lem.LiveRiskEnforcer
        assert not hasattr(lem, "CircuitBreakerEngine")


class TestLiveEnforcer:
    def _enf(self, **kw):
        from risk.live_enforcer import LiveRiskEnforcer

        return LiveRiskEnforcer(level=1, initial_capital=1000.0, **kw)

    def test_nan_daily_pnl_halts(self):
        enf = self._enf()
        ok, _ = enf.evaluate_daily_loss(NAN, 1000.0)
        assert not ok and enf.status.is_halted

    @pytest.mark.parametrize("peak, current", [(0, 900), (NAN, 900), (1000, NAN), (-5, 1)])
    def test_unverifiable_drawdown_halts(self, peak, current):
        enf = self._enf()
        ok, _ = enf.evaluate_drawdown(peak, current)
        assert not ok and enf.status.requires_reauthorization

    def test_validate_new_entry_enforces_daily_loss_and_inputs(self):
        enf = self._enf()
        assert enf.validate_new_entry("BTC/USDT", 40.0, [], 1000.0)[0] is True
        assert enf.validate_new_entry("BTC/USDT", NAN, [], 1000.0)[0] is False
        assert enf.validate_new_entry("BTC/USDT", -5, [], 1000.0)[0] is False
        assert enf.validate_new_entry("BTC/USDT", 40.0, [], NAN)[0] is False
        assert enf.validate_new_entry("BTC/USDT", 40.0, [], 1000.0, rolling_vol_pct=NAN)[0] is False
        # today_realized_loss used to be accepted and ignored.
        blocked = self._enf()
        ok, msg = blocked.validate_new_entry("BTC/USDT", 40.0, [], 1000.0, today_realized_loss=25.0)
        assert not ok and "Daily loss limit" in msg

    @pytest.mark.parametrize("window", [(22, 6), (0, 25), ("a", 5), (3,), (1.5, 4)])
    def test_trading_window_validated(self, window):
        with pytest.raises(ValueError):
            self._enf(trading_window_hours=window)

    def test_zero_capital_blocks_entries_but_constructs(self):
        from risk.live_enforcer import LiveRiskEnforcer

        enf = LiveRiskEnforcer(level=1, initial_capital=0.0)
        assert enf.validate_new_entry("BTC/USDT", 1.0, [], 0.0)[0] is False


class TestLiveRiskEnforcerLegacy:
    def test_nan_inputs_fail_closed(self):
        from risk.live_risk_enforcer import LiveRiskEnforcer

        enf = LiveRiskEnforcer(level=1, current_equity=1000.0)
        assert enf.check_order_admissibility("BTCUSDT", NAN, "s", [])[0] is False
        assert enf.check_order_admissibility("BTCUSDT", 10.0, "s", [], realized_vol_24h=NAN)[0] is False
        enf.record_realized_trade_pnl(NAN)
        assert enf.daily_halt_active
        enf2 = LiveRiskEnforcer(level=1, current_equity=1000.0)
        enf2.update_live_equity(INF)
        assert enf2.circuit_breaker_active and enf2.peak_equity == 1000.0
        with pytest.raises(ValueError):
            LiveRiskEnforcer(current_equity=NAN)


class TestRiskOrchestrator:
    def _orch(self, tmp_path):
        from risk.risk_orchestrator import RiskOrchestrator

        return RiskOrchestrator(log_file=str(tmp_path / "orch.jsonl"), initial_equity=5000.0)

    @pytest.mark.parametrize("heat", [NAN, -1, None, "hot"])
    def test_unknown_heat_blocks(self, tmp_path, heat):
        allow, size, reason = self._orch(tmp_path).evaluate_new_entry_risk("BTC", "s", 1.0, 5000.0, heat)
        assert not allow and size == 0.0 and reason == "BLOCKED_PORTFOLIO_HEAT_UNKNOWN"

    @pytest.mark.parametrize("size", [NAN, -1, 0, INF])
    def test_invalid_requested_size_blocks(self, tmp_path, size):
        assert self._orch(tmp_path).evaluate_new_entry_risk("BTC", "s", size, 5000.0, 10.0)[0] is False

    def test_action_level_drawdown_blocks_entries(self, tmp_path):
        orch = self._orch(tmp_path)
        allow, _, reason = orch.evaluate_new_entry_risk("BTC", "s", 1.0, 4500.0, 10.0)  # 10% DD → ACTION
        assert not allow and reason == "BLOCKED_DRAWDOWN_ACTION_8PCT"

    def test_nan_equity_is_critical(self, tmp_path):
        allow, _, reason = self._orch(tmp_path).evaluate_new_entry_risk("BTC", "s", 1.0, NAN, 10.0)
        assert not allow and reason == "FLATTEN_AND_HALT_CRITICAL_DRAWDOWN"

    def test_var_unknown_and_decisions_log_real_values(self, tmp_path):
        from risk.risk_orchestrator import RiskOrchestrator

        assert RiskOrchestrator.calculate_var_and_cvar([1.0] * 5) == (None, None)
        returns = [-3.5, -2.1, -1.8, -0.5, 0.4, 1.2, 1.5, 2.0, 2.8, -4.2]
        var, cvar = RiskOrchestrator.calculate_var_and_cvar(returns)
        assert var == 4.2 and cvar == 4.2  # tail includes the VaR observation; no invented 1.25x
        orch = self._orch(tmp_path)
        orch.evaluate_new_entry_risk("BTC", "s", 1.0, 5000.0, 10.0, portfolio_returns=returns)
        logged = json.loads((tmp_path / "orch.jsonl").read_text().splitlines()[-1])
        assert logged["var_95_pct"] == 4.2 and logged["cvar_95_pct"] == 4.2
        orch.evaluate_new_entry_risk("BTC", "s", 1.0, 5000.0, 10.0)
        logged = json.loads((tmp_path / "orch.jsonl").read_text().splitlines()[-1])
        assert logged["var_95_pct"] is None  # was hard-coded 2.1

    def test_heat_rejects_negative_risk(self):
        from risk.risk_orchestrator import RiskOrchestrator

        with pytest.raises(ValueError):
            RiskOrchestrator.calculate_portfolio_heat([{"risk_pct": -5}])
        assert RiskOrchestrator.calculate_portfolio_heat([{"risk_pct": 2.0}], np.array([[NAN]])) == 20.0

    def test_measured_snapshot_from_recorded_state(self, tmp_path):
        from risk.risk_orchestrator import build_measured_snapshot

        hist = tmp_path / "eq.jsonl"
        lines = [json.dumps({"equity": 1000 + 10 * (i % 4) - i}) for i in range(30)] + ["{torn"]
        hist.write_text("\n".join(lines))
        trades = tmp_path / "active.json"
        trades.write_text(json.dumps([{"symbol": "BTCUSDT", "quantity": 0.01, "entry_price": 50000.0, "sl_price": 49000.0}]))
        snap = build_measured_snapshot(str(hist), str(trades))
        assert snap["equity_points"] == 30 and snap["equity_history_skipped_lines"] == 1
        assert snap["var_95_pct"] is not None and snap["drawdown_metrics"]["peak_equity"] > 0
        assert snap["portfolio_heat_pct"] > 0 and snap["open_positions_measured"] == 1
        assert snap["correlation_matrix"] is None and "correlation_matrix" in snap["unmeasured_reasons"]

    def test_measured_snapshot_with_no_state(self, tmp_path):
        from risk.risk_orchestrator import build_measured_snapshot

        snap = build_measured_snapshot(str(tmp_path / "none.jsonl"), str(tmp_path / "none.json"))
        assert snap["var_95_pct"] is None and snap["drawdown_metrics"] is None
        assert snap["portfolio_heat_pct"] is None
        assert set(snap["unmeasured_reasons"]) >= {"var_95_pct", "drawdown_metrics", "portfolio_heat_pct"}

    def test_endpoint_reports_measured_values_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TESTNET_EQUITY_HISTORY_FILE", str(tmp_path / "none.jsonl"))
        monkeypatch.setenv("ACTIVE_TRADES_FILE", str(tmp_path / "none.json"))
        from dashboard import app

        body = app.test_client().get("/api/risk/orchestration").get_json()
        assert body["portfolio_heat_pct"] is None and body["var_95_pct"] is None
        assert body["correlation_matrix"] is None
        assert body["strategy_allocation_basis"] == "configured_prior"


class TestStrategyCoordinator:
    def test_unknown_strategies_do_not_crash(self):
        from risk.strategy_coordinator import StrategyCoordinator

        direction, winner, mult = StrategyCoordinator().resolve_signal_conflict("X", {"a": 1, "b": -1})
        assert (direction, winner, mult) == (0, "none", 0.0)  # tie → stand aside (was KeyError)

    def test_neutral_first_vote_does_not_discard_agreement(self):
        from risk.strategy_coordinator import StrategyCoordinator

        assert StrategyCoordinator().resolve_signal_conflict("X", {"a": 0, "strategy_scalper": 1})[0] == 1
        assert StrategyCoordinator().resolve_signal_conflict("X", {"a": NAN})[0] == 0

    def test_higher_sharpe_wins_conflict(self):
        from risk.strategy_coordinator import StrategyCoordinator

        res = StrategyCoordinator().resolve_signal_conflict("X", {"strategy_supertrend": 1, "strategy_scalper": -1})
        assert res == (1, "strategy_supertrend", 0.5)


# ── other sizing engines ─────────────────────────────────────────────────────

class TestUpgradeRiskManager:
    def _mgr(self):
        from stratex_upgrade.risk import RiskManager, RiskState

        d = Decimal(10000)
        return RiskManager(RiskState(equity=d, starting_equity=d, peak_equity=d, daily_start_equity=d))

    @pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), float("nan")])
    def test_non_finite_equity_halts(self, bad):
        mgr = self._mgr()
        mgr.update_equity(bad)
        assert mgr.state.trading_halted and mgr.state.halt_reason == "NON_FINITE_EQUITY"

    def test_non_finite_pnl_halts_and_reserve_rejects(self):
        mgr = self._mgr()
        mgr.record_realized(Decimal("NaN"))
        assert mgr.state.halt_reason == "NON_FINITE_PNL"
        assert mgr.reserve_notional(Decimal("NaN")) is False
        mgr.release_notional(Decimal("NaN"))
        assert mgr.reserved_notional() == 0

    def test_calculate_qty_nan_confidence_is_conservative(self):
        from stratex_upgrade.models import InstrumentRules

        mgr = self._mgr()
        rules = InstrumentRules("BTCUSDT", Decimal("0.01"), Decimal("0.0001"), Decimal("0.0001"), Decimal("1"))
        full = mgr.calculate_qty(Decimal(100), Decimal(50), rules, confidence=1.0)
        nan_conf = mgr.calculate_qty(Decimal(100), Decimal(50), rules, confidence=float("nan"))
        low = mgr.calculate_qty(Decimal(100), Decimal(50), rules, confidence=0.0)
        assert nan_conf == low < full
        assert mgr.calculate_qty(Decimal("NaN"), Decimal(90), rules) == 0


def test_backtest_engine_qty_never_nan_or_negative():
    from backtest_engine import BacktestEngine

    engine = BacktestEngine(pd.DataFrame(), [])
    for entry, stop in [(100.0, NAN), (NAN, 95.0), (-100.0, -101.0), (100.0, INF), (100.0, 100.0)]:
        assert engine._calculate_qty(entry, stop) == 0.0
    assert engine._calculate_qty(100.0, 95.0) > 0


class TestBacktraderSizing:
    @pytest.mark.parametrize("ctor, kwargs", [
        ("FixedSize", {"size": -1}), ("FixedSize", {"size": NAN}), ("PercentSizer", {"percent": 150}),
        ("VolatilitySizer", {"atr_multiplier": 0}), ("KellySizer", {"win_rate": 1.5}), ("KellySizer", {"fraction": 2}),
    ])
    def test_invalid_sizer_parameters_rejected(self, ctor, kwargs):
        from stratex_backtrader_adapter import sizers

        with pytest.raises(ValueError):
            getattr(sizers, ctor)(**kwargs)

    def test_volatility_sizer_needs_measured_atr(self):
        from stratex_backtrader_adapter import sizers

        broker = MagicMock()
        broker.get_value.return_value = 10000.0
        sizer = sizers.VolatilitySizer()
        assert sizer.get_size(broker, "X", 100.0) == 0.0  # no invented 2% ATR
        assert sizer.get_size(broker, "X", 100.0, atr=2.0) == pytest.approx(25.0)
        broker.get_value.return_value = NAN
        assert sizer.get_size(broker, "X", 100.0, atr=2.0) == 0.0

    def test_zero_size_from_sizer_is_rejected_not_one_unit(self):
        from stratex_backtrader_adapter.models import OrderStatus
        from stratex_backtrader_adapter.strategy import Strategy

        broker = MagicMock()
        sizer = MagicMock()
        sizer.get_size.return_value = 0.0
        df = pd.DataFrame({"close": [100.0, 101.0]})
        strat = Strategy(broker=broker, data_feeds={"MAIN": df}, sizer=sizer)
        strat.symbol = "MAIN"
        order = strat.buy()
        assert order.status == OrderStatus.REJECTED
        broker.submit_order.assert_not_called()
        with pytest.raises(ValueError):
            strat.buy(symbol="UNKNOWN")  # used to trade at a fabricated price of 1.0
        with pytest.raises(ValueError):
            strat.sell(size=NAN)


class TestPaperEngine:
    def _portfolio(self, tmp_path):
        from paper_engine.portfolio import PaperPortfolio

        return PaperPortfolio(filename=str(tmp_path / "p.json"), ledger_file=str(tmp_path / "l.jsonl"),
                              equity_file=str(tmp_path / "e.jsonl"))

    @pytest.mark.parametrize("amount", [NAN, -10, 0, INF, "x"])
    def test_margin_amounts_validated(self, tmp_path, amount):
        p = self._portfolio(tmp_path)
        cash = p.cash
        with pytest.raises(ValueError):
            p.allocate_margin(amount, f"evt-{amount}")
        assert p.cash == cash

    def test_release_more_than_allocated_rejected(self, tmp_path):
        p = self._portfolio(tmp_path)
        p.allocate_margin(100.0, "a1")
        with pytest.raises(ValueError):
            p.release_margin(500.0, "r1")

    @pytest.mark.parametrize("pnl", [NAN, INF, "5", None, True])
    def test_realized_pnl_must_be_finite(self, tmp_path, pnl):
        p = self._portfolio(tmp_path)
        with pytest.raises(ValueError):
            p.add_realized_pnl(pnl, f"pnl-{pnl}")
        assert math.isfinite(p.cash)

    @pytest.mark.parametrize("equity, notional", [(NAN, 100.0), (10000.0, NAN), (10000.0, -100.0), (INF, 1.0)])
    def test_risk_limits_fail_closed(self, tmp_path, equity, notional):
        p = self._portfolio(tmp_path)
        with pytest.raises(ValueError, match="Risk Block"):
            p.check_risk_limits(equity, notional)

    @pytest.mark.parametrize("direction, qty", [("BUY", -1), ("BUY", NAN), ("HOLD", 1), ("SELL", 0)])
    def test_simulator_validates_orders(self, tmp_path, direction, qty):
        from paper_engine.simulator import PaperSimulator

        sim = PaperSimulator(self._portfolio(tmp_path), MagicMock(), MagicMock())
        with pytest.raises(ValueError):
            sim.submit_market_order("BTCUSDT", direction, qty, 0.0)

    def test_simulator_rejects_crossed_or_nan_quotes(self, tmp_path):
        from paper_engine.simulator import PaperSimulator

        md = MagicMock()
        cost = MagicMock(entry_slip=0.0005, entry_fee=0.001)
        sim = PaperSimulator(self._portfolio(tmp_path), md, cost)
        for quote in [(NAN, 101.0, "x"), (101.0, 100.0, "x"), (0.0, 1.0, "x")]:
            md.get_bbo.return_value = quote
            with pytest.raises(ValueError):
                sim.submit_market_order("BTCUSDT", "BUY", 1.0, 0.0)


class TestTrailing:
    def test_unknown_side_never_trails(self):
        from testnet_engine.trailing import compute_trail_target

        assert compute_trail_target("HOLD", 100.0, 120.0, 95.0, 150.0, 5.0, None) == (None, None)

    def test_long_alias_is_supported(self):
        from testnet_engine.trailing import compute_trail_target

        long_res = compute_trail_target("LONG", 100.0, 103.0, 95.0, 150.0, 5.0, None)
        buy_res = compute_trail_target("BUY", 100.0, 103.0, 95.0, 150.0, 5.0, None)
        assert long_res == buy_res and buy_res[1] == "BREAKEVEN"

    @pytest.mark.parametrize("current_sl", [NAN, -5, "x"])
    def test_unreadable_current_stop_blocks_move(self, current_sl):
        from testnet_engine.trailing import compute_trail_target

        assert compute_trail_target("BUY", 100.0, 103.0, current_sl, 150.0, 5.0, None) == (None, None)

    def test_freqtrade_trailing_validates(self):
        from stratex_freqtrade_adapter.roi.trailing_engine import TrailingStopEngine

        with pytest.raises(ValueError):
            TrailingStopEngine(trailing_stop=True, trailing_stop_positive=0)
        eng = TrailingStopEngine(trailing_stop=True, trailing_stop_positive=0.01)
        assert eng.calculate_stop_price("BUY", 100.0, 105.0, NAN) is None
        assert eng.calculate_stop_price("FLAT", 100.0, 105.0, 110.0) is None
        assert eng.should_exit("BUY", 100.0, 108.0, 110.0) == (True, pytest.approx(108.9))
