#!/usr/bin/env python3
"""closing: one snapshot per tipped game, 30 minutes before its real (UTC) start, none for a game
that moved, and a missed snapshot recorded as missed.

Run:  python3 harness/test_closing.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import closing  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import testkit  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402
import tipsterworld as w  # noqa: E402

UTC = dt.timezone.utc


def _game(key: str, start: str, played=False) -> dict:
    return {"key": key, "season": "E2026", "code": int(key.split("-")[1]), "round": 8, "phase": "RS",
            "start_utc": start, "home": "PRS", "away": "PAM", "home_pts": None, "away_pts": None,
            "played": played, "neutral": False}


def _tip(game: str, start: str, track="reviewed") -> dict:
    return {"id": f"t-{game}-side-{track}", "game": game, "start_utc": start, "track": track, "provider_id": "p69"}


def test_closing_window():
    w.world()
    cfg = tipstercfg.load()
    start = "2026-10-23T18:45:00Z"
    games, open_tips = [_game("E2026-69", start)], [_tip("E2026-69", start)]
    at = stats.parse_utc(start)
    assert closing.due(at - dt.timedelta(minutes=29), open_tips, games, cfg) == ["E2026-69"]
    assert closing.due(at - dt.timedelta(minutes=31), open_tips, games, cfg) == []
    assert closing.due(at, open_tips, games, cfg) == []


def test_closing_window_across_dst():
    # Athens leaves summer time at 01:00 UTC on 25 Oct 2026 (UTC+3 to UTC+2). A game at 19:00 UTC
    # that evening (21:00 Athens) is snapped from 18:30 UTC: the clock change moves nothing.
    w.world()
    cfg = tipstercfg.load()
    start = "2026-10-25T19:00:00Z"
    games, open_tips = [_game("E2026-90", start)], [_tip("E2026-90", start)]
    assert closing.due(dt.datetime(2026, 10, 25, 18, 29, tzinfo=UTC), open_tips, games, cfg) == []
    assert closing.due(dt.datetime(2026, 10, 25, 18, 30, tzinfo=UTC), open_tips, games, cfg) == ["E2026-90"]
    assert closing.due(dt.datetime(2026, 10, 25, 18, 59, tzinfo=UTC), open_tips, games, cfg) == ["E2026-90"]


def test_postponed_game_skips_closing():
    w.world()
    cfg = tipstercfg.load()
    old, new = "2026-10-23T18:45:00Z", "2026-10-29T18:45:00Z"
    open_tips, games = [_tip("E2026-69", old)], [_game("E2026-69", new)]
    assert closing.due(dt.datetime(2026, 10, 23, 18, 30, tzinfo=UTC), open_tips, games, cfg) == []
    assert closing.missed(dt.datetime(2026, 10, 23, 20, 0, tzinfo=UTC), open_tips, games) == []


def test_already_snapped_not_due_and_one_snapshot_per_game():
    w.world()
    cfg = tipstercfg.load()
    start = "2026-10-23T18:45:00Z"
    games = [_game("E2026-69", start)]
    open_tips = [_tip("E2026-69", start), _tip("E2026-69", start, "model")]
    at = stats.parse_utc(start) - dt.timedelta(minutes=10)
    assert closing.due(at, open_tips, games, cfg) == ["E2026-69"]
    tips.append({"kind": "closing", "game": "E2026-69", "at": at.isoformat(), "books": {}, "evidence": "ev"})
    assert closing.due(at, open_tips, games, cfg) == []


def _tipped_world():
    w.world()
    w.price_paris(bet365=w.book(ml={"home": 2.0, "away": 1.8}), pinnacle=w.book(ml={"home": 1.9, "away": 1.95}))
    for track in ("model", "reviewed"):
        tips.record_tip({"track": track, "at": w.NOW.isoformat(), "season": "E2026", "round": 7, "game": "E2026-69",
                         "start_utc": "2026-10-23T18:45:00Z", "family": "side", "market": "moneyline", "side": "home",
                         "line": None, "book": "bet365", "odds": 2.0, "other_book_odds": {}, "p_win": 0.6,
                         "p_push": 0.0, "edge": 0.2, "kelly": 0.2, "stake": 40, "wallet_before": 1000, "inputs": {},
                         "provider_id": "p69"})


def test_run_snaps_once_and_spends_one_closing():
    _tipped_world()
    at = dt.datetime(2026, 10, 23, 18, 20, tzinfo=UTC)
    code, lines = closing.run(at, getter=lambda u, h: "{}", environ=w.KEY)
    assert code == 0 and lines[0].startswith("SNAPPED E2026-69"), lines
    snaps = [r for r in tips.rows()[0] if r.get("kind") == "closing"]
    assert len(snaps) == 1 and snaps[0]["books"]["pinnacle"]["moneyline"] == {"home": 1.9, "away": 1.95}
    assert budget.status(at)["used"] == 1
    assert closing.run(at + dt.timedelta(minutes=10), getter=w.never, environ=w.KEY)[1] == ["OK snapped=0 missed=0"]


def test_started_without_snapshot_is_missed():
    _tipped_world()
    after = dt.datetime(2026, 10, 23, 19, 0, tzinfo=UTC)
    code, lines = closing.run(after, getter=w.never, environ=w.KEY)
    assert "MISSED E2026-69" in lines
    missed = [r for r in tips.rows()[0] if r.get("kind") == "closing"]
    assert missed == [{"kind": "closing", "game": "E2026-69", "at": after.isoformat(), "missed": True}]


def test_pending_closing_leaves_out_snapped_games():
    import discover
    _tipped_world()
    games = stats.cached_games("E2026")
    assert discover.pending_closing(w.NOW, games) == 1
    tips.append({"kind": "closing", "game": "E2026-69", "at": "x", "books": {}, "evidence": "ev"})
    assert discover.pending_closing(w.NOW, games) == 0


def test_odds_round_cli_keeps_the_closing_reserve():
    import odds
    _tipped_world()
    seen = {}

    def fake(kind, cfg, **kwargs):
        seen.update(kwargs, kind=kind)
        return {"games": [], "unmapped": []}
    real, odds.fetch_round = odds.fetch_round, fake
    try:
        odds.main(["--round"], now=w.NOW)
    finally:
        odds.fetch_round = real
    assert seen["kind"] == "discovery" and seen["pending_closing"] == 1, seen


def test_history_mode_skips_snapshots():
    w.world(extra={"closing_from_history = false": "closing_from_history = true"})
    code, lines = closing.run(w.NOW, getter=w.never, environ=w.KEY)
    assert code == 0 and lines == ["OK closing_from_history: no snapshots taken"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
