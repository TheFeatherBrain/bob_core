#!/usr/bin/env python3
"""The tip ledger: append-only, one line per event, and the two simulated wallets replayed from it.

Why append-only: whether a withdraw alert was right, or a veto saved money, can only be judged
against the tip as it was recorded. Updates, closing snapshots and settlements are their own lines
that reference the tip; nothing edits a tip line. Why one tip per game, family and track: a later
sweep at a better price must not stack a second stake on the same opinion (spec 4.3a); the id makes
a duplicate impossible to record. Why the wallet moves only on settlement: an open tip has not won
or lost anything yet. Why HALT is created by the replay: the reviewed wallet below `halt_below` must
stop new tips even if no person is watching; only a person removes it.

Line kinds:
    tip      id, track ("model" | "reviewed"), at, season, round, game, the candidate fields,
             stake, wallet_before, inputs, review (reviewed track only)
    refused  candidate, track, reason
    update   tip, status, odds_now, edge_now, suggested_stake, reason, evidence, note
    closing  game, books, evidence | missed: true
    settled  tip, result, pnl, clv, clv_note

Usage (library):
    tid = tips.record_tip(row)
    state = tips.replay("reviewed")
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import oscompat  # noqa: E402
import store  # noqa: E402
import tipstercfg  # noqa: E402

TRACKS = ("model", "reviewed")
KINDS = ("tip", "refused", "update", "closing", "settled")


class TipError(Exception):
    pass


class Duplicate(TipError):
    pass


def ledger_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "tips.jsonl"


def halt_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "HALT"


def tip_id(game: str, family: str, track: str) -> str:
    return f"t-{game}-{family}-{track}"


def rows() -> tuple[list[dict], int]:
    return store.read_jsonl(ledger_path())


def record_tip(row: dict) -> str:
    if row.get("track") not in TRACKS:
        raise TipError(f"track must be one of {list(TRACKS)}")
    tid = tip_id(row["game"], row["family"], row["track"])
    lock = ledger_path().with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as fh:
        oscompat.lock(fh)
        try:
            if any(r.get("kind") == "tip" and r.get("id") == tid for r in rows()[0]):
                raise Duplicate(f"{tid} already recorded: no re-tipping a game's {row['family']}")
            store.append_jsonl(ledger_path(), [{**row, "kind": "tip", "id": tid}])
        finally:
            oscompat.unlock(fh)
    return tid


def append(row: dict) -> None:
    kind = row.get("kind")
    if kind not in KINDS or kind == "tip":
        raise TipError(f"append takes one of {[k for k in KINDS if k != 'tip']}; tips go through record_tip")
    store.append_jsonl(ledger_path(), [row])


def record_refused(candidate: dict, track: str, reason: str, at: str) -> None:
    append({"kind": "refused", "at": at, "candidate": candidate, "track": track, "reason": reason})


def decided(game: str, family: str, cid: str | None = None) -> bool:
    """True once the reviewed track holds a tip or a veto for this game and family (or this candidate
    was refused): a veto is a decision on the game, not on one sweep's price (spec 4.3a)."""
    for r in rows()[0]:
        if r.get("track") != "reviewed":
            continue
        if r.get("kind") == "tip" and (r["game"], r["family"]) == (game, family):
            return True
        cand = r.get("candidate", {}) if r.get("kind") == "refused" else {}
        if cand and ((cand.get("game"), cand.get("family")) == (game, family) and r["reason"].startswith("veto")
                     or (cid is not None and cand.get("id") == cid)):
            return True
    return False


def halted() -> str | None:
    try:
        return halt_path().read_text(encoding="utf-8").strip() or "halted"
    except FileNotFoundError:
        return None


def halt(reason: str) -> bool:
    """Create HALT unless it exists; never overwrite the reason a person or an earlier run gave."""
    path = halt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as fh:
            fh.write(reason + "\n")
        return True
    except FileExistsError:
        return False


def replay(track: str) -> dict:
    cfg = tipstercfg.load()
    lines, torn = rows()
    tips = {r["id"]: dict(r, updates=[]) for r in lines if r.get("kind") == "tip" and r.get("track") == track}
    settled: dict[str, dict] = {}
    for r in lines:
        if r.get("kind") == "update" and r.get("tip") in tips:
            tips[r["tip"]]["updates"].append(r)
        elif r.get("kind") == "settled" and r.get("tip") in tips and r["tip"] not in settled:
            settled[r["tip"]] = r
    wallet = cfg["wallet_start"] + sum(s["pnl"] for s in settled.values())
    if track == "reviewed" and wallet < cfg["halt_below"]:
        halt(f"wallet below {cfg['halt_below']}")
    return {"wallet": wallet, "torn": torn,
            "open": [t for tid, t in tips.items() if tid not in settled],
            "settled": [{**t, **{k: v for k, v in settled[tid].items() if k not in ("kind", "tip")}}
                        for tid, t in tips.items() if tid in settled]}


def round_used(track: str, season: str, rnd: int) -> tuple[float, float | None]:
    """Stakes already placed in this round, and the wallet at the round's first tip (None: no tip
    yet, so the current wallet is the base)."""
    in_round = [r for r in rows()[0] if r.get("kind") == "tip" and r.get("track") == track
                and r.get("season") == season and r.get("round") == rnd]
    return sum(r["stake"] for r in in_round), (in_round[0]["wallet_before"] if in_round else None)
