"""
paper_engine/testing_simulators.py

Lightweight, testable versions of PairsSimulator and FundingSimulator
for use in Stage 13 adversarial tests. These are NOT the live simulators.
They track state deterministically without requiring a full portfolio/feed setup.
"""


class PairsSimulator:
    """
    Lightweight pairs trade state tracker for adversarial testing.
    Tracks leg fill status and unhedged state without live market data.
    """

    def __init__(self):
        self._pairs: dict[str, dict] = {}

    def record_leg_fill(
        self,
        pair_id: str,
        leg_name: str,  # "leg_a" or "leg_b"
        symbol: str,
        direction: str,
        price: float,
        quantity: float,
        filled: bool,
    ):
        if pair_id not in self._pairs:
            self._pairs[pair_id] = {"legs": {}, "status": "PENDING"}

        self._pairs[pair_id]["legs"][leg_name] = {
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "quantity": quantity,
            "filled": filled,
        }
        self._update_status(pair_id)

    def _update_status(self, pair_id: str):
        pair = self._pairs[pair_id]
        legs = pair["legs"]
        a = legs.get("leg_a")
        b = legs.get("leg_b")

        if a is None or b is None:
            # Still waiting for the other leg
            pair["status"] = "PENDING"
            return

        if a["filled"] and b["filled"]:
            pair["status"] = "HEDGED"
        elif a["filled"] and not b["filled"]:
            pair["status"] = "LEG_B_FAILED"
        elif not a["filled"] and b["filled"]:
            pair["status"] = "LEG_A_FAILED"
        else:
            pair["status"] = "BOTH_FAILED"

    def get_pair_status(self, pair_id: str) -> str:
        if pair_id not in self._pairs:
            return "UNKNOWN"
        return self._pairs[pair_id]["status"]


class FundingSimulator:
    """
    Lightweight funding arbitrage state tracker for adversarial testing.
    """

    def __init__(self):
        self._arbs: dict[str, dict] = {}
        self._applied_funding_events: dict[str, set[str]] = {}

    def _ensure(self, arb_id: str):
        if arb_id not in self._arbs:
            self._arbs[arb_id] = {
                "spot_filled": False,
                "perp_filled": False,
                "hedged": False,
                "spot_only": False,
                "perp_only": False,
                "total_funding_pnl": 0.0,
            }
            self._applied_funding_events[arb_id] = set()

    def record_spot_fill(self, arb_id: str, symbol: str, price: float, qty: float, filled: bool):
        self._ensure(arb_id)
        self._arbs[arb_id]["spot_filled"] = filled
        self._update_hedge_state(arb_id)

    def record_perp_fill(self, arb_id: str, symbol: str, price: float, qty: float, filled: bool):
        self._ensure(arb_id)
        self._arbs[arb_id]["perp_filled"] = filled
        self._update_hedge_state(arb_id)

    def _update_hedge_state(self, arb_id: str):
        s = self._arbs[arb_id]["spot_filled"]
        p = self._arbs[arb_id]["perp_filled"]
        self._arbs[arb_id]["hedged"] = s and p
        self._arbs[arb_id]["spot_only"] = s and not p
        self._arbs[arb_id]["perp_only"] = p and not s

    def apply_funding_payment(self, arb_id: str, event_id: str, amount: float):
        """Apply funding payment idempotently (same event_id not applied twice)."""
        self._ensure(arb_id)
        if event_id in self._applied_funding_events[arb_id]:
            return  # duplicate — ignore
        self._arbs[arb_id]["total_funding_pnl"] += amount
        self._applied_funding_events[arb_id].add(event_id)

    def get_arb_state(self, arb_id: str) -> dict:
        self._ensure(arb_id)
        return dict(self._arbs[arb_id])
