#!/usr/bin/env python3
"""Market conditions from FRED: context for the board, the digest and every call, never a score input.

Why context only: a score that moved with the economy would make a March call and an October call
mean different things, and the ledger could no longer say whether the picks or the macro earned a
result. A Trader Bob may later use these values to size positions; the Scout only records them.

Why a staleness limit per series: FRED series come out daily, weekly or monthly, and a value that
stopped updating looks exactly like a current one. Each series has the age past which its latest
observation is late, not just old (a monthly series is normally 4 to 10 weeks old).

Why LOOKBACK_DAYS is 500: CPI year on year needs the month a year before the latest one, and the
latest one is up to 75 days old (its staleness limit), so the window must reach back 365 + 75 days
plus room for a late release.

Why the keyless CSV endpoint: no credential to name, store or leak; every read is saved as evidence
by fetch.read like any other.

Usage:
    python3 harness/macro.py --refresh     # OK, or FAIL: naming unread series (file still written)
    python3 harness/macro.py --show        # state/macro.json, one line per series
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
import scoutcfg  # noqa: E402

FRED_HOST = "https://fred.stlouisfed.org/graph/fredgraph.csv?"
CSV_URL = FRED_HOST + "id={series}&cosd={start}"
HEADERS = {"User-Agent": "investor-scout/1.0 (gk@gmail.com)"}
LOOKBACK_DAYS = 500
SERIES = {
    "T10Y2Y": ("10Y minus 2Y Treasury, %", 7),
    "BAMLH0A0HYM2": ("high-yield credit spread, %", 7),
    "VIXCLS": ("VIX close", 7),
    "NFCI": ("Chicago Fed financial conditions index", 14),
    "ICSA": ("initial jobless claims", 14),
    "UNRATE": ("unemployment rate, %", 75),
    "CPIAUCSL": ("CPI, year on year %", 75),
    "FEDFUNDS": ("effective fed funds rate, %", 75),
}
YEAR_ON_YEAR = ("CPIAUCSL",)
MISSING = ("", ".")
TIMEOUT_SECONDS, RETRIES = 15, 1


def _get(url: str, headers: dict) -> str:
    """A bounded read: market conditions are context, and the nightly loop runs them first, so a
    stalled FRED must cost under a minute, not the four minutes per series fetch.get allows."""
    return fetch.get(url, headers, timeout=TIMEOUT_SECONDS, retries=RETRIES)


def parse_csv(text: str) -> list[tuple[str, float]]:
    """Observations oldest first. FRED writes a missing value (a holiday) as "." or blank; those
    rows are skipped, so the latest value is the latest real one, not a gap."""
    rows = [line.strip() for line in text.strip().splitlines()]
    if not rows or rows[0].split(",")[0].lower() not in ("observation_date", "date"):
        raise ValueError("not a FRED CSV (no observation_date header)")
    out = []
    for line in rows[1:]:
        day, _, value = line.partition(",")
        if value.strip() in MISSING:
            continue
        dt.date.fromisoformat(day)
        out.append((day, float(value)))
    return out


def year_on_year(obs: list[tuple[str, float]]) -> float | None:
    """Percent change from the same month a year earlier; None without that exact month, never a
    nearby one, so a gap in the series is not hidden."""
    day, value = obs[-1]
    last = dt.date.fromisoformat(day)
    target = dt.date(last.year - 1, last.month, last.day).isoformat()
    before = next((v for d, v in obs if d == target), None)
    return None if not before else (value / before - 1) * 100


def _read(series: str, url: str, getter) -> tuple[str, pathlib.Path]:
    if not url.startswith(FRED_HOST):
        raise fetch.FetchError(url, None, "refused: macro reads only the FRED CSV endpoint")
    return fetch.read(f"fred-{series.lower()}", url, HEADERS, getter=getter)


def read_series(series: str, today: dt.date, getter=_get) -> dict:
    what, stale_after = SERIES[series]
    row = {"what": what, "value": None, "observed": None, "stale": None, "stale_after_days": stale_after,
           "evidence": None, "reason": None}
    url = CSV_URL.format(series=series, start=(today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat())
    try:
        raw, where = _read(series, url, getter)
    except fetch.NotFound as e:
        return {**row, "evidence": str(e.evidence), "reason": "404 not found"}
    except fetch.FetchError as e:
        return {**row, "reason": str(e)}
    row["evidence"] = str(where)
    try:
        obs = parse_csv(raw)
    except ValueError as e:
        return {**row, "reason": f"unreadable: {e}"}
    if not obs:
        return {**row, "reason": f"no observations in the last {LOOKBACK_DAYS} days"}
    observed = obs[-1][0]
    stale = (today - dt.date.fromisoformat(observed)).days > stale_after
    value = year_on_year(obs) if series in YEAR_ON_YEAR else obs[-1][1]
    if value is None:
        return {**row, "observed": observed, "stale": stale, "reason": "no observation from 12 months earlier"}
    return {**row, "value": value, "observed": observed, "stale": stale}


def path() -> pathlib.Path:
    return scoutcfg.state_dir() / "macro.json"


def load() -> dict:
    return edgar.read_json(path(), {})


def _unreachable(row: dict) -> bool:
    """No value and no evidence: the read itself failed (timeout, refused connection, 5xx). A 404
    or an unreadable body saves evidence and says nothing about the next series."""
    return row["value"] is None and row["evidence"] is None


def refresh(now: dt.datetime, getter=_get) -> tuple[int, str]:
    """Reads every series, except that after the first unreachable read the rest are recorded as
    could not tell without asking: one outage must not cost one timeout per series."""
    series: dict[str, dict] = {}
    down = None
    for s, (what, stale_after) in SERIES.items():
        if down is not None:
            series[s] = {"what": what, "value": None, "observed": None, "stale": None,
                         "stale_after_days": stale_after, "evidence": None,
                         "reason": f"FRED unreachable ({down}); not asked"}
            continue
        series[s] = read_series(s, now.date(), getter)
        if _unreachable(series[s]):
            down = series[s]["reason"]
    edgar.write_json(path(), {"refreshed_at": now.isoformat(timespec="seconds"), "series": series})
    unread = [s for s, r in series.items() if r["value"] is None]
    stale = [s for s, r in series.items() if r["stale"]]
    if unread:
        return 1, f"FAIL: could not tell {', '.join(unread)}; stale={stale}"
    return 0, f"OK series={len(series)} stale={stale}"


def lines(doc: dict) -> list[str]:
    """One line per series for the board and the digest; the only renderer, so both say the same.
    A value read from FRED is quoted as read; only the computed year-on-year change is rounded."""
    if not doc.get("series"):
        return ["market conditions not refreshed (run macro.py --refresh)"]
    out = []
    for series, r in doc["series"].items():
        ev = f" [ev:{r['evidence']}]" if r.get("evidence") else ""
        if r.get("value") is None:
            out.append(f"{series} {r['what']}: could not tell ({r['reason']}){ev}")
            continue
        flag = " STALE" if r.get("stale") else ""
        value = f"{r['value']:.2f}" if series in YEAR_ON_YEAR else f"{r['value']:.10g}"
        out.append(f"{series} {r['what']}: {value} on {r['observed']}{flag}{ev}")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args(argv)
    if a.refresh:
        code, message = refresh(fetch.utcnow())
        print(message)
        return code
    if a.show:
        print("\n".join(lines(load())))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
