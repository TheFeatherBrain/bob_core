#!/usr/bin/env python3
"""The weekly digest: what the Scout called, changed and learned, in Markdown plus JSON.

Every number comes from the ledger and ledger_score's results, never from the agent's prose, so
the digest cannot round a loss into a win. The noise banner and the interim labels are written by
code for the same reason. The JSON copy is what a future sender (Telegram) reads; the Markdown is
what a person reads today.

Usage:
    python3 harness/digest.py --write      # digests/<today>.md and .json in the Bob folder
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import calls  # noqa: E402
import edgar  # noqa: E402
import fetch  # noqa: E402
import ledger_score  # noqa: E402
import scoutcfg  # noqa: E402
import screen  # noqa: E402

WINDOW_DAYS = 7
RANK = {"pass": 0, "could not tell": 1, "watch": 2, "buy-worthy": 3}


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:+.1%}"


def _share(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _changes(rows: list[dict], since: str) -> list[dict]:
    out, last = [], {}
    for row in (r for r in rows if r.get("kind") == "verdict"):
        before = last.get(row["ticker"])
        if row["at"][:10] > since and before and before != row["verdict"]:
            out.append({"ticker": row["ticker"], "from": before, "to": row["verdict"],
                        "direction": "up" if RANK[row["verdict"]] > RANK[before] else "down"})
        last[row["ticker"]] = row["verdict"]
    return out


def _matured(results: dict, since: str, until: str) -> list[dict]:
    return [{"id": c["id"], "ticker": c["ticker"], "months": int(h), "excess_spy": s["excess_spy"],
             "excess_qqq": s["excess_qqq"]}
            for c in results.get("calls", []) for h, s in c["horizons"].items()
            if s.get("status") == "scored" and since < s["due"] <= until]


def _entrants(since: str) -> list[str]:
    seen: dict[str, None] = {}
    for path in sorted(screen.screen_dir().glob("????-??-??.json")):
        if path.stem > since:
            seen.update(dict.fromkeys(edgar.read_json(path, {}).get("new_entrants", [])))
    return list(seen)


def _failed_screens(since: str) -> list[dict]:
    path = screen.screen_dir() / "failures.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("at", "")[:10] > since:
            rows.append(row)
    return rows


def _table(summary: dict) -> list[str]:
    lines = ["| horizon | calls scored | avg vs SPY | avg vs QQQ | beat SPY |", "|---|---|---|---|---|"]
    for h in ledger_score.HORIZONS:
        s = summary.get(str(h), {})
        label = f"{h} months" + (" (interim)" if s.get("interim", h != 12) else "")
        lines.append(f"| {label} | {s.get('n', 0)} | {_pct(s.get('avg_excess_spy'))} | "
                     f"{_pct(s.get('avg_excess_qqq'))} | {_share(s.get('beat_spy'))} |")
    return lines


def build(today: dt.date) -> tuple[str, dict]:
    rows, torn = calls.read()
    since = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    recent = [r for r in rows if r["at"][:10] > since]
    results = edgar.read_json(ledger_score.results_path(), {})
    summary = results.get("summary", {})
    scored_12m = summary.get("12", {}).get("n", 0)
    data = {"week_ending": today.isoformat(),
            "new_calls": [{k: r.get(k) for k in ("id", "ticker", "lane", "score")} for r in recent if r["kind"] == "call"],
            "changes": _changes(rows, since),
            "withdrawn": [{k: r[k] for k in ("call_id", "ticker", "reason")} for r in recent if r["kind"] == "withdrawn"],
            "matured": _matured(results, since, today.isoformat()), "entrants": _entrants(since),
            "failed_screens": _failed_screens(since),
            "summary": summary, "by_lane": results.get("by_lane", {}), "scored_12m": scored_12m,
            "min_scored": ledger_score.MIN_SCORED, "noise": scored_12m < ledger_score.MIN_SCORED, "torn_lines": torn}
    lines = [f"# Investor Scout digest, week ending {today.isoformat()}", ""]
    if data["noise"]:
        lines += [f"> Too few scored calls to judge: {scored_12m} of {ledger_score.MIN_SCORED} needed for a "
                  "12-month verdict. Treat every number below as noise.", ""]
    if data["failed_screens"]:
        lines += ["## Screens that did not run", ""]
        lines += [f"- {f['at'][:10]}: {f['message']}" for f in data["failed_screens"]] + [""]
    lines += ["## New buy-worthy calls", ""]
    lines += [f"- {c['ticker']} ({c['lane']} lane, score {c['score']:.0f}): theses/{c['ticker']}.md"
              for c in data["new_calls"]] or ["- none"]
    lines += ["", "## Verdict changes", ""]
    lines += [f"- {c['ticker']}: {c['from']} -> {c['to']} ({c['direction']})" for c in data["changes"]] or ["- none"]
    lines += ["", "## Withdrawn calls (still scored)", ""]
    lines += [f"- {w['call_id']}: {w['reason']}" for w in data["withdrawn"]] or ["- none"]
    lines += ["", "## Calls reaching 3, 6 or 12 months", ""]
    lines += [f"- {m['ticker']} {m['months']} months{' (interim)' if m['months'] != 12 else ''}: "
              f"{_pct(m['excess_spy'])} vs SPY, {_pct(m['excess_qqq'])} vs QQQ" for m in data["matured"]] or ["- none"]
    lines += ["", "## Scout vs SPY vs QQQ (all calls)", ""] + _table(summary)
    for lane in ("numbers", "story"):
        lines += ["", f"## {lane.capitalize()} lane", ""] + _table(data["by_lane"].get(lane, {}))
    lines += ["", "## New names on the shortlist this week", "", ", ".join(data["entrants"]) or "none"]
    if torn:
        lines += ["", f"> {torn} torn ledger line(s) were skipped; see calls.py list."]
    return "\n".join(lines) + "\n", data


def write(today: dt.date) -> pathlib.Path:
    text, data = build(today)
    folder = scoutcfg.bob_dir() / "digests"
    folder.mkdir(exist_ok=True)
    path = folder / f"{today.isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    edgar.write_json(folder / f"{today.isoformat()}.json", data)
    return path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", required=True)
    ap.parse_args(argv)
    print(f"OK {write(fetch.utcnow().date())}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
