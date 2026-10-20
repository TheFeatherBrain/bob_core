#!/usr/bin/env python3
"""recheck: after news, one odds read for that game; the update never raises the stake, and a
grown edge after injury news is flagged as the market moving, not as a better bet.

Run:  python3 harness/test_recheck.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import discover  # noqa: E402
import recheck  # noqa: E402
import review  # noqa: E402
import testkit  # noqa: E402
import tips  # noqa: E402
import tipsterworld as w  # noqa: E402

LATER = w.NOW + dt.timedelta(hours=2)
TID = "t-E2026-69-side-reviewed"


def _tipped(home_odds: float, extra: dict | None = None) -> dict:
    """A reviewed PRS moneyline tip at `home_odds` (model P = 0.5987)."""
    w.world(extra=extra)
    w.price_paris(bet365=w.book(ml={"home": home_odds, "away": 1.80}))
    code, lines = discover.run(w.NOW, getter=lambda u, h: "{}", environ=w.KEY, sync=w.no_sync)
    assert code == 0 and any(x.startswith("CANDIDATE c-1") for x in lines), lines
    review.review("c-1", "keep", "full strength", [w.evidence_file()], now=w.NOW)
    return tips.replay("reviewed")["open"][0]


def _recheck(**kwargs):
    return recheck.recheck(TID, "Valencia guard ruled out", [w.evidence_file("news")], now=LATER,
                           getter=lambda u, h: "{}", environ=w.KEY, **kwargs)


def test_tip_carries_provider_id():
    assert _tipped(1.76)["provider_id"] == "p69"


def test_injury_trap_never_raises_stake():
    tip = _tipped(1.76)
    assert abs(tip["edge"] - 0.0537) < 0.001 and tip["stake"] == 17
    w.price_paris(bet365=w.book(ml={"home": 1.87, "away": 1.95}))
    update = _recheck()
    assert update["status"] == "still good" and abs(update["edge_now"] - 0.1196) < 0.001
    assert update["note"] == "the market moved; the model does not see injuries"
    assert update["suggested_stake"] == 17 and update["odds_now"] == 1.87


def test_smaller_edge_is_weaker_with_lower_stake():
    _tipped(1.87)
    w.price_paris(bet365=w.book(ml={"home": 1.76, "away": 1.95}))
    update = _recheck()
    assert update["status"] == "weaker" and update["suggested_stake"] < tips.replay("reviewed")["open"][0]["stake"]


def test_edge_below_min_withdraws():
    _tipped(1.76)
    w.price_paris(bet365=w.book(ml={"home": 1.70, "away": 2.05}))
    update = _recheck()
    assert update["status"] == "withdraw" and update["suggested_stake"] == 0


def test_claude_can_withdraw_a_good_edge():
    _tipped(1.76)
    update = _recheck(withdraw=True)
    assert update["status"] == "withdraw" and update["suggested_stake"] == 0


def test_budget_refused_recheck_uses_claude_flag():
    _tipped(1.76, extra={"odds_requests_per_month = 250": "odds_requests_per_month = 1"})
    update = _recheck()
    assert update["status"] == "still good" and update["note"] == "odds not rechecked: budget"
    assert update["odds_now"] is None and update["edge_now"] is None
    withdrawn = _recheck(withdraw=True)
    assert withdrawn["status"] == "withdraw" and withdrawn["note"] == "odds not rechecked: budget"


def test_no_change_spends_nothing():
    _tipped(1.76)
    used = budget.status(LATER)["used"]
    update = recheck.recheck(TID, "item is about the football club", [w.evidence_file("news")], now=LATER,
                             getter=w.never, environ=w.KEY, no_change=True)
    assert update["status"] == "no change" and budget.status(LATER)["used"] == used


def test_updates_are_lines_not_edits():
    _tipped(1.76)
    before = tips.ledger_path().read_bytes()
    _recheck()
    assert tips.ledger_path().read_bytes().startswith(before)
    assert tips.replay("reviewed")["open"][0]["updates"][0]["status"] == "still good"


def test_unknown_or_started_tip_refused():
    _tipped(1.76)
    for tid, now in (("t-nope", LATER), (TID, dt.datetime(2026, 10, 23, 18, 45, tzinfo=dt.timezone.utc))):
        try:
            recheck.recheck(tid, "x", [w.evidence_file("news")], now=now, getter=w.never, environ=w.KEY)
        except recheck.RecheckError:
            continue
        raise AssertionError(tid)


def test_line_moved_uses_nearest_main_line():
    w.world()
    w.price_paris(bet365=w.book(hc={"line": -1.5, "home": 2.10, "away": 1.75}))
    discover.run(w.NOW, getter=lambda u, h: "{}", environ=w.KEY, sync=w.no_sync)
    review.review("c-1", "keep", "ok", [w.evidence_file()], now=w.NOW)
    w.price_paris(bet365=w.book(hc={"line": -2.5, "home": 2.25, "away": 1.65}))
    update = recheck.recheck("t-E2026-69-side-reviewed", "news", [w.evidence_file("news")], now=LATER,
                             getter=lambda u, h: "{}", environ=w.KEY)
    assert "line moved from -1.5 to -2.5" in update["note"], update


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
