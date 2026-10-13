#!/usr/bin/env python3
"""Opportunistic insider buying: the factor with the strongest evidence in the scorecard.

Cohen, Malloy and Pomorski (2012): insiders who trade in the same month year after year (bonuses,
tax dates) carry no information; those who break their pattern earn about 0.8% a month abnormal.
So only open-market purchases (code P) count, pre-planned 10b5-1 trades are dropped, and only
buys edgar.py classified as "opportunistic" are kept: the insider has three years of filings on
record and did not trade on the open market in that month in each of the prior three years.
Routine and unclassified buyers are left out, not guessed. Several insiders buying together gets
a bonus. An amended Form 4 restating the same trade counts once. The signal sums gross purchases,
not purchases net of sales (a deviation from the spec, listed in the plan): sales are mostly
diversification or liquidity and carry little information.

"No buying" is only true if every day of the window was read, so the signal is None until the
Form 4 store covers the window (the last 3 days may lag while SEC publishes its index), and None
for an issuer whose Form 4 in the window could not be parsed.

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
import fetch  # noqa: E402

WINDOW_DAYS = 90
CLUSTER_BONUS = 1.5
INDEX_LAG_DAYS = 3


def opportunistic_buys(txns: list[dict], today: dt.date, days: int = WINDOW_DAYS) -> list[dict]:
    start = today - dt.timedelta(days=days)
    return [t for t in txns
            if t["code"] == "P" and t["acquired"] and not t["plan"] and t["shares"] and t["price"]
            and start <= dt.date.fromisoformat(t["date"]) <= today
            and t.get("class") == "opportunistic"]


def insider_signal(buys: list[dict], market_cap: float | None) -> float | None:
    if not market_cap or market_cap <= 0:
        return None
    value = sum(t["shares"] * t["price"] for t in buys)
    bonus = CLUSTER_BONUS if len({t["owner_cik"] for t in buys}) >= 2 else 1.0
    return value / market_cap * bonus


def covered(coverage: list[str], today: dt.date, days: int = WINDOW_DAYS) -> bool:
    have = set(coverage)
    return all((today - dt.timedelta(days=k)).isoformat() in have for k in range(INDEX_LAG_DAYS + 1, days + 1))


def _key(t: dict) -> tuple:
    return t["issuer_cik"], t["owner_cik"], t["date"], t["code"], t["shares"], t["price"]


def load_store(sec: pathlib.Path) -> tuple[dict[int, list[dict]], dict[int, list[str]], list[str]]:
    """(transactions by issuer, deduplicated; unparsed filing dates by issuer; days read)."""
    by_issuer: dict[int, list[dict]] = {}
    seen: set[tuple] = set()
    for path in sorted((sec / "form4").glob("????-??-??.json")):
        for t in edgar.read_json(path, []):
            if _key(t) not in seen:
                seen.add(_key(t))
                by_issuer.setdefault(t["issuer_cik"], []).append(t)
    unparsed: dict[int, list[str]] = {}
    for day, filings in edgar.read_json(sec / "form4" / "unparsed.json", {}).items():
        for f in filings:
            for cik in f["ciks"]:
                unparsed.setdefault(cik, []).append(day)
    return by_issuer, unparsed, edgar.read_json(sec / "form4" / "coverage.json", [])


def signal_for(cik: int, market_cap: float | None, store: tuple, today: dt.date) -> tuple[float | None, list[str]]:
    by_issuer, unparsed, coverage = store
    start = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    if not covered(coverage, today) or any(day >= start for day in unparsed.get(cik, [])):
        return None, []
    buys = opportunistic_buys(by_issuer.get(cik, []), today)
    return insider_signal(buys, market_cap), [t["evidence"] for t in buys]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cik", type=int, required=True)
    ap.add_argument("--market-cap", type=float, required=True)
    a = ap.parse_args(argv)
    value, evidence = signal_for(a.cik, a.market_cap, load_store(edgar.sec_dir()), fetch.utcnow().date())
    print(f"{'could not tell (Form 4 window not fully read, or a filing did not parse)' if value is None else value} {evidence}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
