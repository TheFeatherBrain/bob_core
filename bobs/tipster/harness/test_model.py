#!/usr/bin/env python3
"""model: expected margin and total from ratings, and win, cover and push probabilities around them.

Run:  python3 harness/test_model.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import model  # noqa: E402
import testkit  # noqa: E402

HOME = {"ortg": 115.0, "drtg": 105.0, "pace": 74.0}
AWAY = {"ortg": 110.0, "drtg": 108.0, "pace": 70.0}
LEAGUE = {"ortg": 110.0, "pace": 72.0, "games": 300}
PARAMS = {"home_adv": 3.0, "k": 8, "sigma_margin": 12.0, "sigma_total": 16.0, "fitted_on": ["E2023"]}


def test_expect_hand_worked():
    # pace = 74 x 70 / 72 = 71.944
    # home = 71.944 x 115 x 108 / 110 / 100 = 81.232
    # away = 71.944 x 110 x 105 / 110 / 100 = 75.542
    # margin = 81.232 - 75.542 + 3 = 8.690; total = 156.773
    exp = model.expect(HOME, AWAY, LEAGUE, PARAMS, neutral=False)
    assert abs(exp["pace"] - 71.944) < 0.01 and abs(exp["margin"] - 8.690) < 0.01 and abs(exp["total"] - 156.773) < 0.01


def test_final_four_has_no_home_advantage():
    home = model.expect(HOME, AWAY, LEAGUE, PARAMS, neutral=False)
    neutral = model.expect(HOME, AWAY, LEAGUE, PARAMS, neutral=True)
    assert abs(home["margin"] - neutral["margin"] - 3.0) < 1e-9 and home["total"] == neutral["total"]


def test_home_win_is_half_at_zero_margin():
    assert abs(model.p_home_win({"margin": 0.0, "total": 150.0}, PARAMS) - 0.5) < 1e-12
    assert model.p_home_win({"margin": 6.0, "total": 150.0}, PARAMS) > 0.5


def test_half_point_line_never_pushes():
    exp = {"margin": 6.0, "total": 160.0}
    for side in ("home", "away"):
        assert model.p_side(exp, PARAMS, side, -5.5 if side == "home" else 5.5)[1] == 0
    assert model.p_total(exp, PARAMS, "over", 160.5)[1] == 0


def test_whole_line_push_probability():
    win, push = model.p_side({"margin": 6.0, "total": 150.0}, PARAMS, "home", -6.0)
    assert abs(push - 0.0332) < 0.0001, push
    assert abs(win - (1 - push) / 2) < 1e-9


def test_win_push_lose_sum_to_one():
    exp = {"margin": 4.3, "total": 158.2}
    for side, line in (("home", -6.0), ("away", 6.0), ("home", 3.5), ("away", -2.0)):
        win, push = model.p_side(exp, PARAMS, side, line)
        lose, _ = model.p_side(exp, PARAMS, "away" if side == "home" else "home", -line)
        assert abs(win + push + lose - 1) < 1e-9, (side, line)
    over, push = model.p_total(exp, PARAMS, "over", 158.0)
    under, push2 = model.p_total(exp, PARAMS, "under", 158.0)
    assert push == push2 > 0 and abs(over + under + push - 1) < 1e-9


def test_over_under_mirror():
    exp = {"margin": 2.0, "total": 157.0}
    over, _ = model.p_total(exp, PARAMS, "over", 160.5)
    under, _ = model.p_total(exp, PARAMS, "under", 160.5)
    assert abs(over + under - 1) < 1e-12 and under > over


def test_away_cover_is_home_mirror():
    exp = {"margin": 5.0, "total": 150.0}
    home, _ = model.p_side(exp, PARAMS, "home", -5.5)
    away, _ = model.p_side(exp, PARAMS, "away", 5.5)
    assert abs(home + away - 1) < 1e-12 and away > home


def test_double_round_adjusts_tired_team():
    params = dict(PARAMS, double_adj=-3.0)
    base = model.expect(HOME, AWAY, LEAGUE, params, neutral=False)
    home_tired = model.expect(HOME, AWAY, LEAGUE, params, neutral=False, double=(True, False))
    away_tired = model.expect(HOME, AWAY, LEAGUE, params, neutral=False, double=(False, True))
    assert abs(home_tired["margin"] - (base["margin"] - 3)) < 1e-9
    assert abs(away_tired["margin"] - (base["margin"] + 3)) < 1e-9
    assert model.expect(HOME, AWAY, LEAGUE, PARAMS, neutral=False, double=(True, False)) == base


def test_predict_none_without_ratings():
    game = {"home": "DUB", "away": "OLY", "neutral": False, "start_utc": "2026-10-01T18:00:00Z"}
    assert model.predict(game, [], None, PARAMS) is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
