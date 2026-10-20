#!/usr/bin/env python3
"""The weekly digest: the board's numbers for the last 7 days and the season, both tracks, as a file.

Why the board's own functions: a digest that computes its numbers differently will one day
disagree with the board, and then neither is believed. Why a new file each time, never an
overwrite: last week's digest is a record of what was known then.

Usage:
    python3 harness/digest.py --write     # writes <data_root>/digests/<date>.md, prints its path
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import tips  # noqa: E402
import tipster_board  # noqa: E402
import tipstercfg  # noqa: E402

ROWS = ("all",) + tipstercfg.MARKETS


def folder() -> pathlib.Path:
    return tipstercfg.state_dir().parent / "digests"


def numbers(now: dt.datetime, days: int | None) -> dict:
    """Scores per track and market for tips settled in the last `days` days (None: all of them)."""
    since = (now - dt.timedelta(days=days)).isoformat() if days else ""
    return {track: tipster_board.scores([t for t in tipster_board.replayed(track)["settled"]
                                         if since <= t["at_settled"] <= now.isoformat()])
            for track in tips.TRACKS}


def _fmt(value, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _table(scored: dict) -> list[str]:
    lines = ["| track | market | n | profit EUR | ROI | mean CLV | CLV > 0 |", "|---|---|---|---|---|---|---|"]
    for track in tips.TRACKS:
        for market in ROWS:
            s = scored[track][market]
            clv = "n/a" if s["clv_mean"] is None else f"{s['clv_mean']:.3f} (n={s['clv_n']})"
            lines.append(f"| {track} | {market} | {s['n']} | {s['profit']:.2f} | {_fmt(s['roi'], '.1%')} | {clv} | "
                         f"{_fmt(s['clv_positive_share'], '.0%')} |")
    return lines


def render(now: dt.datetime) -> str:
    week, season = numbers(now, 7), numbers(now, None)
    wallets = {track: tips.replay(track)["wallet"] for track in tips.TRACKS}
    b = budget.status(now)
    out = [f"# Tipster digest {now:%Y-%m-%d}", "",
           f"Wallets: reviewed EUR {wallets['reviewed']:,.2f}, model EUR {wallets['model']:,.2f}",
           f"Odds requests {b['month']}: {b['used']} of {b['allowance']}", ""]
    for track in tips.TRACKS:
        for banner in tipster_board.banners(season[track]["all"]):
            out.append(f"- {track}: {banner}")
    out += ["", "## Last 7 days", ""] + _table(week) + ["", "## Season", ""] + _table(season) + [""]
    return "\n".join(out)


def write(now: dt.datetime | None = None) -> pathlib.Path:
    now = now or dt.datetime.now(dt.timezone.utc)
    text = render(now)
    folder().mkdir(parents=True, exist_ok=True)
    for n in range(1, 100):
        path = folder() / (f"{now:%Y-%m-%d}.md" if n == 1 else f"{now:%Y-%m-%d}-{n}.md")
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(text)
            return path
        except FileExistsError:
            continue
    raise FileExistsError(f"99 digests on {now:%Y-%m-%d}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write the weekly digest.")
    parser.add_argument("--write", action="store_true", required=True)
    parser.parse_args(argv)
    print(write())
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
