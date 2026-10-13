#!/usr/bin/env python3
"""The deterministic scorecard: percentile ranks, a weighted score over what is known, a gate.

Why percentiles: revenue growth, a surprise ratio and a Piotroski score live on different scales;
ranking each within the day's universe makes them comparable without hand-tuned scaling.

Why coverage is separate from score: a company with only two of five groups known must not look
strong or weak because of what is missing. The score averages the groups that have data; coverage
says how much of the weight that was, and a card under the floor is "could not tell".

Why growth and quality rank inside the sector: they differ by industry, so a market-wide rank
rewards the industry, not the company. Momentum, surprise and insider stay market-wide: beating the
market is what a call claims, and SUE is already scaled by each company's own history. A sector
with fewer known values than min_sector_size ranks market-wide; each signal records which.

Gate order: coverage floor, the Altman distress veto, the top-20% rank, then the agreement rule
(at least min_agreeing_groups known groups above the 50th percentile). Agreement comes last so it
only demotes names that would otherwise be eligible.

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
SECTOR_SIGNALS = ("ttm_growth", "acceleration", "piotroski")
MARKET = "market"


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


def peer_ranks(values: dict[str, float | None], sectors: dict[str, str],
               min_size: int) -> tuple[dict[str, float | None], dict[str, str]]:
    market = percentile_ranks(values)
    members: dict[str, dict[str, float | None]] = {}
    for ticker, value in values.items():
        if ticker in sectors:
            members.setdefault(sectors[ticker], {})[ticker] = value
    pcts, peers = dict(market), dict.fromkeys(values, MARKET)
    for name, group in members.items():
        if sum(v is not None for v in group.values()) < min_size:
            continue
        pcts.update(percentile_ranks(group))
        peers.update(dict.fromkeys(group, name))
    return pcts, peers


def build(raw: dict[str, dict[str, dict]], weights: dict[str, float], min_coverage: float,
          sectors: dict[str, str] | None = None, min_sector_size: int = 15) -> dict[str, dict]:
    sectors = sectors or {}
    pcts, peers = {}, {}
    for s in SIGNALS:
        values = {t: raw[t].get(s, {}).get("value") for t in raw}
        if s in SECTOR_SIGNALS:
            pcts[s], peers[s] = peer_ranks(values, sectors, min_sector_size)
        else:
            pcts[s], peers[s] = percentile_ranks(values), dict.fromkeys(raw, MARKET)
    cards = {}
    for ticker, signals in raw.items():
        sig = {s: {"value": signals.get(s, {}).get("value"), "pct": pcts[s][ticker], "peer": peers[s][ticker],
                   "evidence": signals.get(s, {}).get("evidence", [])} for s in SIGNALS}
        groups = {}
        for group, names in GROUP_SIGNALS.items():
            known = [sig[s]["pct"] for s in names if sig[s]["pct"] is not None]
            groups[group] = sum(known) / len(known) if known else None
        have = {g: w for g, w in weights.items() if groups[g] is not None}
        coverage = round(sum(have.values()), 9)
        score = sum(groups[g] * w for g, w in have.items()) / coverage if coverage > 0 else None
        known_groups = [v for v in groups.values() if v is not None]
        cards[ticker] = {"score": score, "coverage": coverage, "groups": groups, "signals": sig,
                         "sector": sectors.get(ticker), "agreeing": sum(v > 50 for v in known_groups),
                         "known_groups": len(known_groups)}
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
    if card["agreeing"] < cfg["min_agreeing_groups"]:
        return "watch", (f"{card['agreeing']} of {card['known_groups']} known groups above median, "
                         f"{cfg['min_agreeing_groups']} needed")
    return "eligible", f"score rank {card['rank_pct']:.0f}, coverage {card['coverage']:.0%}"
