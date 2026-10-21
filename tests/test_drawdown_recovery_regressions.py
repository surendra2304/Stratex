"""Regression coverage for renewed critical drawdown events."""

from risk.drawdown_controller import DrawdownController


def test_new_critical_drawdown_restarts_paper_recovery_window():
    controller = DrawdownController(initial_equity=10_000.0)

    status = controller.update_equity(8_700.0)
    assert status.level == "CRITICAL_12PCT"
    resumed, tier = controller.progress_recovery_paper_trading(hours_elapsed=24.0)
    assert resumed is False
    assert tier == 0.0
    assert status.clean_paper_hours_accumulated == 24.0

    # A bounce keeps the recovery gate closed; a later breach is a new event.
    status = controller.update_equity(9_500.0)
    assert status.in_recovery_mode is True
    assert status.allow_new_entries is False
    assert status.level == "RECOVERY_PAPER_VALIDATION"

    status = controller.update_equity(8_700.0)
    assert status.level == "CRITICAL_12PCT"
    assert status.clean_paper_hours_accumulated == 0.0
    assert status.allow_new_entries is False

    resumed, tier = controller.progress_recovery_paper_trading(hours_elapsed=24.0)
    assert resumed is False
    assert tier == 0.0
    assert status.clean_paper_hours_accumulated == 24.0
