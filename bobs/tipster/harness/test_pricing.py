#!/usr/bin/env python3
"""pricing: margin removal, expected value with pushes, Kelly, one side and one total per game, stakes.

Run:  python3 harness/test_pricing.py
"""
from __future__ import annotations

import math
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import pricing  # noqa: E402
import testkit  # noqa: E402

CFG = tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))["tipster"]
PARAMS = {"home_adv": 3.0, "k": 8, "sigma_margin": 12.0, "sigma_total": 16.0, "double_adj": 0.0}
GAME = {"key": "E2026-71", "home": "OLY", "away": "MAD", "start_utc": "2026-10-22T17:00:00Z"}
ALL = ["moneyline", "handicap", "total"]


def _books(**books) -> dict:
    return {"provider_id": "p1", "home": "OLY", "away": "MAD", "start_utc": GAME["start_utc"], "books": books}


def _book(ml=None, hc=None, tot=None) -> dict:
    return {"moneyline": ml, "handicap": hc, "total": tot}


def test_power_two_way_worked():
    assert all(abs(p - 0.5) < 1e-9 for p in pricing.fair_power([1.90, 1.90]))
    # q = (1/1.50, 1/2.70) = (0.666667, 0.370370), sum 1.037037. k solves q1^k + q2^k = 1:
    # k = 1.059323, p = (0.650822, 0.349178). Check: ln p1 / ln q1 = ln p2 / ln q2 = k.
    p1, p2 = pricing.fair_power([1.50, 2.70])
    assert abs(p1 - 0.650822) < 1e-6 and abs(p2 - 0.349178) < 1e-6
    assert abs(math.log(p1) / math.log(1 / 1.5) - math.log(p2) / math.log(1 / 2.7)) < 1e-6


def test_power_refuses_bad_odds():
    for bad in ([1.0, 2.0], [0.9, 3.0], [2.0]):
        try:
            pricing.fair_power(bad)
        except pricing.PricingError:
            continue
        raise AssertionError(bad)


def test_ev_with_push():
    assert abs(pricing.ev(0.5, 0.0, 2.1) - 0.05) < 1e-12
    # win 0.48 at 2.10 = 1.008, push 0.04 returns the stake: EV = 1.008 + 0.04 - 1 = 0.048
    assert abs(pricing.ev(0.48, 0.04, 2.10) - 0.048) < 1e-12


def test_kelly_zero_without_edge():
    assert pricing.kelly(0.5, 0.0, 2.0) == 0
    assert pricing.kelly(0.4, 0.0, 2.0) == 0
    # (0.55 x 1.0 - 0.45) / 1.0 = 0.10
    assert abs(pricing.kelly(0.55, 0.0, 2.0) - 0.10) < 1e-12
    # with a push: (0.5 x 1 - (1 - 0.5 - 0.1)) / 1 = 0.10
    assert abs(pricing.kelly(0.5, 0.1, 2.0) - 0.10) < 1e-12


def test_books_compared_on_own_lines():
    exp = {"margin": 8.0, "total": 158.0}
    board = _books(bet365=_book(hc={"line": -5.5, "home": 1.91, "away": 1.91}),
                   stoiximan=_book(hc={"line": -6.5, "home": 2.05, "away": 1.75}))
    cands = pricing.candidates(GAME, exp, PARAMS, board, CFG, ALL)
    side = [c for c in cands if c["family"] == "side"]
    assert len(side) == 1
    c = side[0]
    p365, _ = pricing.model.p_side(exp, PARAMS, "home", -5.5)
    pstx, _ = pricing.model.p_side(exp, PARAMS, "home", -6.5)
    best = "stoiximan" if pstx * 2.05 > p365 * 1.91 else "bet365"
    assert c["book"] == best and c["side"] == "home" and c["market"] == "handicap"
    other = "bet365" if best == "stoiximan" else "stoiximan"
    assert c["other_book_odds"] == {other: {"line": -5.5 if other == "bet365" else -6.5,
                                            "odds": 1.91 if other == "bet365" else 2.05}}
    assert c["line"] == (-6.5 if best == "stoiximan" else -5.5)


def test_one_side_tip_per_game():
    exp = {"margin": 10.0, "total": 158.0}
    board = _books(bet365=_book(ml={"home": 1.60, "away": 2.40}, hc={"line": -5.5, "home": 1.95, "away": 1.85}))
    cands = pricing.candidates(GAME, exp, PARAMS, board, CFG, ALL)
    sides = [c for c in cands if c["family"] == "side"]
    ml_edge = pricing.ev(pricing.model.p_home_win(exp, PARAMS), 0, 1.60)
    hc_edge = pricing.ev(*pricing.model.p_side(exp, PARAMS, "home", -5.5), 1.95)
    assert ml_edge >= 0.03 and hc_edge >= 0.03
    assert len(sides) == 1 and abs(sides[0]["edge"] - max(ml_edge, hc_edge)) < 1e-12


def test_one_total_tip_per_game():
    exp = {"margin": 0.0, "total": 170.0}
    board = _books(bet365=_book(tot={"line": 160.5, "over": 1.90, "under": 1.90}),
                   stoiximan=_book(tot={"line": 161.5, "over": 1.95, "under": 1.85}))
    totals = [c for c in pricing.candidates(GAME, exp, PARAMS, board, CFG, ALL) if c["family"] == "total"]
    assert len(totals) == 1 and totals[0]["side"] == "over"


def test_failed_market_not_offered():
    exp = {"margin": 10.0, "total": 158.0}
    board = _books(bet365=_book(hc={"line": -5.5, "home": 1.95, "away": 1.85}))
    assert [c for c in pricing.candidates(GAME, exp, PARAMS, board, CFG, ["moneyline", "total"])] == []
    assert pricing.candidates(GAME, exp, PARAMS, board, CFG, ALL)


def test_below_min_edge_not_offered_and_sharp_book_never_tipped():
    exp = {"margin": 0.0, "total": 158.0}
    fair = _book(ml={"home": 1.95, "away": 1.95})
    assert pricing.candidates(GAME, exp, PARAMS, _books(bet365=fair), CFG, ALL) == []
    rich = _book(ml={"home": 2.50, "away": 1.60})
    assert pricing.candidates(GAME, exp, PARAMS, _books(pinnacle=rich), CFG, ALL) == []


def test_stake_caps():
    cfg = dict(CFG)
    # Kelly binds: 0.25 x 0.10 x 1000 = 25
    assert pricing.stake(0.10, 1000, 0, 1000, cfg) == 25
    # 10% binds: 0.25 x 0.8 x 1000 = 200 > 100
    assert pricing.stake(0.80, 1000, 0, 1000, cfg) == 100
    # round room binds: 25% of 1000 = 250, 230 used, 20 left
    assert pricing.stake(0.80, 1000, 230, 1000, cfg) == 20


def test_stake_floors_to_whole_euros():
    assert pricing.stake(0.1199, 1000, 0, 1000, CFG) == 29


def test_round_cap_exhausted_gives_zero():
    assert pricing.stake(0.5, 1000, 250, 1000, CFG) == 0
    assert pricing.stake(0.5, 1000, 260, 1000, CFG) == 0
    assert pricing.stake(0.0, 1000, 0, 1000, CFG) == 0


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
