#!/usr/bin/env python3
"""settle: results from the official score, the stated void rule, and CLV against fair closing odds.

Run:  python3 harness/test_settle.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import settle  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import testkit  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402
import tipsterworld as w  # noqa: E402

UTC = dt.timezone.utc
PARAMS = w.PARAMS


def _game(home_pts=None, away_pts=None, start="2026-10-23T18:45:00Z") -> dict:
    played = home_pts is not None
    return {"key": "E2026-69", "season": "E2026", "code": 69, "round": 7, "phase": "RS", "start_utc": start,
            "home": "PRS", "away": "PAM", "home_pts": home_pts, "away_pts": away_pts, "played": played,
            "neutral": False}


def _tip(market="moneyline", side="home", line=None, odds=2.05, stake=40, track="reviewed") -> dict:
    return {"track": track, "at": "2026-10-21T08:00:00+00:00", "season": "E2026", "round": 7, "game": "E2026-69",
            "start_utc": "2026-10-23T18:45:00Z", "family": "total" if market == "total" else "side",
            "market": market, "side": side, "line": line, "book": "stoiximan", "odds": odds,
            "other_book_odds": {}, "p_win": 0.55, "p_push": 0.0, "edge": 0.1, "kelly": 0.1, "stake": stake,
            "wallet_before": 1000, "inputs": {"margin": 3.0, "total": 160.0}, "provider_id": "p69"}


def test_moneyline_and_total_results():
    assert settle.result(_tip(), _game(80, 75)) == "win"
    assert settle.result(_tip(side="away"), _game(80, 75)) == "loss"
    assert settle.result(_tip("total", "over", 160.5), _game(80, 81)) == "win"
    assert settle.result(_tip("total", "under", 160.5), _game(80, 81)) == "loss"
    assert settle.result(_tip("total", "under", 161.0), _game(80, 81)) == "push"


def test_handicap_push_on_whole_line():
    # home -5 and the home team wins by 5: stake returned
    assert settle.result(_tip("handicap", "home", -5.0), _game(85, 80)) == "push"
    assert settle.result(_tip("handicap", "home", -5.5), _game(85, 80)) == "loss"
    # away +5.5 and the away team loses by 5: covers
    assert settle.result(_tip("handicap", "away", 5.5), _game(85, 80)) == "win"


def test_clv_worked_example():
    # taken 2.05; Pinnacle closes 1.95 / 1.95, fair 0.5 / 0.5: CLV = 2.05 / 2.00 - 1 = 0.025
    books = {"pinnacle": {"moneyline": {"home": 1.95, "away": 1.95}}}
    clv, note = settle.clv(_tip(), books, tipstercfg.load(), PARAMS)
    assert abs(clv - 0.025) < 1e-9 and note == "pinnacle"


def test_clv_consensus_when_no_sharp():
    books = {"bet365": {"moneyline": {"home": 1.90, "away": 1.90}},
             "stoiximan": {"moneyline": {"home": 2.10, "away": 1.75}}, "pinnacle": {"moneyline": None}}
    clv, note = settle.clv(_tip(), books, tipstercfg.load(), PARAMS)
    fair_stx = settle.pricing.fair_power([2.10, 1.75])[0]
    assert note == "consensus" and abs(clv - (2.05 * (0.5 + fair_stx) / 2 - 1)) < 1e-9


def test_clv_line_moved():
    # tip home -5.5; Pinnacle closes home -7.5 at 1.95 / 1.95. Fair P(cover -7.5) = 0.5 means the
    # market's mean margin is 7.5; with sigma 12, P(cover -5.5) = Phi(2 / 12) = 0.566184.
    books = {"pinnacle": {"handicap": {"line": -7.5, "home": 1.95, "away": 1.95}}}
    clv, note = settle.clv(_tip("handicap", "home", -5.5, odds=1.91), books, tipstercfg.load(), PARAMS)
    assert note == "pinnacle; line moved" and abs(clv - (1.91 * 0.566184 - 1)) < 1e-5, clv


def test_missed_closing_gives_could_not_tell():
    assert settle.clv(_tip(), None, tipstercfg.load(), PARAMS) == (None, "could not tell")
    assert settle.clv(_tip(), {"pinnacle": {"handicap": None}}, tipstercfg.load(), PARAMS) == (None, "could not tell")


def _world_with_tip(start="2026-10-23T18:45:00Z", **tip):
    w.world()
    tid = tips.record_tip(dict(_tip(**tip), start_utc=start))
    return tid


def _games_cache(game: dict) -> None:
    cached = stats.cached_games("E2026")
    store.write_json(stats.season_dir("E2026") / "games.json",
                     {"fetched_utc": "2026-10-30T00:00:00+00:00",
                      "games": [game if g["key"] == game["key"] else g for g in cached]})


def test_run_settles_with_pnl_and_clv():
    tid = _world_with_tip(stake=40, odds=2.05)
    tips.append({"kind": "closing", "game": "E2026-69", "at": "x", "evidence": "ev",
                 "books": {"pinnacle": {"moneyline": {"home": 1.95, "away": 1.95}}}})
    _games_cache(_game(80, 75))
    code, lines = settle.run(dt.datetime(2026, 10, 24, 9, 15, tzinfo=UTC), refresh=False)
    assert code == 0 and lines[0].startswith(f"SETTLED {tid} win pnl=42.00 clv=0.025"), lines
    assert tips.replay("reviewed")["wallet"] == 1042


def test_settle_is_idempotent():
    _world_with_tip()
    _games_cache(_game(70, 75))
    now = dt.datetime(2026, 10, 24, 9, 15, tzinfo=UTC)
    settle.run(now, refresh=False)
    code, lines = settle.run(now, refresh=False)
    assert lines == ["OK settled=0 open=0"] and tips.replay("reviewed")["wallet"] == 960
    assert len([r for r in tips.rows()[0] if r.get("kind") == "settled"]) == 1


def test_postponed_game_voids_and_skips_closing():
    # Postponed from 23 Oct to 29 Oct: no snapshot at the old time (test_closing), and 48 hours
    # after the start recorded on the tip, the tip is void: stake returned, never settled later
    # against the replayed game.
    tid = _world_with_tip()
    _games_cache(_game(start="2026-10-29T18:45:00Z"))
    assert settle.run(dt.datetime(2026, 10, 25, 18, 0, tzinfo=UTC), refresh=False)[1] == ["OK settled=0 open=1"]
    code, lines = settle.run(dt.datetime(2026, 10, 25, 18, 45, tzinfo=UTC), refresh=False)
    assert lines[0] == f"SETTLED {tid} void pnl=0.00 clv=could not tell", lines
    _games_cache(_game(90, 60, start="2026-10-29T18:45:00Z"))
    assert settle.run(dt.datetime(2026, 10, 30, 9, 0, tzinfo=UTC), refresh=False)[1] == ["OK settled=0 open=0"]
    assert tips.replay("reviewed")["wallet"] == 1000


def test_replay_after_the_void_window_is_void_even_when_settle_runs_late():
    # Postponed from 23 Oct to 28 Oct and played; settle first runs on the 29th (PC asleep): still void.
    tid = _world_with_tip()
    _games_cache(_game(80, 70, start="2026-10-28T18:45:00Z"))
    code, lines = settle.run(dt.datetime(2026, 10, 29, 9, 0, tzinfo=UTC), refresh=False)
    assert lines[0] == f"SETTLED {tid} void pnl=0.00 clv=could not tell", lines


def test_small_delay_inside_the_window_settles_normally():
    tid = _world_with_tip()
    _games_cache(_game(80, 70, start="2026-10-24T18:45:00Z"))
    code, lines = settle.run(dt.datetime(2026, 10, 25, 9, 0, tzinfo=UTC), refresh=False)
    assert lines[0].startswith(f"SETTLED {tid} win"), lines


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
