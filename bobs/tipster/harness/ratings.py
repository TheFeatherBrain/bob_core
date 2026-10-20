#!/usr/bin/env python3
"""Team ratings from box lines: offence and defence per 100 possessions, pace per 40 minutes.

Why possession-weighted sums, not a mean of per-game ratings: a 60-possession grind and an
80-possession shootout are not equal evidence, and a mean of ratios overweights the slow game.
Why shrinkage: six games are not a season. Current ratings are blended with a prior (half last
season's final numbers, half the league average) by games played, so early-season noise does not
become a confident tip. A club with no previous Euroleague season gets the league average as its
prior: not a zero, not a crash, and never another club's numbers.

Why `before` is a time, not a game count: the calibration walk-forward and live discovery must
both see only games that started before the one being predicted.

Usage (library):
    season = ratings.Season(lines, before=start)
    r = ratings.ratings_at("OLY", lines_before, last_season, k=8)
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import stats  # noqa: E402

KEYS = ("ortg", "drtg", "pace")


def possessions(line: dict) -> float:
    return line["fga"] + 0.44 * line["fta"] - line["oreb"] + line["tov"]


def game_possessions(a: dict, b: dict) -> float:
    return (possessions(a) + possessions(b)) / 2


class Season:
    """Team totals from the lines of games that started before `before` (all lines when None)."""

    def __init__(self, lines: list[dict], before: dt.datetime | None = None):
        self.totals: dict[str, dict] = defaultdict(lambda: {"pts": 0.0, "opp_pts": 0.0, "poss": 0.0,
                                                            "game_minutes": 0.0, "games": 0})
        self.extend([x for x in lines if before is None or stats.parse_utc(x["start_utc"]) < before])

    def extend(self, lines: list[dict]) -> None:
        """Add the games these lines belong to; calibration walks a season forward with it."""
        by_game: dict[str, list[dict]] = defaultdict(list)
        for line in lines:
            by_game[line["game"]].append(line)
        for pair in by_game.values():
            if len(pair) != 2:
                continue
            poss = game_possessions(pair[0], pair[1])
            for line in pair:
                t = self.totals[line["team"]]
                t["pts"] += line["pts"]
                t["opp_pts"] += line["opp_pts"]
                t["poss"] += poss
                t["game_minutes"] += line["minutes"] / 5
                t["games"] += 1

    def team(self, code: str) -> dict | None:
        t = self.totals.get(code)
        if not t or not t["poss"]:
            return None
        return {"ortg": 100 * t["pts"] / t["poss"], "drtg": 100 * t["opp_pts"] / t["poss"],
                "pace": t["poss"] * 40 / t["game_minutes"], "games": t["games"],
                "ppg": t["pts"] / t["games"], "oppg": t["opp_pts"] / t["games"]}

    def league(self) -> dict | None:
        poss = sum(t["poss"] for t in self.totals.values())
        if not poss:
            return None
        team_games = sum(t["games"] for t in self.totals.values())
        return {"ortg": 100 * sum(t["pts"] for t in self.totals.values()) / poss,
                "pace": poss * 40 / sum(t["game_minutes"] for t in self.totals.values()),
                "games": team_games // 2, "ppg": sum(t["pts"] for t in self.totals.values()) / team_games}


def _league_as_team(league: dict) -> dict:
    return {"ortg": league["ortg"], "drtg": league["ortg"], "pace": league["pace"]}


def shrunk(current: dict | None, prior: dict | None, league: dict, k: float) -> dict:
    base = _league_as_team(league)
    blended = base if prior is None else {x: (prior[x] + base[x]) / 2 for x in KEYS}
    n = current["games"] if current else 0
    if n + k == 0:
        return blended
    return {x: (n * (current[x] if current else 0) + k * blended[x]) / (n + k) for x in KEYS}


def ratings_at(code: str, lines_before: list[dict], last_season: Season | None, k: float) -> dict | None:
    season = Season(lines_before)
    current = season.team(code)
    league = (last_season.league() if last_season else None) or season.league()
    if league is None:
        return None
    prior = last_season.team(code) if last_season else None
    return shrunk(current, prior, league, k)
