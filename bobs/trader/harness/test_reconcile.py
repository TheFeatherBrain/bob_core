#!/usr/bin/env python3
"""reconcile: shares exactly, cash to the cent; a torn ledger line is a mismatch, not a crash.

Run:  python3 harness/test_reconcile.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import reconcile  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 30, tzinfo=dt.timezone.utc)


class Broker:
    def __init__(self, positions: dict, cash: float):
        self._positions, self._cash = positions, cash

    def positions(self) -> dict:
        return self._positions

    def account(self) -> dict:
        return {"cash": self._cash}


ROWS = [ledger.line("cash", NOW, "s", amount=1000.0),
        ledger.line("filled", NOW, "s", order_id="o", symbol="AAA", side="buy", qty=3, price=100.0, commission=1.0)]


def test_exact_match_is_clean():
    assert reconcile.check(ROWS, Broker({"AAA": 3.0}, 699.0)) == []


def test_one_share_or_one_cent_off_is_a_mismatch():
    assert reconcile.check(ROWS, Broker({"AAA": 4.0}, 699.0)) == ["AAA: ledger 3 shares, broker 4"]
    assert reconcile.check(ROWS, Broker({"AAA": 3.0}, 699.01)) == ["cash: ledger 699.00, broker 699.01"]
    assert reconcile.check(ROWS, Broker({"AAA": 3.0, "ZZZ": 1.0}, 699.0)) == ["ZZZ: ledger 0 shares, broker 1"]


def test_a_lost_line_shows_as_a_mismatch():
    assert reconcile.check(ROWS[:1], Broker({"AAA": 3.0}, 699.0)) == [
        "AAA: ledger 0 shares, broker 3", "cash: ledger 1000.00, broker 699.00"]


def test_a_split_without_its_split_line_is_a_mismatch_and_with_it_reconciles():
    buy = ledger.line("filled", NOW, "s", order_id="o", symbol="BBB", side="buy", qty=10, price=100.0, commission=1.0)
    rows = [ledger.line("cash", NOW, "s", amount=1000.0), buy]
    assert reconcile.check(rows, Broker({"BBB": 20.0}, -1.0)) == ["BBB: ledger 10 shares, broker 20"]
    split = ledger.line("split", NOW, "t", symbol="BBB", ratio=2.0)
    assert reconcile.check(rows + [split], Broker({"BBB": 20.0}, -1.0)) == []


def test_unresolved_mismatch_blocks_until_corrected():
    mismatch = ledger.line("mismatch", NOW, "s", details=["x"])
    assert reconcile.check(ROWS + [mismatch], Broker({"AAA": 3.0}, 699.0)) == [f"unresolved mismatch {mismatch['id']}"]
    fix = ledger.line("correction", NOW, "s", clears=mismatch["id"], cash_delta=0.0, share_deltas={})
    assert reconcile.check(ROWS + [mismatch, fix], Broker({"AAA": 3.0}, 699.0)) == []


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
