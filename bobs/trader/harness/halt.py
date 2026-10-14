#!/usr/bin/env python3
"""Pull the brake: create state/HALT so no new buys are placed; sells by the exit rules still run.

Why the agent may halt but never resume: stopping is always safe, starting again is a decision.
This CLI only appends to HALT; protect_gate refuses any edit or removal of files under state/, so
only a person deletes it.

Usage:
    python3 harness/halt.py --reason "why"
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import fetch  # noqa: E402
import trade  # noqa: E402


def halt(reason: str, now: dt.datetime) -> pathlib.Path:
    path = trade.halt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{now.isoformat(timespec='seconds')} {reason.strip()}\n")
    return path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reason", required=True)
    a = ap.parse_args(argv)
    print(f"OK HALT at {halt(a.reason, fetch.utcnow())}; a person deletes it to resume buying")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
