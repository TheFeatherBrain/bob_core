#!/usr/bin/env python3
"""Turn model probabilities and bookmaker prices into candidate tips and stakes.

Why the power method for margin removal: bookmakers load more margin onto the longshot, and the
power method takes more off the longshot, where plain normalisation would leave the favourite's
fair probability too low (it is used for CLV; the model gives the probability a tip is priced on).
Why each book on its own line: bet365 at -5.5 and Stoiximan at -6.5 are different bets; comparing
their prices without their lines would pick the wrong one. Why one side tip per game: moneyline and
handicap come from the same expected margin, so tipping both stacks one opinion twice.
Why stakes are floored to whole euros: a tip the person can place as written.

Usage (library):
    cands = pricing.candidates(game, exp, params, board_game, cfg, passed_markets)
    euros = pricing.stake(cand["kelly"], wallet, round_used, round_base, cfg)
"""
from __future__ import annotations

import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import model  # noqa: E402

SIDES = {"moneyline": ("home", "away"), "handicap": ("home", "away"), "total": ("over", "under")}
FAMILY = {"moneyline": "side", "handicap": "side", "total": "total"}


class PricingError(Exception):
    pass


def fair_power(odds: list[float]) -> list[float]:
    if len(odds) < 2 or any(o <= 1 for o in odds):
        raise PricingError(f"need two or more decimal odds above 1, got {odds}")
    q = [1 / o for o in odds]
    lo, hi = 0.5, 2.0
    while hi - lo > 1e-9:
        k = (lo + hi) / 2
        if sum(x ** k for x in q) > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    return [x ** k for x in q]


def ev(p_win: float, p_push: float, odds: float) -> float:
    return p_win * odds + p_push - 1


def kelly(p_win: float, p_push: float, odds: float) -> float:
    return max(0.0, (p_win * (odds - 1) - (1 - p_win - p_push)) / (odds - 1))


def probability(exp: dict, params: dict, market: str, side: str, side_line: float | None) -> tuple[float, float]:
    """P(win), P(push) for one side, with the line as that side sees it (away +5.5)."""
    if market == "moneyline":
        home = model.p_home_win(exp, params)
        return (home if side == "home" else 1 - home), 0.0
    if market == "handicap":
        return model.p_side(exp, params, side, side_line)
    return model.p_total(exp, params, side, side_line)


def side_line(market: str, side: str, line: float | None) -> float | None:
    """The line as the tipped side sees it: away +5.5 when the home handicap is -5.5."""
    if market == "handicap" and side == "away":
        return -line
    return line


def offers(board_game: dict, books: list[str], market: str, side: str) -> dict[str, dict]:
    """Per book: that side's price and its line from the side's view. Missing or invalid: absent."""
    out = {}
    for book in books:
        prices = (board_game["books"].get(book) or {}).get(market)
        if prices and isinstance(prices.get(side), (int, float)) and prices[side] > 1:
            out[book] = {"line": side_line(market, side, prices.get("line")), "odds": prices[side]}
    return out


def candidates(game: dict, exp: dict, params: dict, board_game: dict, cfg: dict,
               passed_markets: list[str]) -> list[dict]:
    best: dict[str, dict] = {}
    for market in (m for m in passed_markets if m in cfg["markets"]):
        for side in SIDES[market]:
            offered = offers(board_game, cfg["tip_books"], market, side)
            for book, offer in offered.items():
                p_win, p_push = probability(exp, params, market, side, offer["line"])
                edge = ev(p_win, p_push, offer["odds"])
                if edge < cfg["min_edge"]:
                    continue
                family = FAMILY[market]
                if family in best and best[family]["edge"] >= edge:
                    continue
                best[family] = {"game": game["key"], "family": family, "market": market, "side": side,
                                "line": offer["line"], "book": book, "odds": offer["odds"],
                                "other_book_odds": {b: o for b, o in offered.items() if b != book},
                                "p_win": p_win, "p_push": p_push, "edge": edge,
                                "kelly": kelly(p_win, p_push, offer["odds"])}
    return [best[f] for f in ("side", "total") if f in best]


def stake(kelly_value: float, wallet: float, round_used: float, round_base: float, cfg: dict) -> int:
    room = cfg["max_round_share"] * round_base - round_used
    return max(0, math.floor(min(cfg["kelly_fraction"] * kelly_value * wallet, cfg["max_tip_share"] * wallet, room)))
