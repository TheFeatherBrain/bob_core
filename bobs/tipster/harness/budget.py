#!/usr/bin/env python3
"""The odds request budget: every request is written down before it is made, and none exceeds the
month's allowance.

Why before: a request that crashes mid-flight still counted against the provider's quota; writing
it afterwards would undercount, and the free plan ends silently when the count is wrong. Why a
priority order (closing, then recheck, then discovery): a closing snapshot is what makes a tip
measurable (CLV), so a discovery sweep may not spend the requests the already-tipped games still
need. A recheck must leave one closing per pending game; a discovery must leave a closing and a
recheck per pending game.

Usage:
    python3 harness/budget.py --status    # OK month=YYYY-MM used=N allowance=M remaining=R
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import store  # noqa: E402
import tipstercfg  # noqa: E402

KINDS = ("closing", "recheck", "discovery")
#: Requests per call, per provider and kind. Provisional until plan Task 0 item 5 measures them.
COST = {"oddspapi": {"closing": 1, "recheck": 1, "discovery": 1}}


class Refused(Exception):
    pass


def ledger_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "odds-requests.jsonl"


def cost(kind: str, provider: str | None = None) -> int:
    table = COST.get(provider or tipstercfg.load()["odds_provider"], COST["oddspapi"])
    return table[kind]


def _month(now: dt.datetime) -> str:
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m")


def status(now: dt.datetime) -> dict:
    month = _month(now)
    rows, _ = store.read_jsonl(ledger_path())
    used = sum(r.get("cost", 0) for r in rows if r.get("month") == month)
    allowance = tipstercfg.load()["odds_requests_per_month"]
    return {"used": used, "allowance": allowance, "remaining": allowance - used, "month": month}


def spend(kind: str, pending_closing: int, now: dt.datetime) -> None:
    if kind not in KINDS:
        raise Refused(f"unknown request kind {kind!r}; one of {list(KINDS)}")
    s = status(now)
    need = cost(kind)
    if kind in ("recheck", "discovery"):
        need += pending_closing * cost("closing")
    if kind == "discovery":
        need += pending_closing * cost("recheck")
    if s["used"] + need > s["allowance"]:
        raise Refused(f"{kind} needs {need} of the {s['remaining']} requests left in {s['month']} "
                      f"({pending_closing} tipped games still need their closing snapshot)")
    store.append_jsonl(ledger_path(), [{"at": now.isoformat(), "month": s["month"], "kind": kind,
                                        "cost": cost(kind)}])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Odds request budget.")
    parser.add_argument("--status", action="store_true", required=True)
    parser.parse_args(argv)
    s = status(dt.datetime.now(dt.timezone.utc))
    print(f"OK month={s['month']} used={s['used']} allowance={s['allowance']} remaining={s['remaining']}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
