#!/usr/bin/env python3
"""budget: the odds allowance is never exceeded, and closing snapshots are protected when it runs low.

Run:  python3 harness/test_budget.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import store  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.timezone.utc)


def _bob(allowance: int, used: int = 0, month: str = "2026-10"):
    testkit.temp_bob({"odds_requests_per_month = 250": f"odds_requests_per_month = {allowance}"})
    if used:
        store.append_jsonl(budget.ledger_path(), [{"at": f"{month}-01T00:00:00+00:00", "month": month,
                                                   "kind": "discovery", "cost": used}])


def _refused(kind: str, pending: int) -> bool:
    try:
        budget.spend(kind, pending, NOW)
    except budget.Refused:
        return True
    return False


def test_budget_refuses_over_allowance():
    _bob(10, used=9)
    assert not _refused("closing", 0)
    assert _refused("closing", 0)
    assert budget.status(NOW) == {"used": 10, "allowance": 10, "remaining": 0, "month": "2026-10"}


def test_budget_keeps_closing_reserve():
    _bob(10, used=9)
    assert _refused("recheck", 2)
    assert _refused("discovery", 2)
    assert not _refused("closing", 2)


def test_discovery_needs_room_for_a_recheck_per_pending_game():
    _bob(10, used=5)
    # 2 pending: reserve 2 closings + 2 rechecks = 4; discovery 1 + 4 = 5 fits exactly
    assert not _refused("discovery", 2)
    assert _refused("discovery", 2)
    assert not _refused("recheck", 2)


def test_budget_month_rolls_over():
    _bob(10, used=10, month="2026-09")
    assert budget.status(NOW)["used"] == 0
    assert not _refused("discovery", 0)


def test_unknown_kind_refused():
    _bob(10)
    try:
        budget.spend("whim", 0, NOW)
    except budget.Refused as e:
        assert "kind" in str(e)
    else:
        raise AssertionError("not refused")


def test_refusal_writes_nothing():
    _bob(1, used=1)
    assert _refused("closing", 0)
    rows, _ = store.read_jsonl(budget.ledger_path())
    assert len(rows) == 1


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
