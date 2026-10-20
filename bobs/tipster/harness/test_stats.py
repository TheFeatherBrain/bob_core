#!/usr/bin/env python3
"""stats: the official feed parsed into games and team box lines, cached, never refetched once played.

Fixtures in harness/fixtures/stats/ are raw responses recorded on 2026-10-20 (games-E2024-ff.json is
the E2024 list cut to its Final Four and two playoff games, structure unchanged).

Run:  python3 harness/test_stats.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import stats  # noqa: E402
import testkit  # noqa: E402


def _games(name: str) -> list[dict]:
    return stats.parse_games(testkit.fixture(f"stats/{name}"))


def _game(season_file: str, code: int) -> dict:
    return next(g for g in _games(season_file) if g["code"] == code)


def _raises(url, headers):
    raise AssertionError(f"requested {url}")


def test_games_parse_fixture():
    games = _games("games-E2026.json")
    assert len(games) == 380
    assert all(g["start_utc"].endswith("Z") and g["season"] == "E2026" for g in games)
    played = [g for g in games if g["played"]]
    assert len(played) == 60 and all(isinstance(g["home_pts"], int) and isinstance(g["away_pts"], int) for g in played)
    assert all(g["home_pts"] is None and g["away_pts"] is None for g in games if not g["played"])
    first = _game("games-E2026.json", 2)
    assert (first["key"], first["round"], first["phase"], first["home"], first["away"]) == ("E2026-2", 1, "RS", "DUB", "MAD")
    assert (first["home_pts"], first["away_pts"], first["start_utc"], first["neutral"]) == (78, 77, "2026-09-24T16:00:00Z", False)


def test_final_four_is_neutral():
    games = {g["code"]: g for g in _games("games-E2024-ff.json")}
    assert all(games[c]["neutral"] for c in (327, 328, 329, 330)), "the feed marks only the semifinals"
    assert not games[311]["neutral"] and not games[312]["neutral"]


def test_box_lines_from_fixture():
    game = _game("games-E2026.json", 1)
    home, away = stats.parse_box(testkit.fixture("stats/stats-E2026-1.json"), game)
    assert (home["team"], away["team"]) == (game["home"], game["away"]) == ("RED", "ZAL")
    assert (home["pts"], away["pts"]) == (game["home_pts"], game["away_pts"]) == (77, 83)
    assert home["opp_pts"] == 83 and away["opp_pts"] == 77 and home["home"] and not away["home"]
    assert home["minutes"] == 200 and home["game"] == "E2026-1" and home["opp"] == "ZAL"
    assert all(isinstance(home[k], (int, float)) for k in ("fga", "fta", "oreb", "tov"))


def test_overtime_minutes():
    game = _game("games-E2026.json", 58)
    lines = stats.parse_box(testkit.fixture("stats/stats-E2026-58.json"), game)
    assert [x["minutes"] for x in lines] == [225, 225]
    assert [x["pts"] for x in lines] == [game["home_pts"], game["away_pts"]]


def test_old_format_box_parses():
    game = {"key": "E2015-250", "season": "E2015", "code": 250, "home": "ULK", "away": "CSK",
            "home_pts": 96, "away_pts": 101, "played": True, "neutral": True}
    home, away = stats.parse_box(testkit.fixture("stats/stats-E2015-250.json"), game)
    assert (home["pts"], away["pts"], home["minutes"], home["fga"], home["oreb"]) == (96, 101, 225, 70, 16)
    assert home["neutral"] and away["neutral"]


def test_box_whose_points_disagree_is_refused():
    game = dict(_game("games-E2026.json", 1), home_pts=78)
    try:
        stats.parse_box(testkit.fixture("stats/stats-E2026-1.json"), game)
    except stats.StatsError as e:
        assert "E2026-1" in str(e)
    else:
        raise AssertionError("a box that disagrees with the score was accepted")


def test_finished_game_not_refetched():
    testkit.temp_bob()
    game = _game("games-E2026.json", 1)
    calls = []

    def getter(url, headers):
        calls.append(url)
        return testkit.fixture("stats/stats-E2026-1.json")
    first = stats.box_lines(game, getter=getter)
    second = stats.box_lines(game, getter=_raises)
    assert first == second and len(first) == 2 and len(calls) == 1
    assert calls[0].endswith("/seasons/E2026/games/1/stats")


def test_unplayed_game_has_no_box():
    testkit.temp_bob()
    game = next(g for g in _games("games-E2026.json") if not g["played"])
    assert stats.box_lines(game, getter=_raises) == []


def test_games_cached_for_two_hours_unless_refresh():
    testkit.temp_bob()
    raw = testkit.fixture("stats/games-E2026.json")
    calls = []

    def getter(url, headers):
        calls.append(url)
        return raw
    t0 = stats.parse_utc("2026-10-20T08:00:00Z")
    assert len(stats.games("E2026", getter=getter, now=t0)) == 380
    stats.games("E2026", getter=getter, now=stats.parse_utc("2026-10-20T09:59:00Z"))
    assert len(calls) == 1
    stats.games("E2026", getter=getter, now=stats.parse_utc("2026-10-20T10:01:00Z"))
    stats.games("E2026", getter=getter, now=t0, refresh=True)
    assert len(calls) == 3


def test_season_lines_chronological_from_cache():
    bob = testkit.temp_bob()
    testkit.put(stats.season_dir("E2026") / "box-1.json", testkit.fixture("stats/stats-E2026-1.json"))
    testkit.put(stats.season_dir("E2026") / "box-58.json", testkit.fixture("stats/stats-E2026-58.json"))
    games = [g for g in _games("games-E2026.json") if g["code"] in (1, 58)]
    lines = stats.season_lines_from(games, getter=_raises)
    assert [x["game"] for x in lines] == ["E2026-1", "E2026-1", "E2026-58", "E2026-58"], lines
    assert bob.exists()


def test_limiter_spacing():
    testkit.temp_bob()
    now = [0.0]

    def sleep(s):
        now[0] += s
    limiter = stats.make_limiter(clock=lambda: now[0], sleep=sleep)
    for _ in range(4):
        limiter.wait()
    assert now[0] >= 0.75 - 1e-9, now[0]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
