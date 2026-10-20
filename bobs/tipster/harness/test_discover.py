#!/usr/bin/env python3
"""discover: sweeps only when due, never when off or halted, never a started game, and the model
track recorded for every candidate at its computed stake.

Run:  python3 harness/test_discover.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import discover  # noqa: E402
import stats  # noqa: E402
import testkit  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402
import tipsterworld as w  # noqa: E402


def _run(now=w.NOW, getter=lambda u, h: "{}"):
    return discover.run(now, getter=getter, environ=w.KEY, sync=w.no_sync)


def _games():
    return stats.games("E2026", now=w.NOW, getter=w.never)


def test_mode_off_reads_nothing():
    w.world(mode="off")
    code, lines = discover.run(w.NOW, getter=w.never, environ=w.KEY, sync=lambda *a, **k: 1 / 0)
    assert code == 0 and lines == ["OK mode off"]


def test_halt_records_nothing_new():
    w.world()
    tips.halt("person says stop")
    code, lines = discover.run(w.NOW, getter=w.never, environ=w.KEY, sync=lambda *a, **k: 1 / 0)
    assert code == 0 and lines == ["OK halted: person says stop"]
    assert tips.rows() == ([], 0)


def test_due_early_and_gameday():
    w.world()
    cfg, games = tipstercfg.load(), _games()
    empty = {"early": [], "gameday": []}
    # 08:00 UTC on 21 Oct is 11:00 in Athens; game 61 starts 16:00 UTC the same Athens day.
    assert discover.due(w.NOW, games, cfg, empty) == "gameday"
    assert discover.due(w.NOW, games, cfg, {"early": [], "gameday": ["2026-10-21"]}) == "early"
    assert discover.due(w.NOW, games, cfg, {"early": ["E2026-R7"], "gameday": ["2026-10-21"]}) is None
    # 25 Oct, no games that day; round 8 starts 27 Oct 16:00 UTC, within 48 hours of 26 Oct 18:00
    sunday = dt.datetime(2026, 10, 25, 9, 0, tzinfo=dt.timezone.utc)
    assert discover.due(sunday, games, cfg, empty) is None
    assert discover.due(dt.datetime(2026, 10, 26, 18, 0, tzinfo=dt.timezone.utc), games, cfg, empty) == "early"


def test_gameday_uses_athens_date():
    w.world()
    cfg, games = tipstercfg.load(), _games()
    # 22:30 UTC on 22 Oct is 01:30 on 23 Oct in Athens: game day is the 23rd (games 68 to 70)
    late = dt.datetime(2026, 10, 22, 22, 30, tzinfo=dt.timezone.utc)
    assert discover.due(late, games, cfg, {"early": ["E2026-R7", "E2026-R8"], "gameday": ["2026-10-22"]}) == "gameday"
    assert discover.athens_date(late) == "2026-10-23"


def test_started_game_not_offered():
    w.world()
    w.price(61, "2026-10-21T16:00:00Z", "Hapoel Tel Aviv", "Virtus Bologna", bet365=w.book(ml=w.ML))
    code, lines = _run(now=dt.datetime(2026, 10, 21, 15, 45, tzinfo=dt.timezone.utc))
    assert code == 0 and not [x for x in lines if x.startswith("CANDIDATE")], lines


def test_model_track_recorded_at_discovery():
    w.world()
    w.price_paris()
    code, lines = _run()
    cands = [x for x in lines if x.startswith("CANDIDATE")]
    assert code == 0 and len(cands) == 1, lines
    # margin = home advantage 3, P(PRS) = Phi(3/12) = 0.5987; edge = 0.5987 x 2.00 - 1 = 0.197
    # Kelly = 0.197; stake = floor(0.25 x 0.197 x 1000) = 49
    assert cands[0].startswith("CANDIDATE c-1 E2026-69 side PRS ML @2.00 bet365 edge=0.197 stake=49"), cands[0]
    model = tips.replay("model")["open"]
    assert [(t["id"], t["stake"], t["wallet_before"]) for t in model] == [("t-E2026-69-side-model", 49, 1000)]
    assert model[0]["inputs"]["home_adv"] == 3.0 and model[0]["inputs"]["odds_evidence"]
    assert tips.replay("reviewed")["open"] == []
    assert budget.status(w.NOW)["used"] == 1
    assert lines[-1].startswith("OK sweep=gameday candidates=1")


def test_second_sweep_does_not_retip():
    w.world()
    w.price_paris()
    _run()
    # a game-day sweep also counts as the round's early sweep; the next is the 22nd's game day
    assert _run(now=w.NOW + dt.timedelta(hours=1))[1] == ["OK no sweep due"]
    code, lines = _run(now=w.NOW + dt.timedelta(days=1))
    assert [x for x in lines if x.startswith("CANDIDATE")], lines
    assert len(tips.replay("model")["open"]) == 1
    refused = [r for r in tips.rows()[0] if r.get("kind") == "refused"]
    assert [r["reason"] for r in refused] == ["duplicate"]


def test_sweep_reads_odds_only_to_the_end_of_the_next_round():
    w.world()
    seen = {}

    def fake(kind, cfg, **kwargs):
        seen.update(kwargs)
        return {"games": [], "unmapped": [], "evidence": None, "stopped": "discovery needs 1 of the 0 left"}
    real, discover.odds.fetch_round = discover.odds.fetch_round, fake
    try:
        code, lines = _run()
    finally:
        discover.odds.fetch_round = real
    # round 7's last game is Paris v Valencia, 23 Oct 18:45 UTC
    assert seen["until"] == stats.parse_utc("2026-10-23T18:45:00Z"), seen
    assert "BUDGET stopped: discovery needs 1 of the 0 left" in lines, lines


def test_tip_cites_the_games_own_odds_read():
    w.world()
    w.price_paris()
    _run()
    tip = tips.replay("model")["open"][0]
    assert tip["inputs"]["odds_evidence"] and pathlib.Path(tip["inputs"]["odds_evidence"]).exists()


def test_no_sweep_due_spends_nothing():
    w.world()
    discover.mark_sweep("gameday", w.NOW, _games(), tipstercfg.load())
    discover.mark_sweep("early", w.NOW, _games(), tipstercfg.load())
    code, lines = _run(getter=w.never)
    assert code == 0 and lines == ["OK no sweep due"] and budget.status(w.NOW)["used"] == 0


def test_no_calibration_file_no_tips():
    w.world(calibrated=False)
    code, lines = _run(getter=w.never)
    assert code == 0 and lines == ["OK no market passed calibration; no odds read"], lines
    assert budget.status(w.NOW)["used"] == 0


def test_unmapped_names_reported():
    w.world()
    w.price_paris(home_name="Paris Basketball B")
    code, lines = _run()
    assert "UNMAPPED Paris Basketball B" in lines and not [x for x in lines if x.startswith("CANDIDATE")]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
