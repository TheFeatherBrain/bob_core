#!/usr/bin/env python3
"""ratings: possessions, possession-weighted ratings and pace, early-season shrinkage, no future leak.

Run:  python3 harness/test_ratings.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import ratings  # noqa: E402
import stats  # noqa: E402
import testkit  # noqa: E402


def _game1() -> list[dict]:
    game = next(g for g in stats.parse_games(testkit.fixture("stats/games-E2026.json")) if g["code"] == 1)
    assert game["start_utc"] == "2026-09-24T18:00:00Z"
    return stats.parse_box(testkit.fixture("stats/stats-E2026-1.json"), game)


def _pair(key: str, start: str, a: str, b: str, pts_a: int, pts_b: int, poss: float, minutes: int = 200,
          a_home: bool = True) -> list[dict]:
    """Two lines whose possession estimate is exactly `poss` each (fga = poss, the rest 0)."""
    def line(team, opp, pts, opp_pts, home):
        return {"game": key, "start_utc": start, "team": team, "opp": opp, "home": home, "neutral": False,
                "pts": pts, "fga": poss, "fta": 0, "oreb": 0, "tov": 0, "minutes": minutes, "opp_pts": opp_pts}
    return [line(a, b, pts_a, pts_b, a_home), line(b, a, pts_b, pts_a, not a_home)]


def test_possessions_hand_worked():
    # Game E2026-1, Crvena Zvezda (RED) 77 Zalgiris (ZAL) 83, from the recorded box score:
    #   RED: FGA 73 + 0.44 x FTA 19 (8.36) - OREB 15 + TOV 14 = 80.36
    #   ZAL: FGA 63 + 0.44 x FTA 27 (11.88) - OREB 13 + TOV 16 = 77.88
    #   game: (80.36 + 77.88) / 2 = 79.12
    red, zal = _game1()
    assert abs(ratings.possessions(red) - 80.36) < 0.01
    assert abs(ratings.possessions(zal) - 77.88) < 0.01
    assert abs(ratings.game_possessions(red, zal) - 79.12) < 0.01


def test_ratings_are_possession_weighted():
    lines = (_pair("g1", "2026-10-01T18:00:00Z", "AAA", "BBB", 60, 54, 60)
             + _pair("g2", "2026-10-08T18:00:00Z", "AAA", "CCC", 96, 80, 80))
    team = ratings.Season(lines).team("AAA")
    # (60 + 96) / (60 + 80) x 100 = 111.43, not the mean of 100 and 120 (110)
    assert abs(team["ortg"] - 156 / 140 * 100) < 1e-9 and team["games"] == 2
    assert abs(team["drtg"] - 134 / 140 * 100) < 1e-9


def test_pace_per_40_minutes_with_overtime():
    lines = (_pair("g1", "2026-10-01T18:00:00Z", "AAA", "BBB", 80, 70, 70)
             + _pair("g2", "2026-10-08T18:00:00Z", "AAA", "CCC", 90, 88, 90, minutes=225))
    # 160 possessions over 40 + 45 game minutes = 75.29 per 40
    assert abs(ratings.Season(lines).team("AAA")["pace"] - 160 * 40 / 85) < 1e-9
    league = ratings.Season(lines).league()
    assert abs(league["pace"] - 160 * 40 / 85) < 1e-9 and league["games"] == 2


def test_shrink_weights_by_games():
    league = {"ortg": 110.0, "pace": 72.0, "games": 300}
    prior = {"ortg": 120.0, "drtg": 100.0, "pace": 76.0, "games": 34}
    blended_prior = {"ortg": 115.0, "drtg": 105.0, "pace": 74.0}
    zero = ratings.shrunk(None, prior, league, k=8)
    assert all(abs(zero[x] - blended_prior[x]) < 1e-9 for x in blended_prior), zero
    current = {"ortg": 125.0, "drtg": 95.0, "pace": 70.0, "games": 8}
    mid = ratings.shrunk(current, prior, league, k=8)
    assert all(abs(mid[x] - (current[x] + blended_prior[x]) / 2) < 1e-9 for x in blended_prior), mid
    assert ratings.shrunk(current, prior, league, k=0)["ortg"] == 125.0


def test_newcomer_prior_is_league_average():
    last = ratings.Season(_pair("o1", "2025-10-01T18:00:00Z", "OLY", "PAN", 90, 80, 85)
                          + _pair("o2", "2025-10-08T18:00:00Z", "PAN", "OLY", 70, 75, 75))
    league = last.league()
    dub = ratings.ratings_at("DUB", [], last, k=8)
    assert dub == {"ortg": league["ortg"], "drtg": league["ortg"], "pace": league["pace"]}, dub
    oly = ratings.ratings_at("OLY", [], last, k=8)
    assert oly != dub and oly["ortg"] > league["ortg"]


def test_no_rating_at_all_is_none():
    assert ratings.ratings_at("DUB", [], None, k=8) is None


def test_no_future_games_leak():
    past = _pair("g1", "2026-10-01T18:00:00Z", "AAA", "BBB", 80, 70, 70)
    future = _pair("g2", "2026-10-08T18:00:00Z", "AAA", "BBB", 130, 50, 70)
    cutoff = stats.parse_utc("2026-10-08T18:00:00Z")
    assert ratings.Season(past + future, before=cutoff).team("AAA") == ratings.Season(past).team("AAA")
    assert ratings.Season(past + future, before=cutoff).league() == ratings.Season(past).league()
    assert ratings.Season([], before=cutoff).team("AAA") is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
