#!/usr/bin/env python3
"""Turn cached SEC frames into per-company quarterly series and annual facts.

Why dates, not frame labels: SEC files each fact under the calendar frame closest to it, so
NVIDIA's quarter ending 27 July sits in CY2025Q2 while a calendar company's sits there too, and a
fiscal year ending in January lands in the previous calendar year's frame. Matching on each fact's
own start and end avoids comparing the wrong quarters.

Why derive the fourth quarter: most companies never tag Q4 on its own; the 10-K reports the year.
Q4 is the year minus the three quarters inside it, and is marked derived.

Usage:
    python3 harness/xbrl.py --cik 1045810     # print the series and annual facts for one company
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import edgar  # noqa: E402
from fundamentals.quality import AnnualFacts  # noqa: E402

VARIANT = {v: (name, i) for name, variants in edgar.CONCEPTS.items() for i, v in enumerate(variants)}
QUARTER_DAYS, YEAR_DAYS = (80, 100), (350, 380)
INSTANT_TOLERANCE_DAYS = 20
SHARES_WINDOW_DAYS = 120
FIELDS = {"net_income": "net_income", "ocf": "operating_cash_flow", "revenue": "revenue",
          "gross_profit": "gross_profit", "ebit": "ebit"}
INSTANT_FIELDS = {"assets": "total_assets", "current_assets": "current_assets",
                  "current_liabilities": "current_liabilities", "long_term_debt": "long_term_debt",
                  "liabilities": "total_liabilities", "retained_earnings": "retained_earnings"}


def _date(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def _span(row: dict) -> int | None:
    return (_date(row["end"]) - _date(row["start"])).days if row.get("start") else None


def _kind(period: str) -> str:
    return "instant" if period.endswith("I") else "quarter" if "Q" in period else "annual"


def load_frames(sec: pathlib.Path) -> dict[tuple[str, str], dict[int, list[dict]]]:
    best: dict[tuple, tuple[int, dict]] = {}
    for path in sorted((sec / "frames").glob("*.json")):
        tax, rest = path.stem.split(".", 1)
        concept, unit, period = rest.rsplit(".", 2)
        name, priority = VARIANT.get((tax, concept, unit), (None, 0))
        if name is None:
            continue
        doc = edgar.read_json(path, {})
        for row in doc.get("rows", []):
            key = (name, _kind(period), row["cik"], row.get("start"), row["end"])
            if key not in best or priority < best[key][0]:
                best[key] = (priority, {**row, "evidence": doc.get("evidence")})
    out: dict[tuple[str, str], dict[int, list[dict]]] = {}
    for (name, kind, cik, _, _), (_, row) in best.items():
        out.setdefault((name, kind), {}).setdefault(cik, []).append(row)
    for by_cik in out.values():
        for rows in by_cik.values():
            rows.sort(key=lambda r: r["end"])
    return out


def quarterly_series(quarters: list[dict], annuals: list[dict]) -> list[dict]:
    qs = [r for r in quarters if _span(r) is not None and QUARTER_DAYS[0] <= _span(r) <= QUARTER_DAYS[1]]
    by_end = {r["end"]: {"start": r["start"], "end": r["end"], "val": r["val"],
                         "evidence": [r["evidence"]], "derived": False} for r in qs}
    for year in annuals:
        span = _span(year)
        if span is None or not YEAR_DAYS[0] <= span <= YEAR_DAYS[1] or year["end"] in by_end:
            continue
        inside = sorted((q for q in qs if q["start"] >= year["start"] and q["end"] <= year["end"]),
                        key=lambda q: q["end"])
        if len(inside) != 3 or (_date(year["end"]) - _date(inside[-1]["end"])).days < QUARTER_DAYS[0]:
            continue
        by_end[year["end"]] = {
            "start": (_date(inside[-1]["end"]) + dt.timedelta(days=1)).isoformat(), "end": year["end"],
            "val": year["val"] - sum(q["val"] for q in inside),
            "evidence": [year["evidence"], *(q["evidence"] for q in inside)], "derived": True}
    return sorted(by_end.values(), key=lambda r: r["end"])


def _rows(frames: dict, name: str, kind: str, cik: int) -> list[dict]:
    return frames.get((name, kind), {}).get(cik, [])


def company_series(frames: dict, cik: int) -> dict:
    return {"revenue_q": quarterly_series(_rows(frames, "revenue", "quarter", cik),
                                          _rows(frames, "revenue", "annual", cik)),
            "eps_q": quarterly_series(_rows(frames, "eps", "quarter", cik), [])}


def _closest(rows: list[dict], target: dt.date, before: int, after: int) -> dict | None:
    """The row whose end is nearest `target`, within `before` days earlier and `after` days later."""
    near = [r for r in rows if -before <= (_date(r["end"]) - target).days <= after]
    return min(near, key=lambda r: abs((_date(r["end"]) - target).days), default=None)


def _facts_for(frames: dict, cik: int, end: str) -> tuple[AnnualFacts, list[str]]:
    target = _date(end)
    values: dict[str, float] = {}
    evidence: list[str] = []
    for name, field in FIELDS.items():
        row = next((r for r in _rows(frames, name, "annual", cik) if r["end"] == end), None)
        if row:
            values[field] = row["val"]
            evidence.append(row["evidence"])
    for name, field in INSTANT_FIELDS.items():
        row = _closest(_rows(frames, name, "instant", cik), target, INSTANT_TOLERANCE_DAYS, INSTANT_TOLERANCE_DAYS)
        if row:
            values[field] = row["val"]
            evidence.append(row["evidence"])
    shares = _closest(_rows(frames, "shares", "instant", cik), target, 0, SHARES_WINDOW_DAYS)
    if shares:
        values["shares_outstanding"] = shares["val"]
        evidence.append(shares["evidence"])
    return AnnualFacts(**values), evidence


def annual_facts(frames: dict, cik: int) -> tuple[AnnualFacts | None, AnnualFacts | None, list[str]]:
    years = [r for name in ("net_income", "revenue") for r in _rows(frames, name, "annual", cik)
             if YEAR_DAYS[0] <= (_span(r) or 0) <= YEAR_DAYS[1]]
    if not years:
        return None, None, []
    latest = max(r["end"] for r in years)
    current, evidence = _facts_for(frames, cik, latest)
    prior_end = next((r["end"] for r in sorted(years, key=lambda r: r["end"], reverse=True)
                      if 335 <= (_date(latest) - _date(r["end"])).days <= 395), None)
    if prior_end is None:
        return current, None, evidence
    previous, more = _facts_for(frames, cik, prior_end)
    return current, previous, evidence + more


def latest_shares(frames: dict, cik: int) -> tuple[float | None, str | None]:
    rows = _rows(frames, "shares", "instant", cik)
    return (rows[-1]["val"], rows[-1]["evidence"]) if rows else (None, None)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cik", type=int, required=True)
    a = ap.parse_args(argv)
    frames = load_frames(edgar.sec_dir())
    current, previous, _ = annual_facts(frames, a.cik)
    print(json.dumps({"series": company_series(frames, a.cik),
                      "current": dataclasses.asdict(current) if current else None,
                      "previous": dataclasses.asdict(previous) if previous else None,
                      "shares": latest_shares(frames, a.cik)}, indent=2))
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
