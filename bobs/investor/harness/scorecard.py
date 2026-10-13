#!/usr/bin/env python3
"""The deterministic scorecard: percentile ranks, a weighted score over what is known, a gate.

Why percentiles: revenue growth, a surprise ratio and a Piotroski score live on different scales;
ranking each within the day's universe makes them comparable without hand-tuned scaling.

Why coverage is separate from score: a company with only two of five groups known must not look
strong or weak because of what is missing. The score averages the groups that have data; coverage
says how much of the weight that was, and a card under the floor is "could not tell".

Gate order (the spec's): coverage floor, then the Altman distress veto, then the top-20% rank.

Usage (library; screen.py calls it):
    cards = scorecard.build(raw, cfg["weights"], cfg["min_coverage"])
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import scoutcfg  # noqa: E402

GROUP_SIGNALS = {"growth": ("ttm_growth", "acceleration"), "surprise": ("eps_sue", "revenue_sue"),
                 "momentum": ("momentum_12_1", "high_proximity"), "quality": ("piotroski",),
                 "insider": ("insider",)}
assert tuple(GROUP_SIGNALS) == scoutcfg.GROUPS, "scorecard groups must match [scout.weights]"
SIGNALS = [s for names in GROUP_SIGNALS.values() for s in names]


def percentile_ranks(values: dict[str, float | None]) -> dict[str, float | None]:
    present = sorted((v, k) for k, v in values.items() if v is not None)
    out: dict[str, float | None] = {k: None for k in values}
    n = len(present)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        pct = 50.0 if n == 1 else (i + j) / 2 / (n - 1) * 100
        for k in range(i, j + 1):
            out[present[k][1]] = pct
        i = j + 1
    return out


def build(raw: dict[str, dict[str, dict]], weights: dict[str, float], min_coverage: float) -> dict[str, dict]:
    pcts = {s: percentile_ranks({t: raw[t].get(s, {}).get("value") for t in raw}) for s in SIGNALS}
    cards = {}
    for ticker, signals in raw.items():
        sig = {s: {"value": signals.get(s, {}).get("value"), "pct": pcts[s][ticker],
                   "evidence": signals.get(s, {}).get("evidence", [])} for s in SIGNALS}
        groups = {}
        for group, names in GROUP_SIGNALS.items():
            known = [sig[s]["pct"] for s in names if sig[s]["pct"] is not None]
            groups[group] = sum(known) / len(known) if known else None
        have = {g: w for g, w in weights.items() if groups[g] is not None}
        coverage = round(sum(have.values()), 9)
        score = sum(groups[g] * w for g, w in have.items()) / coverage if coverage > 0 else None
        cards[ticker] = {"score": score, "coverage": coverage, "groups": groups, "signals": sig}
    ranks = percentile_ranks({t: c["score"] for t, c in cards.items() if c["coverage"] >= min_coverage})
    for ticker, card in cards.items():
        card["rank_pct"] = ranks.get(ticker)
    return cards


def gate(card: dict, altman: float | None, cfg: dict) -> tuple[str, str]:
    if round(card["coverage"], 9) < cfg["min_coverage"]:
        return "could not tell", f"coverage {card['coverage']:.0%} below {cfg['min_coverage']:.0%}"
    if altman is not None and altman < cfg["altman_veto"]:
        return "watch", f"Altman Z {altman:.2f} below {cfg['altman_veto']}"
    if card["rank_pct"] is None or card["rank_pct"] < cfg["buy_percentile"]:
        return "pass", f"score rank {card['rank_pct'] or 0:.0f} below {cfg['buy_percentile']}"
    return "eligible", f"score rank {card['rank_pct']:.0f}, coverage {card['coverage']:.0%}"
