#!/usr/bin/env python3
"""Claude's review of a candidate: keep, halve or veto, with a reason and saved evidence.

Why there is no argument for a probability, an edge or a stake: Claude reads news and can cut a
tip or stop it, never inflate it (spec 5). The stake is computed here from the reviewed wallet;
halving is the only change Claude can make to it. Why evidence must be a saved read inside the
evidence dir: a decision that rests on something nobody can open later is a recollection.
Why expired candidates are refused: the price and the game's state at the last sweep are what
the candidate was computed from; a review after tip-off or after a newer sweep reviews a stale
number.

Usage:
    python3 harness/review.py c-3 --keep  --reason TEXT --evidence PATH [PATH ...]
    python3 harness/review.py c-3 --halve --reason TEXT --evidence PATH [PATH ...]
    python3 harness/review.py c-3 --veto  --reason TEXT --evidence PATH [PATH ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import discover  # noqa: E402
import notify  # noqa: E402
import pricing  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402

CANDIDATE_FIELDS = ("game", "family", "market", "side", "line", "book", "odds", "other_book_odds", "p_win",
                    "p_push", "edge", "kelly", "season", "round", "inputs", "provider_id", "start_utc", "home", "away")


class ReviewError(Exception):
    pass


def _candidate(cid: str, now: dt.datetime) -> dict:
    found = store.read_json(discover.candidates_path(), {}).get("candidates", {}).get(cid)
    if found is None:
        raise ReviewError(f"{cid}: unknown or expired (a newer sweep replaced it)")
    if now >= stats.parse_utc(found["expires"]):
        raise ReviewError(f"{cid}: expired at {found['expires']} (the game has started)")
    return found


def check_evidence(paths: list[str]) -> list[str]:
    root = tipstercfg.evidence_dir().resolve()
    for p in paths:
        path = pathlib.Path(p).resolve()
        if not path.is_relative_to(root):
            raise ReviewError(f"evidence {p} is not under the evidence dir {root}")
        if not path.exists():
            raise ReviewError(f"evidence {p} does not exist")
    return list(paths)


def review(cid: str, decision: str, reason: str, evidence: list[str], now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = tipstercfg.load()
    if not tipstercfg.is_on():
        raise ReviewError("mode off: no tip is recorded or sent")
    halted = tips.halted()
    if halted:
        raise ReviewError(f"halted: {halted}")
    if not reason.strip():
        raise ReviewError("a reason is required")
    cand = _candidate(cid, now)
    paths = check_evidence(evidence)
    if tips.decided(cand["game"], cand["family"], cid):
        raise ReviewError(f"{cid}: {cand['game']} {cand['family']} already reviewed")
    note = {"decision": decision, "reason": reason.strip(), "evidence": paths, "candidate": cid}
    if decision == "veto":
        tips.record_refused({**{k: cand[k] for k in CANDIDATE_FIELDS}, "id": cid, "review": note}, "reviewed",
                            f"veto: {reason.strip()}", now.isoformat())
        return f"OK vetoed {cid} {cand['game']} {cand['family']}"
    wallet = tips.replay("reviewed")["wallet"]
    used, base = tips.round_used("reviewed", cand["season"], cand["round"])
    stake = pricing.stake(cand["kelly"], wallet, used, base if base is not None else wallet, cfg)
    if decision == "halve":
        stake = math.floor(stake / 2)
    if stake <= 0:
        tips.record_refused({**{k: cand[k] for k in CANDIDATE_FIELDS}, "id": cid, "review": note}, "reviewed",
                            "round cap", now.isoformat())
        return f"OK refused {cid}: no stake left in the round cap"
    row = {**{k: cand[k] for k in CANDIDATE_FIELDS}, "track": "reviewed", "at": now.isoformat(),
           "stake": stake, "wallet_before": wallet, "review": note}
    tid = tips.record_tip(row)
    notify.queue_tip({**row, "id": tid})
    return f"OK {'kept' if decision == 'keep' else 'halved'} {tid} stake={stake}"


def main(argv: list[str], now: dt.datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep, halve or veto a candidate, with evidence.")
    parser.add_argument("candidate")
    decision = parser.add_mutually_exclusive_group(required=True)
    decision.add_argument("--keep", action="store_const", dest="decision", const="keep")
    decision.add_argument("--halve", action="store_const", dest="decision", const="halve")
    decision.add_argument("--veto", action="store_const", dest="decision", const="veto")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--evidence", nargs="+", required=True)
    args = parser.parse_args(argv)
    try:
        print(review(args.candidate, args.decision, args.reason, args.evidence, now))
    except (ReviewError, tips.TipError) as e:
        print(f"FAIL: {e}")
        return 1
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
