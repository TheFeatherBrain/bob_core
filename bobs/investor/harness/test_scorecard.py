#!/usr/bin/env python3
"""scorecard: missing groups shrink coverage, never the score; the gate follows the spec's order.

Run:  python3 harness/test_scorecard.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import scorecard  # noqa: E402
import testkit  # noqa: E402

WEIGHTS = {"growth": 0.30, "surprise": 0.15, "momentum": 0.20, "quality": 0.20, "insider": 0.15}
CFG = {"min_coverage": 0.70, "altman_veto": 1.8, "buy_percentile": 80, "min_agreeing_groups": 3}
ALL = [s for names in scorecard.GROUP_SIGNALS.values() for s in names]


def _raw(value: float, only: tuple[str, ...] | None = None) -> dict:
    return {s: {"value": value if only is None or s in only else None, "evidence": [f"ev/{s}"]} for s in ALL}


def test_percentiles_with_ties_and_gaps():
    assert scorecard.percentile_ranks({"a": 1, "b": 2, "c": 3, "d": None}) == {"a": 0.0, "b": 50.0, "c": 100.0, "d": None}
    assert scorecard.percentile_ranks({"a": 1, "b": 1, "c": 2}) == {"a": 25.0, "b": 25.0, "c": 100.0}
    assert scorecard.percentile_ranks({"a": 7}) == {"a": 50.0}


def test_full_coverage_scores_and_ranks():
    cards = scorecard.build({"A": _raw(1), "B": _raw(2), "C": _raw(3)}, WEIGHTS, 0.70)
    assert [round(cards[t]["score"], 9) for t in "ABC"] == [0.0, 50.0, 100.0]
    assert all(abs(cards[t]["coverage"] - 1.0) < 1e-9 for t in "ABC")
    assert [cards[t]["rank_pct"] for t in "ABC"] == [0.0, 50.0, 100.0]
    assert cards["C"]["signals"]["insider"]["evidence"] == ["ev/insider"]


def test_missing_groups_shrink_coverage_not_score():
    raw = {"A": _raw(1), "B": _raw(2),
           "C": _raw(3, only=("ttm_growth", "acceleration", "momentum_12_1", "high_proximity"))}
    cards = scorecard.build(raw, WEIGHTS, 0.70)
    assert abs(cards["C"]["coverage"] - 0.5) < 1e-9 and cards["C"]["score"] == 100.0
    # B: growth 50, surprise 100, momentum 50, quality 100, insider 100
    assert abs(cards["B"]["score"] - 75.0) < 1e-9
    assert cards["C"]["rank_pct"] is None and cards["B"]["rank_pct"] == 100.0


def test_gate_order():
    card = {"coverage": 1.0, "rank_pct": 90.0, "score": 70.0, "agreeing": 3, "known_groups": 4}
    assert scorecard.gate(card, 3.0, CFG)[0] == "eligible"
    assert scorecard.gate(card, None, CFG)[0] == "eligible"
    assert scorecard.gate(card, 1.2, CFG) == ("watch", "Altman Z 1.20 below 1.8")
    assert scorecard.gate({**card, "rank_pct": 79.9}, 3.0, CFG)[0] == "pass"
    assert scorecard.gate({**card, "coverage": 0.65, "rank_pct": None}, 3.0, CFG)[0] == "could not tell"


def test_agreement_only_demotes_names_that_would_be_eligible():
    card = {"coverage": 1.0, "rank_pct": 90.0, "score": 70.0, "agreeing": 2, "known_groups": 4}
    assert scorecard.gate(card, 3.0, CFG) == ("watch", "2 of 4 known groups above median, 3 needed")
    assert scorecard.gate({**card, "rank_pct": 10.0, "agreeing": 0}, 3.0, CFG)[0] == "pass"


def test_gate_accepts_coverage_exactly_at_the_floor():
    card = {"coverage": 0.30 + 0.20 + 0.20, "rank_pct": 95.0, "score": 80.0, "agreeing": 3, "known_groups": 3}
    assert scorecard.gate(card, None, CFG)[0] == "eligible"


def test_peer_ranks_inside_big_enough_sectors_only():
    values = {"T1": 1.0, "T2": 2.0, "B1": 10.0, "B2": 20.0}
    sectors = {"T1": "BusEq", "T2": "BusEq", "B1": "Money", "B2": "Money"}
    pcts, peers = scorecard.peer_ranks(values, sectors, 2)
    assert pcts == {"T1": 0.0, "T2": 100.0, "B1": 0.0, "B2": 100.0}
    assert peers == {"T1": "BusEq", "T2": "BusEq", "B1": "Money", "B2": "Money"}
    pcts, peers = scorecard.peer_ranks(values, sectors, 3)
    assert abs(pcts["T2"] - 100 / 3) < 1e-9 and abs(pcts["B1"] - 200 / 3) < 1e-9
    assert set(peers.values()) == {"market"}


def test_sector_size_counts_known_values_only():
    values = {"T1": 1.0, "T2": 2.0, "T3": None, "X": 5.0}
    sectors = {"T1": "BusEq", "T2": "BusEq", "T3": "BusEq", "X": "Money"}
    pcts, peers = scorecard.peer_ranks(values, sectors, 3)
    assert peers["T1"] == "market" and pcts["T2"] == 50.0 and pcts["T3"] is None


def test_a_ticker_without_a_sector_ranks_market_wide():
    values = {"T1": 1.0, "T2": 2.0, "U": 3.0}
    pcts, peers = scorecard.peer_ranks(values, {"T1": "BusEq", "T2": "BusEq"}, 2)
    assert peers == {"T1": "BusEq", "T2": "BusEq", "U": "market"} and pcts["U"] == 100.0


def test_build_ranks_growth_and_quality_by_sector_and_the_rest_by_market():
    raw = {"A": _raw(1), "B": _raw(2), "C": _raw(3)}
    cards = scorecard.build(raw, WEIGHTS, 0.70, {"A": "BusEq", "B": "BusEq", "C": "Money"}, 2)
    assert cards["A"]["signals"]["ttm_growth"]["peer"] == "BusEq"
    assert cards["A"]["signals"]["piotroski"]["peer"] == "BusEq"
    assert cards["A"]["signals"]["momentum_12_1"]["peer"] == "market"
    assert cards["C"]["signals"]["ttm_growth"]["peer"] == "market"
    assert cards["B"]["signals"]["ttm_growth"]["pct"] == 100.0 and cards["B"]["sector"] == "BusEq"


def test_build_counts_agreeing_and_known_groups():
    raw = {"A": _raw(1), "B": _raw(2), "C": _raw(3, only=("ttm_growth", "acceleration", "momentum_12_1",
                                                         "high_proximity", "eps_sue", "revenue_sue"))}
    cards = scorecard.build(raw, WEIGHTS, 0.70)
    assert (cards["A"]["agreeing"], cards["A"]["known_groups"]) == (0, 5)
    assert (cards["C"]["agreeing"], cards["C"]["known_groups"]) == (3, 3)
    assert cards["A"]["sector"] is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
