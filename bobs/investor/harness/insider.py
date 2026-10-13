#!/usr/bin/env python3
"""Opportunistic insider buying: the factor with the strongest evidence in the scorecard.

Cohen, Malloy and Pomorski (2012): insiders who trade in the same month year after year (bonuses,
tax dates) carry no information; those who break their pattern earn about 0.8% a month abnormal.
So only open-market purchases (code P) count, pre-planned 10b5-1 trades are dropped, and a buyer
who traded in the same month in each of the three prior years is routine. Several insiders buying
together gets a bonus. Buys by an owner whose history was never read are left out, not guessed.

"No buying" is only true if every day of the window was read, so the signal is None until the
Form 4 store covers the window (the last 3 days may lag while SEC publishes its index).

Usage:
    python3 harness/insider.py --cik 1045810 --market-cap 4.4e12
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402

WINDOW_DAYS = 90
ROUTINE_YEARS = (1, 2, 3)
CLUSTER_BONUS = 1.5
INDEX_LAG_DAYS = 3


def is_routine(owner_dates: list[dt.date], trade: dt.date) -> bool:
    return all(any(d.year == trade.year - k and d.month == trade.month for d in owner_dates)
               for k in ROUTINE_YEARS)


def opportunistic_buys(txns: list[dict], owners: dict[int, list[dt.date]], today: dt.date,
                       days: int = WINDOW_DAYS) -> list[dict]:
    start = today - dt.timedelta(days=days)
    return [t for t in txns
            if t["code"] == "P" and t["acquired"] and not t["plan"] and t["shares"] and t["price"]
            and start <= dt.date.fromisoformat(t["date"]) <= today
            and t["owner_cik"] in owners
            and not is_routine(owners[t["owner_cik"]], dt.date.fromisoformat(t["date"]))]


def insider_signal(buys: list[dict], market_cap: float | None) -> float | None:
    if not market_cap or market_cap <= 0:
        return None
    value = sum(t["shares"] * t["price"] for t in buys)
    bonus = CLUSTER_BONUS if len({t["owner_cik"] for t in buys}) >= 2 else 1.0
    return value / market_cap * bonus


def covered(coverage: list[str], today: dt.date, days: int = WINDOW_DAYS) -> bool:
    have = set(coverage)
    return all((today - dt.timedelta(days=k)).isoformat() in have for k in range(INDEX_LAG_DAYS + 1, days + 1))


def load_store(sec: pathlib.Path) -> tuple[dict[int, list[dict]], dict[int, list[dt.date]], list[str]]:
    by_issuer: dict[int, list[dict]] = {}
    for path in sorted((sec / "form4").glob("????-??-??.json")):
        for t in edgar.read_json(path, []):
            by_issuer.setdefault(t["issuer_cik"], []).append(t)
    owners = {int(p.stem): [dt.date.fromisoformat(d) for d in edgar.read_json(p, {}).get("dates", [])]
              for p in (sec / "owners").glob("*.json")}
    return by_issuer, owners, edgar.read_json(sec / "form4" / "coverage.json", [])


def signal_for(cik: int, market_cap: float | None, store: tuple, today: dt.date) -> tuple[float | None, list[str]]:
    by_issuer, owners, coverage = store
    if not covered(coverage, today):
        return None, []
    buys = opportunistic_buys(by_issuer.get(cik, []), owners, today)
    return insider_signal(buys, market_cap), [t["evidence"] for t in buys]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cik", type=int, required=True)
    ap.add_argument("--market-cap", type=float, required=True)
    a = ap.parse_args(argv)
    value, evidence = signal_for(a.cik, a.market_cap, load_store(edgar.sec_dir()), dt.date.today())
    print(f"{'could not tell (Form 4 window not fully read)' if value is None else value} {evidence}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
