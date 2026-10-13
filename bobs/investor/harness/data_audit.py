#!/usr/bin/env python3
"""What this Bob fetches but never uses, and how much of the universe each signal covers.

Why: the scanner this design borrowed ideas from read 16 of 117 metrics it paid for, and nobody had
looked. A fetch nobody reads costs SEC requests and evidence space every night, and hides that a
signal someone believes is in the score is not. test_data_audit.py fails on a dead fetch, so it is
found when it is made. Coverage is reported, never failed on: low coverage is a fact about the
data, not a fault in the code.

Usage:
    python3 harness/data_audit.py --report    # OK or FAIL, then the three lists
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
import macro  # noqa: E402
import scorecard  # noqa: E402
import screen  # noqa: E402
import xbrl  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
READERS = ("xbrl.py", "signals.py", "screen.py")


def _text(paths) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


def unused_concepts() -> list[str]:
    """Concepts edgar fetches that nothing reads. A concept mapped into the fundamentals dataclasses
    counts as read only when a fundamentals module reads that field; any other concept, when a
    reader names it."""
    fetched = dict.fromkeys(edgar.QUARTERLY + edgar.RECENT_QUARTERLY + edgar.ANNUAL + edgar.INSTANT)
    mapped = {**xbrl.FIELDS, **xbrl.INSTANT_FIELDS}
    readers = _text(HERE / f for f in READERS)
    fundamentals = _text(sorted((HERE / "fundamentals").glob("*.py")))
    out = []
    for name in fetched:
        if name in mapped:
            used = re.search(rf"\.{re.escape(mapped[name])}\b", fundamentals) is not None
        else:
            used = f'"{name}"' in readers
        if not used:
            out.append(name)
    return out


def unshown_series() -> list[str]:
    sample = {"series": {s: {"what": what, "value": 1.0, "observed": "2026-01-01", "stale": False,
                             "evidence": None, "reason": None} for s, (what, _) in macro.SERIES.items()}}
    text = "\n".join(macro.lines(sample))
    return [s for s in macro.SERIES if s not in text]


def signal_coverage(doc: dict) -> dict[str, float | None]:
    cards = doc.get("cards", {})
    return {s: (sum(c["signals"][s]["value"] is not None for c in cards.values()) / len(cards) if cards else None)
            for s in scorecard.SIGNALS}


def report_lines() -> tuple[bool, list[str]]:
    dead, unshown = unused_concepts(), unshown_series()
    cover = signal_coverage(screen.latest())
    known = [f"{s} {v:.0%}" for s, v in cover.items() if v is not None]
    return not (dead or unshown), [f"fetched but never read: {', '.join(dead) or 'none'}",
                                   f"FRED series not shown: {', '.join(unshown) or 'none'}",
                                   "signal coverage in the latest screen: " + (", ".join(known) or "no screen")]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", action="store_true", required=True)
    ap.parse_args(argv)
    ok, lines = report_lines()
    print("OK" if ok else "FAIL: data fetched but never read, or a series shown nowhere")
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
