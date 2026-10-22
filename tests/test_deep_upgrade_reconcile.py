from pathlib import Path

from stratex_upgrade.reconcile import AtomicJsonStore, ExchangeReconciler


class Adapter:
    def fetch_open_orders(self): return []
    def fetch_positions(self): return []


def test_reconcile_missing_remote_order():
    r = ExchangeReconciler(Adapter())
    report = r.reconcile([{"clientOrderId": "x", "status": "SUBMITTED"}], [])
    assert not report.ok
    assert report.issues[0].category == "MISSING_REMOTE_ORDER"


def test_atomic_json_store(tmp_path: Path):
    p = tmp_path / "state.json"
    s = AtomicJsonStore(str(p))
    s.write({"x": 1})
    assert s.read({}) == {"x": 1}
