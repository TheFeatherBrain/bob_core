#!/usr/bin/env python3
"""Closing snapshot: the tipped game's odds once, in the last `closing_minutes` before tip-off.

Why a snapshot: closing line value (odds taken against the fair closing price) is the early
signal of an edge; profit needs ten times the tips to mean anything (spec 1). Why only the
current UTC start time decides: the clock change (Athens moves from UTC+3 to UTC+2 on 25 Oct)
must not move a snapshot, and a postponed game must not be snapped at its old time. Why a start
without a snapshot is written as missed: "no closing line" must be visible as a gap, so its CLV
reads "could not tell" and not zero. One snapshot per game serves both tracks.

Usage:
    python3 harness/closing.py --due     # SNAPPED / MISSED lines, then OK snapped=N missed=M
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import discover  # noqa: E402
import fetch  # noqa: E402
import odds  # noqa: E402
import stats  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402


def _snapped() -> set[str]:
    return {r["game"] for r in tips.rows()[0] if r.get("kind") == "closing"}


def _open_tips() -> list[dict]:
    return [t for track in tips.TRACKS for t in tips.replay(track)["open"]]


def due(now: dt.datetime, open_tips: list[dict], games: list[dict], cfg: dict) -> list[str]:
    starts = {g["key"]: stats.parse_utc(g["start_utc"]) for g in games if not g["played"]}
    window = dt.timedelta(minutes=cfg["closing_minutes"])
    done = _snapped()
    keys = {t["game"] for t in open_tips}
    return sorted(k for k in keys if k in starts and k not in done and now < starts[k] <= now + window)


def missed(now: dt.datetime, open_tips: list[dict], games: list[dict]) -> list[str]:
    starts = {g["key"]: stats.parse_utc(g["start_utc"]) for g in games}
    done = _snapped()
    return sorted({t["game"] for t in open_tips if t["game"] in starts and t["game"] not in done
                   and starts[t["game"]] <= now})


def run(now: dt.datetime | None = None, *, getter=fetch.get, environ=os.environ) -> tuple[int, list[str]]:
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = tipstercfg.load()
    if not tipstercfg.is_on():
        return 0, ["OK mode off"]
    if cfg["closing_from_history"]:
        return 0, ["OK closing_from_history: no snapshots taken"]
    open_tips = _open_tips()
    if not open_tips:
        return 0, ["OK snapped=0 missed=0"]
    games = stats.games(discover.season_of(now), now=now)
    out, snapped = [], 0
    for key in due(now, open_tips, games, cfg):
        tip = next(t for t in open_tips if t["game"] == key)
        try:
            board = odds.fetch_game("closing", tip["provider_id"], cfg, environ=environ, getter=getter, now=now)
        except (odds.OddsError, budget.Refused) as e:
            out.append(f"FAILED {key}: {e}")
            continue
        game = next(g for g in games if g["key"] == key)
        board_game = next((b for b in board["games"] if (b["home"], b["away"]) == (game["home"], game["away"])), None)
        if board_game is None:
            out.append(f"FAILED {key}: not in the provider's answer")
            continue
        tips.append({"kind": "closing", "game": key, "at": now.isoformat(), "books": board_game["books"],
                     "evidence": board["evidence"]})
        snapped += 1
        out.append(f"SNAPPED {key} [ev:{board['evidence']}]")
    gone = missed(now, open_tips, games)
    for key in gone:
        tips.append({"kind": "closing", "game": key, "at": now.isoformat(), "missed": True})
        out.append(f"MISSED {key}")
    out.append(f"OK snapped={snapped} missed={len(gone)}")
    return 0, out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Closing snapshots for tipped games about to start.")
    parser.add_argument("--due", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        code, lines = run()
    except (stats.StatsError, fetch.FetchError) as e:
        code, lines = 1, [f"FAIL: {type(e).__name__}: {e}"]
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
