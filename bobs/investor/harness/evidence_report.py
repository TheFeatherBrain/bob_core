#!/usr/bin/env python3
"""How much evidence this Bob keeps, what still cites it, and what could be pruned. Never deletes.

Every read is saved, so evidence grows every night (Form 4s, frames, price pages). Deleting is
irreversible and a thesis or ledger row may cite a folder, so this tool only reports: totals by
month, how many folders are cited (by a thesis, or by any state file such as the price or frame
caches), and which uncited folders are older than the cut-off. A person decides what to remove.

Usage:
    python3 harness/evidence_report.py [--older-than 180]
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import fetch  # noqa: E402
import scoutcfg  # noqa: E402

REF = re.compile(r"(\d{4}-\d{2}-\d{2})[\\/]+([a-z0-9][a-z0-9._-]{0,63})")
SHOWN = 20


def _cited() -> set[str]:
    texts = [p for p in (scoutcfg.bob_dir() / "theses").glob("*.md")]
    texts += [p for p in scoutcfg.state_dir().rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl")]
    found: set[str] = set()
    for path in texts:
        found.update(f"{d}/{i}" for d, i in REF.findall(path.read_text(encoding="utf-8", errors="replace")))
    return found


def build(today: dt.date, older_than_days: int) -> dict:
    root = scoutcfg.evidence_dir()
    folders = sorted(p for p in root.glob("????-??-??/*") if p.is_dir())
    cited = _cited()
    cutoff = (today - dt.timedelta(days=older_than_days)).isoformat()
    months: dict[str, int] = {}
    total = 0
    for folder in folders:
        months[folder.parent.name[:7]] = months.get(folder.parent.name[:7], 0) + 1
        total += sum(f.stat().st_size for f in folder.iterdir() if f.is_file())
    rels = [f"{f.parent.name}/{f.name}" for f in folders]
    return {"folders": len(folders), "bytes": total, "months": months,
            "cited": sum(r in cited for r in rels),
            "candidates": [r for r in rels if r not in cited and r[:10] < cutoff]}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--older-than", type=int, default=180, help="days; older uncited folders are listed")
    a = ap.parse_args(argv)
    report = build(fetch.utcnow().date(), a.older_than)
    print(f"OK evidence folders={report['folders']} size={report['bytes'] / 1e6:.1f} MB cited={report['cited']} "
          f"uncited_older_than_{a.older_than}d={len(report['candidates'])}")
    print("by month: " + ", ".join(f"{m} {n}" for m, n in sorted(report["months"].items())))
    for rel in report["candidates"][:SHOWN]:
        print(f"  could prune: {rel}")
    if len(report["candidates"]) > SHOWN:
        print(f"  ... and {len(report['candidates']) - SHOWN} more. Deleting is a person's decision.")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
