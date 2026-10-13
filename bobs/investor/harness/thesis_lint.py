#!/usr/bin/env python3
"""Refuse a thesis that is not finished: missing a case, an uncited number, a citation to nothing.

What it checks: the Verdict, Lane and Date lines; the four claim sections exist; Bull and Bear each
carry at least one cited claim; every line that contains a digit outside an inline command
(`python3 ...`; any other backticked text still counts, so backticks cannot hide a claim) carries a
[ev:...] citation, in every section but two: the pasted Scorecard (code output with its own
citations) and "What would change my mind" (future conditions, which no evidence can show yet); every citation names a saved evidence folder (one with meta.json) inside this
Bob's evidence dir.

What it cannot check: that the cited evidence says what the line claims. That is core's known
limit ("no machine claim verification"); the citation makes it checkable by a person.

Usage:
    python3 harness/thesis_lint.py theses/NVDA.md
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import scoutcfg  # noqa: E402

VERDICTS = ("buy-worthy", "watch", "pass", "could not tell")
LANES = ("numbers", "story")
CITE = re.compile(r"\[ev:([^\]]+)\]")
HEADER = re.compile(r"^(Verdict|Lane|Date):[ \t]*(.*?)[ \t]*$", re.M)
CLAIM_SECTIONS = ("Bull case", "Bear case", "Reverse DCF", "What would change my mind")
MUST_CITE = ("Bull case", "Bear case")
UNCHECKED = ("Scorecard", "What would change my mind")
CODE = re.compile(r"`python3? [^`]*`")


def header(text: str) -> dict:
    return {key.lower(): value for key, value in HEADER.findall(text)}


def sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def resolve(cite: str, evidence_dir: pathlib.Path) -> pathlib.Path | None:
    path = pathlib.Path(cite.strip())
    target = path if path.is_absolute() else evidence_dir / path
    try:
        target.resolve().relative_to(evidence_dir.resolve())
    except ValueError:
        return None
    return target if (target / "meta.json").is_file() else None


def lint(text: str, evidence_dir: pathlib.Path) -> list[str]:
    errors = []
    head = header(text)
    if head.get("verdict") not in VERDICTS:
        errors.append(f"Verdict: one of {', '.join(VERDICTS)}")
    if head.get("lane") not in LANES:
        errors.append(f"Lane: one of {', '.join(LANES)}")
    try:
        dt.date.fromisoformat(head.get("date", ""))
    except ValueError:
        errors.append("Date: YYYY-MM-DD")
    found = sections(text)
    errors += [f"missing section: ## {name}" for name in CLAIM_SECTIONS if name not in found]
    for name, lines in found.items():
        if name in UNCHECKED:
            continue
        for line in (raw.strip() for raw in lines):
            if line and re.search(r"\d", CODE.sub("", CITE.sub("", line))) and not CITE.search(line):
                errors.append(f"{name}: uncited number: {line[:80]}")
    errors += [f"{name}: no cited claim" for name in MUST_CITE
               if name in found and not any(CITE.search(line) for line in found[name])]
    errors += [f"evidence not found: {cite}" for cite in CITE.findall(text) if resolve(cite, evidence_dir) is None]
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    path = pathlib.Path(argv[0])
    if not path.is_absolute():
        path = scoutcfg.bob_dir() / path
    errors = lint(path.read_text(encoding="utf-8"), scoutcfg.evidence_dir())
    for err in errors:
        print(f"FAIL: {err}")
    if not errors:
        print("OK")
    return 1 if errors else 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
