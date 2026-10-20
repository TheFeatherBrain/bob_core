#!/usr/bin/env python3
"""oddspapi: the step 0 responses parsed into board rows, main lines only, home-side handicaps,
closing prices from the free history, and one budgeted request per read.

Fixtures in harness/fixtures/odds/ are step 0 responses (docs/step0/FINDINGS.md):
odds-game.json is Paris v Valencia (round 7) read on 2026-10-20; historical-game-pregame.json is
Barcelona v Olympiacos (round 6), pre-game prices only; fixtures-E2026.json is the season list.

Run:  python3 harness/test_oddspapi.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import odds  # noqa: E402
import oddspapi  # noqa: E402
import teams  # noqa: E402
import testkit  # noqa: E402
import tipstercfg  # noqa: E402

NOW = dt.datetime(2026, 10, 21, 8, 0, tzinfo=dt.timezone.utc)
KEY = {"ODDS_API_KEY": "fake-key-123456"}
PARIS = "id1100013873263076"


def test_parse_fixture_game():
    books = oddspapi.parse_books(testkit.fixture("odds/odds-game.json"))
    assert books["bet365"] == {"moneyline": {"home": 2.55, "away": 1.47},
                               "handicap": {"line": 4.5, "home": 1.83, "away": 1.83},
                               "total": {"line": 184.5, "over": 1.83, "under": 1.83}}, books["bet365"]
    assert books["stoiximan"] == {"moneyline": {"home": 2.15, "away": 1.62},
                                  "handicap": {"line": 3.5, "home": 1.85, "away": 1.83},
                                  "total": {"line": 184.5, "over": 1.83, "under": 1.85}}, books["stoiximan"]
    assert "pinnacle" not in books or books["pinnacle"] == {"moneyline": None, "handicap": None, "total": None}


def test_line_is_home_handicap():
    # Paris (home) is the moneyline underdog at 2.55, so it receives points: +4.5, not -4.5
    books = oddspapi.parse_books(testkit.fixture("odds/odds-game.json"))
    assert books["bet365"]["handicap"]["line"] > 0 and books["bet365"]["moneyline"]["home"] > books["bet365"]["moneyline"]["away"]


def test_only_full_game_markets_count():
    # bet365 also lists quarter, half and team totals with mainLine true (e.g. first-half handicap 2.5)
    books = oddspapi.parse_books(testkit.fixture("odds/odds-game.json"))
    assert books["bet365"]["handicap"]["line"] != 2.5 and books["bet365"]["total"]["line"] == 184.5


def test_main_line_flag_wins_over_an_even_alternate():
    # 11474 (handicap 4.5) is flagged main at 1.80 / 2.00; 11470 (3.5) is an alternate at 1.90 / 1.90
    def outcome(price, main):
        return {"players": {"0": {"price": price, "active": True, "mainLine": main}}}
    raw = {"bookmakerOdds": {"bet365": {"markets": {
        "11474": {"outcomes": {"11474": outcome(1.80, True), "11475": outcome(2.00, True)}},
        "11470": {"outcomes": {"11470": outcome(1.90, False), "11471": outcome(1.90, False)}}}}}}
    import json
    books = oddspapi.parse_books(json.dumps(raw))
    assert books["bet365"]["handicap"] == {"line": 4.5, "home": 1.80, "away": 2.00}, books


def test_parse_fixtures():
    fixtures = oddspapi.parse_fixtures(testkit.fixture("odds/fixtures-E2026.json"))
    paris = next(f for f in fixtures if f["provider_id"] == PARIS)
    assert paris == {"provider_id": PARIS, "start_utc": "2026-10-23T18:45:00Z", "home_name": "Paris Basketball",
                     "away_name": "Valencia Basket", "status": 0, "has_odds": True}


def test_provider_names_map_to_clubs():
    fixtures = oddspapi.parse_fixtures(testkit.fixture("odds/fixtures-E2026.json"))
    names = {n for f in fixtures for n in (f["home_name"], f["away_name"])}
    assert [n for n in sorted(names) if teams.code(n) is None] == []
    assert teams.code("BC Olympiakos Piraeus") == "OLY" and teams.code("KK Crvena zvezda Belgrade") == "RED"


def test_closing_from_history():
    # Barcelona v Olympiacos, 16 Oct 18:30 UTC: last prices before the start, on the line closest to even
    books = oddspapi.parse_closing(testkit.fixture("odds/historical-game-pregame.json"), "2026-10-16T18:30:00Z")
    assert books["pinnacle"] == {"moneyline": {"home": 2.28, "away": 1.671},
                                 "handicap": {"line": 3.0, "home": 1.934, "away": 1.917},
                                 "total": {"line": 177.5, "over": 1.934, "under": 1.917}}, books["pinnacle"]
    assert books["stoiximan"]["handicap"] == {"line": 3.5, "home": 1.9, "away": 1.88}
    assert books["stoiximan"]["total"] == {"line": 177.5, "over": 1.87, "under": 1.91}


def test_closing_ignores_prices_after_the_start():
    early = oddspapi.parse_closing(testkit.fixture("odds/historical-game-pregame.json"), "2026-10-14T03:00:00Z")
    assert early["bet365"]["moneyline"] == {"home": 2.4, "away": early["bet365"]["moneyline"]["away"]}


def _world():
    testkit.temp_bob()
    return tipstercfg.load()


def _getter(calls):
    def getter(url, headers):
        calls.append(url)
        if "/fixtures?" in url:
            return testkit.fixture("odds/fixtures-E2026.json")
        if "/odds?" in url:
            return testkit.fixture("odds/odds-game.json")
        raise AssertionError(url)
    return getter


def test_sweep_reads_fixtures_once_then_each_game_in_the_window():
    cfg = _world()
    calls = []
    until = dt.datetime(2026, 10, 22, 0, 0, tzinfo=dt.timezone.utc)
    board = odds.fetch_round("discovery", cfg, environ=KEY, getter=_getter(calls), now=NOW, until=until)
    # one game before midnight on the 22nd: Hapoel v Virtus, 21 Oct 16:00
    assert len(calls) == 2 and "/fixtures?" in calls[0] and "fixtureId=id1100013873263058" in calls[1]
    assert "bookmakers=bet365,stoiximan,pinnacle" in calls[1] and budget.status(NOW)["used"] == 2
    assert [(g["home"], g["away"], g["provider_id"]) for g in board["games"]] == [("HTA", "VIR", "id1100013873263058")]
    assert board["games"][0]["evidence"] and board["stopped"] is None


def test_sweep_stops_at_the_budget_and_keeps_what_it_read():
    _world()
    testkit.temp_bob({"odds_requests_per_month = 250": "odds_requests_per_month = 3"})
    cfg = tipstercfg.load()
    calls = []
    until = dt.datetime(2026, 10, 23, 0, 0, tzinfo=dt.timezone.utc)
    board = odds.fetch_round("discovery", cfg, environ=KEY, getter=_getter(calls), now=NOW, until=until)
    assert len(calls) == 3 and len(board["games"]) == 2 and "needs" in board["stopped"]


def test_game_read_uses_the_cached_fixture_names():
    cfg = _world()
    calls = []
    odds.fetch_round("discovery", cfg, environ=KEY, getter=_getter(calls), now=NOW,
                     until=dt.datetime(2026, 10, 22, 0, 0, tzinfo=dt.timezone.utc))
    board = odds.fetch_game("recheck", PARIS, cfg, environ=KEY, getter=_getter(calls), now=NOW)
    assert [(g["home"], g["away"]) for g in board["games"]] == [("PRS", "PAM")] and calls[-1].count("fixtureId=") == 1


def test_history_read_is_free():
    cfg = _world()
    calls = []

    def getter(url, headers):
        calls.append(url)
        return testkit.fixture("odds/historical-game-pregame.json")
    books = odds.closing_books(cfg, "id1100013873263054", "2026-10-16T18:30:00Z", environ=KEY, getter=getter,
                               sleep=lambda s: None)
    assert books["pinnacle"]["total"]["line"] == 177.5 and budget.status(NOW)["used"] == 0
    assert "historical-odds?fixtureId=id1100013873263054&bookmakers=pinnacle,bet365,stoiximan" in calls[0]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
