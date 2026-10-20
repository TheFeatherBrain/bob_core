#!/usr/bin/env python3
"""Matchup model: expected margin and total from team ratings, then probabilities around them.

Why a normal around the expected margin and total: the calibration check (calibrate.py) fits its
spread on past seasons and tests whether the resulting probabilities hold, band by band, so the
shape is checked rather than assumed. Why the continuity correction on whole-number lines: scores
are whole numbers, so "margin exactly 6" is a band one point wide, and leaving it out would call a
push impossible and overstate the win probability on every whole line.

Why `neutral` zeroes home advantage: the Final Four is played at one venue for all four clubs.
Why the total comes from points for and against (`points`), not from pace x ratings: the product
compounds the noise of three estimates and lost to a plain season average on past seasons, and
points include overtime, which the bookmakers' totals count too (see ratings.shrunk_points).
Why `double` is a pair: a team in its second game of a double-round week may be tired;
`params["double_adj"]` is the margin change for the tired team (0 unless calibration kept it).

Usage (library):
    exp = model.predict(game, lines_before, last_season, params)
    p_win, p_push = model.p_side(exp, params, "home", -5.5)
"""
from __future__ import annotations

import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import ratings  # noqa: E402


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def expect(home: dict, away: dict, league: dict, params: dict, neutral: bool,
           double: tuple[bool, bool] = (False, False), points: tuple[dict, dict] | None = None) -> dict:
    pace = home["pace"] * away["pace"] / league["pace"]
    pts_h = pace * home["ortg"] * away["drtg"] / league["ortg"] / 100
    pts_a = pace * away["ortg"] * home["drtg"] / league["ortg"] / 100
    tired = params.get("double_adj", 0.0) * (int(double[0]) - int(double[1]))
    margin = pts_h - pts_a + (0.0 if neutral else params["home_adv"]) + tired
    if points is not None:
        h, a = points
        total = (h["for"] + a["against"] + a["for"] + h["against"]) / 2
    else:
        total = pts_h + pts_a
    return {"margin": margin, "total": total, "pace": pace}


def _whole(line: float) -> bool:
    return abs(line - round(line)) < 1e-9


def _beat(mean: float, sigma: float, threshold: float) -> tuple[float, float]:
    """P(X > threshold) and P(X lands on it), X ~ N(mean, sigma) read as a whole-number score."""
    if not _whole(threshold):
        return 1 - _cdf((threshold - mean) / sigma), 0.0
    above = 1 - _cdf((threshold + 0.5 - mean) / sigma)
    push = _cdf((threshold + 0.5 - mean) / sigma) - _cdf((threshold - 0.5 - mean) / sigma)
    return above, push


def p_home_win(exp: dict, params: dict) -> float:
    return 1 - _cdf(-exp["margin"] / params["sigma_margin"])


def p_side(exp: dict, params: dict, side: str, line: float) -> tuple[float, float]:
    """Cover with `line` added to that side's margin: home -5.5 wins when the margin exceeds 5.5."""
    mean = exp["margin"] if side == "home" else -exp["margin"]
    return _beat(mean, params["sigma_margin"], -line)


def p_total(exp: dict, params: dict, side: str, line: float) -> tuple[float, float]:
    over, push = _beat(exp["total"], params["sigma_total"], line)
    return (over, push) if side == "over" else (1 - over - push, push)


def predict(game: dict, lines_before: list[dict], last_season: ratings.Season | None, params: dict,
            double: tuple[bool, bool] = (False, False)) -> dict | None:
    home = ratings.ratings_at(game["home"], lines_before, last_season, params["k"])
    away = ratings.ratings_at(game["away"], lines_before, last_season, params["k"])
    league = ratings.Season(lines_before).league() or (last_season.league() if last_season else None)
    if home is None or away is None or league is None:
        return None
    k_total = params.get("k_total", params["k"])
    points = (ratings.points_at(game["home"], lines_before, last_season, k_total),
              ratings.points_at(game["away"], lines_before, last_season, k_total))
    return expect(home, away, league, params, game["neutral"], double, points)
