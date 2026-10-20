#!/usr/bin/env python3
"""Recheck a tipped game after material news: one odds read, then an update the person sees.

Why an update never raises the stake: the team model does not know who is injured. When a key
player is ruled out, the market moves against his team and the model's edge appears to grow; that
is the market seeing what the model cannot, not a better bet (spec 6, "a trap"). So the suggested
stake is at most the original, and a grown edge is labelled as the market moving.
Why the tip line is never changed: updates are their own ledger lines, so it can be checked later
whether withdraw alerts were right. Why `--no-change` spends nothing: news that is not material
(the football club, a player not in the rotation) must still be recorded as read, but costs no
odds request and sends nothing.

Usage:
    python3 harness/recheck.py TIP --reason TEXT --evidence PATH [PATH ...] [--withdraw]
    python3 harness/recheck.py TIP --no-change --reason TEXT --evidence PATH [PATH ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import calibrate  # noqa: E402
import discover  # noqa: E402
import fetch  # noqa: E402
import odds  # noqa: E402
import pricing  # noqa: E402
import review  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402

TRAP_NOTE = "the market moved; the model does not see injuries"
BUDGET_NOTE = "odds not rechecked: budget"


class RecheckError(Exception):
    pass


def queue_message(update: dict, tip: dict) -> None:
    """Outbox stub until plan Task 13."""


def _open_tip(tid: str, now: dt.datetime) -> tuple[dict, dict]:
    tip = next((t for t in tips.replay("reviewed")["open"] if t["id"] == tid), None)
    if tip is None:
        raise RecheckError(f"{tid}: not an open reviewed tip")
    game = next((g for g in stats.cached_games(tip["season"]) if g["key"] == tip["game"]), None)
    if game is None or stats.parse_utc(game["start_utc"]) <= now:
        raise RecheckError(f"{tid}: the game has started or is unknown; watching stopped at tip-off")
    return tip, game


def _best_now(tip: dict, board_game: dict, cfg: dict, params: dict) -> tuple[dict | None, str | None]:
    """The better of the tip books on the tip's market and line; when that line is gone, on the
    nearest main line, with a note. The model's expectation is the one recorded on the tip."""
    offered = [{"book": b, **o} for b, o in pricing.offers(board_game, cfg["tip_books"], tip["market"], tip["side"]).items()]
    if not offered:
        return None, None
    note = None
    if tip["market"] != "moneyline":
        nearest = min(offered, key=lambda o: abs(o["line"] - tip["line"]))["line"]
        if nearest != tip["line"]:
            note = f"line moved from {tip['line']:g} to {nearest:g}"
        offered = [o for o in offered if o["line"] == nearest]
    exp = {"margin": tip["inputs"]["margin"], "total": tip["inputs"]["total"]}
    best = None
    for o in offered:
        p_win, p_push = pricing.probability(exp, params, tip["market"], tip["side"], o["line"])
        edge = pricing.ev(p_win, p_push, o["odds"])
        if best is None or edge > best["edge"]:
            best = {**o, "p_win": p_win, "p_push": p_push, "edge": edge, "kelly": pricing.kelly(p_win, p_push, o["odds"])}
    return best, note


def _suggested(tip: dict, kelly: float, cfg: dict) -> int:
    state = tips.replay("reviewed")
    used, base = tips.round_used("reviewed", tip["season"], tip["round"])
    room_used = used - tip["stake"]
    return min(tip["stake"], pricing.stake(kelly, state["wallet"], room_used,
                                           base if base is not None else state["wallet"], cfg))


def _pending(now: dt.datetime, season: str) -> int:
    return discover.pending_closing(now, stats.cached_games(season))


def recheck(tid: str, reason: str, evidence: list[str], *, now: dt.datetime | None = None, getter=fetch.get,
            environ=os.environ, withdraw: bool = False, no_change: bool = False) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = tipstercfg.load()
    tip, game = _open_tip(tid, now)
    try:
        paths = review.check_evidence(evidence)
    except review.ReviewError as e:
        raise RecheckError(str(e)) from None
    row = {"kind": "update", "tip": tid, "at": now.isoformat(), "reason": reason.strip(), "evidence": paths,
           "odds_now": None, "edge_now": None, "book_now": None, "note": None}
    if no_change:
        update = {**row, "status": "no change", "suggested_stake": tip["stake"]}
        tips.append(update)
        return update
    try:
        board = odds.fetch_game("recheck", tip["provider_id"], cfg, environ=environ, getter=getter, now=now,
                                pending_closing=_pending(now, tip["season"]))
    except budget.Refused:
        update = {**row, "status": "withdraw" if withdraw else "still good", "note": BUDGET_NOTE,
                  "suggested_stake": 0 if withdraw else tip["stake"]}
        tips.append(update)
        queue_message(update, tip)
        return update
    except odds.OddsError as e:
        raise RecheckError(str(e)) from None
    board_game = next((b for b in board["games"] if (b["home"], b["away"]) == (game["home"], game["away"])), None)
    params = store.read_json(calibrate.params_path(), None)
    best, note = _best_now(tip, board_game, cfg, params) if board_game and params else (None, None)
    if best is None:
        update = {**row, "status": "withdraw" if withdraw else "still good",
                  "note": "could not tell: the market is not offered now", "suggested_stake": 0 if withdraw else tip["stake"]}
    else:
        if withdraw or best["edge"] < cfg["min_edge"]:
            status, stake = "withdraw", 0
        elif best["edge"] < tip["edge"]:
            status, stake = "weaker", _suggested(tip, best["kelly"], cfg)
        else:
            status, stake = "still good", tip["stake"]
            if best["edge"] > tip["edge"]:
                note = "; ".join(n for n in (TRAP_NOTE, note) if n)
        update = {**row, "status": status, "odds_now": best["odds"], "edge_now": best["edge"], "book_now": best["book"],
                  "line_now": best["line"], "note": note, "suggested_stake": stake}
    update["evidence"] = paths + [board["evidence"]]
    tips.append(update)
    queue_message(update, tip)
    return update


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Recheck a tipped game after news.")
    parser.add_argument("tip")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--evidence", nargs="+", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--withdraw", action="store_true")
    mode.add_argument("--no-change", action="store_true")
    args = parser.parse_args(argv)
    try:
        u = recheck(args.tip, args.reason, args.evidence, withdraw=args.withdraw, no_change=args.no_change)
    except RecheckError as e:
        print(f"FAIL: {e}")
        return 1
    edge = "n/a" if u["edge_now"] is None else f"{u['edge_now']:.3f}"
    print(f"OK update {args.tip} {u['status']} edge_now={edge} suggested_stake={u['suggested_stake']}"
          + (f" note={u['note']}" if u["note"] else ""))
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
