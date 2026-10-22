class CostEngine:
    """
    Part 4 & 5: Cost Engine and Commission Provider
    Explicitly decouples all components of trading friction.
    """
    def __init__(self, entry_fee=0.00075, exit_fee=0.00075, entry_slip=0.0005, exit_slip=0.0005, spread=0.0):
        # Default parameters represent generic Binance Taker assumptions (0.075% fee, 0.05% slippage)
        self.entry_fee = entry_fee
        self.exit_fee = exit_fee
        self.entry_slip = entry_slip
        self.exit_slip = exit_slip
        self.spread = spread
        
    @classmethod
    def get_binance_taker_config(cls):
        return cls(entry_fee=0.001, exit_fee=0.001, entry_slip=0.0005, exit_slip=0.0005, spread=0.0001)
        
    @classmethod
    def get_binance_maker_config(cls):
        # VIP0 Maker fee is 0.1% on Spot, but on USD-M Futures it's 0.02%. 
        # We will assume Futures VIP0 Maker (0.02%)
        return cls(entry_fee=0.0002, exit_fee=0.0002, entry_slip=0.0, exit_slip=0.0, spread=0.0)

    @classmethod
    def get_futures_maker_config(cls):
        # 8 bps round-trip friction for USD-M Futures with LIMIT_MAKER entry (0.02% maker + 0.04% taker exit + 0.02% slippage)
        return cls(entry_fee=0.0002, exit_fee=0.0004, entry_slip=0.0, exit_slip=0.0002, spread=0.0)
        
    def calculate_net_pnl(self, gross_pnl_pct):
        """
        Calculates Net PnL percentage.
        net PnL = gross PnL - entry fee - exit fee - entry slippage - exit slippage - spread cost
        """
        friction = self.entry_fee + self.exit_fee + self.entry_slip + self.exit_slip + self.spread
        return gross_pnl_pct - friction

    def get_total_friction(self):
        return self.entry_fee + self.exit_fee + self.entry_slip + self.exit_slip + self.spread
        
    def calculate_expectancy(self, win_rate, pt_pct, sl_pct):
        """
        Calculates expectancy with decoupled costs applied to both wins and losses.
        """
        net_win = self.calculate_net_pnl(pt_pct)
        # Loss is negative gross PnL, minus friction (friction always hurts)
        net_loss = -sl_pct - self.get_total_friction()
        
        expectancy = (win_rate * net_win) + ((1 - win_rate) * net_loss)
        return expectancy

    def get_report_dict(self):
        return {
            "entry_fee_bps": self.entry_fee * 10000,
            "exit_fee_bps": self.exit_fee * 10000,
            "entry_slip_bps": self.entry_slip * 10000,
            "exit_slip_bps": self.exit_slip * 10000,
            "spread_bps": self.spread * 10000,
            "total_friction_bps": self.get_total_friction() * 10000
        }
