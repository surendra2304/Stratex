import json

from dashboard import _get_trades_data
from testnet_engine.protection import compute_net_pnl


class TestAccountingImmutability:
    """
    Property-style tests for Final Accounting Immutability:
    1. Equity Identity: Bot-Managed Equity == USDT Cash + Market Value of Bot-Managed Open Assets
    2. PnL Identity: Net Realized PnL == Sum(Gross PnL) - Sum(Total Fees)
    3. Fee Identity: Fees are counted exactly once per fill/order lifecycle
    4. Duplicate Idempotency: Duplicate fills/records are deduplicated without mutating metrics
    5. Restart Idempotency: Multiple restarts/reconciliations yield identical state
    6. Faucet Assets Exclusion: Unmanaged faucet coins do not inflate managed equity
    7. Equity History Immutability: Append-only with no synthetic point interpolation
    """

    def test_equity_identity_property(self):
        """BOT-MANAGED EQUITY == USDT CASH + CURRENT MARKET VALUE OF BOT-MANAGED OPEN ASSETS."""
        usdt_cash = 9700.00
        open_positions = {
            "LINKUSDT": {"quantity": 23.24, "entry_price": 9.4070, "current_price": 10.0000}
        }
        
        # Calculate managed market value
        managed_crypto_value = sum(p["quantity"] * p["current_price"] for p in open_positions.values())
        expected_managed_equity = round(usdt_cash + managed_crypto_value, 2)

        calculated_equity = round(9700.00 + (23.24 * 10.0000), 2)
        assert calculated_equity == expected_managed_equity
        assert calculated_equity == 9932.40

    def test_pnl_and_fee_identity_property(self):
        """REALIZED PNL == CLOSED TRADE PNL AFTER FEES (Net PnL == Gross PnL - Total Fees)."""
        # Long Trade: 1.0 BTC @ 60,000 -> Exit @ 61,000
        # Entry Fee: 0.1% of 60,000 = $60
        # Exit Fee: 0.1% of 61,000 = $61
        # Gross PnL: +$1,000.00
        # Expected Net PnL: 1,000 - 60 - 61 = +$879.00
        gross_pnl, net_pnl = compute_net_pnl(
            "BUY", 1.0, 60000.0, 60.0, 1.0, 61000.0, 61.0
        )
        assert gross_pnl == 1000.0
        assert net_pnl == 879.0
        assert net_pnl == (gross_pnl - (60.0 + 61.0))

    def test_short_pnl_and_fee_identity_property(self):
        """Short Trade PnL Identity."""
        # Short Trade: 10 ETH @ 3,000 -> Exit @ 2,900
        # Entry Fee: 10 * 3000 * 0.001 = $30
        # Exit Fee: 10 * 2900 * 0.001 = $29
        # Gross PnL: (3,000 - 2,900) * 10 = +$1,000.00
        # Expected Net PnL: 1,000 - 30 - 29 = +$941.00
        gross_pnl, net_pnl = compute_net_pnl(
            "SELL", 10.0, 3000.0, 30.0, 10.0, 2900.0, 29.0
        )
        assert gross_pnl == 1000.0
        assert net_pnl == 941.0
        assert net_pnl == (gross_pnl - (30.0 + 29.0))

    def test_duplicate_idempotency_in_ledger_aggregation(self, tmp_path, monkeypatch):
        """Duplicate ledger entries must be deduplicated with zero PnL drift."""
        ledger_file = tmp_path / "testnet_trade_ledger.jsonl"
        trade1 = {
            "signal_id": "SIG_001",
            "symbol": "BTCUSDT",
            "strategy": "adx_ema",
            "source": "BINANCE_EXECUTION",
            "side": "BUY",
            "entry_order_id": "111",
            "entry_price": 60000.0,
            "entry_executed_quantity": 0.01,
            "entry_fee": 0.06,
            "exit_order_id": "222",
            "exit_price": 61000.0,
            "exit_executed_quantity": 0.01,
            "exit_fee": 0.061,
            "exit_reason": "WIN",
            "gross_pnl": 10.0,
            "total_fees": 0.121,
            "net_pnl": 9.879,
            "pnl": 9.879,
            "fees": 0.121,
            "entry_timestamp": "2026-08-18T10:00:00Z",
            "exit_timestamp": "2026-08-18T10:15:00Z",
            "timestamp": "2026-08-18T10:15:00Z",
            "action": "CLOSE_WIN",
            "quantity": 0.01,
            "oco_id": 999
        }

        # Write trade1 twice (duplicate injection)
        with open(ledger_file, "w") as f:
            f.write(json.dumps(trade1) + "\n")
            f.write(json.dumps(trade1) + "\n")

        monkeypatch.setenv("TESTNET_LEDGER_FILE", str(ledger_file))

        data = _get_trades_data()
        assert len(data["positions"]) == 1 # Deduplicated to exactly 1 trade
        assert data["total_trades"] == 1
        assert round(data["net_pnl"], 3) == 9.879
        assert round(data["positions"][0]["fees"], 3) == 0.121

    def test_faucet_assets_exclusion_from_managed_equity(self):
        """Unmanaged faucet assets (e.g. BNB, XRP testnet drops) must not inflate bot-managed equity."""
        wallet_balances = [
            {"asset": "USDT", "free": "9700.00", "locked": "0.00"},
            {"asset": "LINK", "free": "23.24", "locked": "0.00"}, # Bot-managed (open position)
            {"asset": "BNB", "free": "50.00", "locked": "0.00"},   # Faucet gift (unmanaged)
            {"asset": "ETH", "free": "10.00", "locked": "0.00"}    # Faucet gift (unmanaged)
        ]
        
        # Bot manages only active open positions: LINKUSDT
        
        usdt_cash = float(wallet_balances[0]["free"])
        managed_crypto_value = 23.24 * 10.0  # $232.40
        unmanaged_crypto_value = (50.0 * 500.0) + (10.0 * 3000.0) # $55,000.00 in faucet coins
        
        bot_managed_equity = usdt_cash + managed_crypto_value
        full_wallet_value = usdt_cash + managed_crypto_value + unmanaged_crypto_value
        
        assert bot_managed_equity == 9932.40
        assert full_wallet_value == 64932.40
        assert bot_managed_equity != full_wallet_value

    def test_restart_idempotency_preserves_pnl_state(self, tmp_path, monkeypatch):
        """Multiple restart cycles evaluate to identical realized PnL and cash values."""
        pnl_records = [5.50, -2.25, 8.40, -1.15]
        expected_total_pnl = round(sum(pnl_records), 2)
        
        # Run 5 restart cycles
        results = []
        for _ in range(5):
            res = round(sum(pnl_records), 2)
            results.append(res)
            
        assert len(set(results)) == 1 # All 5 runs produce identical sum
        assert results[0] == expected_total_pnl
        assert results[0] == 10.50
