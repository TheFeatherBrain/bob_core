#!/usr/bin/env python3
"""Read SEC EDGAR for this Bob: the listed universe, XBRL frames, the daily index, Form 4s.

Why frames: one frames call returns one figure for every company in a period, so screening ~3,000
companies costs ~120 calls instead of 3,000 companyfacts downloads of several MB each. Frames that
ended long ago rarely change and are fetched once; recent ones are refreshed every few days (late
filers, amendments).

Why a Form 4 store with coverage: "no insider bought" is only true if every day of the window was
read. The store records the days read; the insider factor stays "could not tell" until the whole
window is there. A missing index is counted as read only for weekends or when it is old enough
that SEC has certainly published it; otherwise the day is retried next run.

Every read is saved as evidence (fetch.read) before it is parsed.

Usage:
    python3 harness/edgar.py --sync                      # tickers, frames, last 7 days of Form 4s
    python3 harness/edgar.py --backfill-form4 --days 90  # one-off: fill the insider window
    python3 harness/edgar.py --selftest-live             # one real read of each kind (network)
    python3 harness/edgar.py --read URL                  # any SEC filing, saved as evidence first
    python3 harness/edgar.py --eight-k --days 3          # recent 8-Ks of universe companies
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import fetch  # noqa: E402
import scoutcfg  # noqa: E402

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FRAME_URL = "https://data.sec.gov/api/xbrl/frames/{tax}/{concept}/{unit}/{period}.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}/form.{ymd}.idx"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/{path}"
SEC_HOSTS = ("https://www.sec.gov/", "https://data.sec.gov/")
EXCHANGES = {"NYSE", "Nasdaq"}
SETTLED_DAYS = 270
INDEX_PUBLISHED_DAYS = 3
OWNER_REFRESH_DAYS = 30
QUARTERS_BACK, YEARS_BACK, INSTANTS_BACK = 13, 3, 6

CONCEPTS: dict[str, list[tuple[str, str, str]]] = {
    "revenue": [("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
                ("us-gaap", "Revenues", "USD"), ("us-gaap", "SalesRevenueNet", "USD")],
    "eps": [("us-gaap", "EarningsPerShareDiluted", "USD-per-shares")],
    "net_income": [("us-gaap", "NetIncomeLoss", "USD")],
    "ocf": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD")],
    "gross_profit": [("us-gaap", "GrossProfit", "USD")],
    "ebit": [("us-gaap", "OperatingIncomeLoss", "USD")],
    "assets": [("us-gaap", "Assets", "USD")],
    "current_assets": [("us-gaap", "AssetsCurrent", "USD")],
    "current_liabilities": [("us-gaap", "LiabilitiesCurrent", "USD")],
    "long_term_debt": [("us-gaap", "LongTermDebtNoncurrent", "USD")],
    "liabilities": [("us-gaap", "Liabilities", "USD")],
    "retained_earnings": [("us-gaap", "RetainedEarningsAccumulatedDeficit", "USD")],
    "shares": [("dei", "EntityCommonStockSharesOutstanding", "shares")],
}
QUARTERLY = ("revenue", "eps")
ANNUAL = ("revenue", "net_income", "ocf", "gross_profit", "ebit")
INSTANT = ("assets", "current_assets", "current_liabilities", "long_term_debt", "liabilities",
           "retained_earnings", "shares")


def parse_tickers(text: str) -> list[dict]:
    doc = json.loads(text)
    rows = [dict(zip(doc["fields"], r)) for r in doc["data"]]
    return [{"cik": int(r["cik"]), "name": r["name"], "ticker": r["ticker"], "exchange": r["exchange"]}
            for r in rows if r.get("exchange") in EXCHANGES and r.get("ticker")]


def sec_to_alpaca(ticker: str) -> str:
    """SEC writes share classes with a hyphen (BRK-B), Alpaca with a dot (BRK.B)."""
    return ticker.upper().replace("-", ".")


def parse_frame(text: str) -> list[dict]:
    out = []
    for r in json.loads(text).get("data", []):
        row = {"cik": int(r["cik"]), "val": float(r["val"]), "end": r["end"], "accn": r.get("accn")}
        if "start" in r:
            row["start"] = r["start"]
        out.append(row)
    return out


def parse_daily_index(text: str) -> list[dict]:
    lines = text.splitlines()
    try:
        first = next(i for i, line in enumerate(lines) if line.startswith("-----")) + 1
    except StopIteration:
        return []
    out = []
    for line in lines[first:]:
        parts = line.rsplit(None, 3)
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        head = re.split(r"\s{2,}", parts[0].strip(), maxsplit=1)
        form, name = head[0], head[1] if len(head) > 1 else ""
        out.append({"form": form, "name": name, "cik": int(parts[1]),
                    "date": f"{parts[2][:4]}-{parts[2][4:6]}-{parts[2][6:]}", "path": parts[3]})
    return out


def parse_form4(text: str) -> list[dict]:
    """Non-derivative transactions of one Form 4. The 10b5-1 box (aff10b5One, on forms since
    2023) marks pre-planned trades, which carry no information about the insider's view."""
    m = re.search(r"<XML>(.*?)</XML>", text, re.S | re.I)
    if not m:
        return []
    root = ET.fromstring(m.group(1).strip().encode())

    def val(node, path: str) -> str | None:
        el = node.find(path) if node is not None else None
        return el.text.strip() if el is not None and el.text else None

    owner = root.find("reportingOwner/reportingOwnerId")
    common = {"issuer_cik": int(val(root, "issuer/issuerCik") or 0),
              "owner_cik": int(val(owner, "rptOwnerCik") or 0),
              "owner": val(owner, "rptOwnerName"),
              "plan": (val(root, "aff10b5One") or "").lower() in ("1", "true")}
    out = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = val(t, "transactionAmounts/transactionShares/value")
        price = val(t, "transactionAmounts/transactionPricePerShare/value")
        out.append({**common,
                    "date": (val(t, "transactionDate/value") or "")[:10],
                    "code": val(t, "transactionCoding/transactionCode"),
                    "shares": float(shares) if shares else None,
                    "price": float(price) if price else None,
                    "acquired": val(t, "transactionAmounts/transactionAcquiredDisposedCode/value") == "A"})
    return out


def parse_owner_dates(text: str) -> list[dt.date]:
    recent = json.loads(text)["filings"]["recent"]
    return [dt.date.fromisoformat(d) for form, d in zip(recent["form"], recent["filingDate"])
            if form in ("4", "4/A")]


def quarter_of(day: dt.date) -> int:
    return (day.month - 1) // 3 + 1


def quarter_end(year: int, q: int) -> dt.date:
    return dt.date(year, q * 3, (31, 30, 30, 31)[q - 1])


def recent_quarters(today: dt.date, n: int) -> list[tuple[int, int]]:
    """The last n completed calendar quarters, oldest first; the current quarter is never one."""
    year, q, out = today.year, quarter_of(today), []
    for _ in range(n):
        year, q = (year - 1, 4) if q == 1 else (year, q - 1)
        out.append((year, q))
    return out[::-1]


def frame_jobs(today: dt.date) -> list[dict]:
    jobs = []

    def add(name: str, period: str, end: dt.date) -> None:
        jobs.extend({"name": name, "tax": tax, "concept": concept, "unit": unit, "period": period,
                     "period_end": end} for tax, concept, unit in CONCEPTS[name])
    for name in QUARTERLY:
        for y, q in recent_quarters(today, QUARTERS_BACK):
            add(name, f"CY{y}Q{q}", quarter_end(y, q))
    for name in ANNUAL:
        for y in range(today.year - YEARS_BACK, today.year):
            add(name, f"CY{y}", dt.date(y, 12, 31))
    for name in INSTANT:
        for y, q in recent_quarters(today, INSTANTS_BACK):
            add(name, f"CY{y}Q{q}I", quarter_end(y, q))
    return jobs


class Client:
    def __init__(self, contact: str, *, getter=fetch.get, limiter: fetch.Limiter | None = None,
                 base: pathlib.Path | None = None):
        self.headers = {"User-Agent": contact, "Accept-Encoding": "identity"}
        self.getter = getter
        self.limiter = limiter or fetch.Limiter(8)
        self.base = base

    def _read(self, prefix: str, url: str) -> tuple[str, pathlib.Path]:
        return fetch.read(prefix, url, self.headers, limiter=self.limiter, getter=self.getter,
                          base=self.base)

    def tickers(self) -> tuple[list[dict], pathlib.Path]:
        raw, where = self._read("sec-tickers", TICKERS_URL)
        return parse_tickers(raw), where

    def frame(self, tax: str, concept: str, unit: str, period: str) -> tuple[list[dict], pathlib.Path]:
        url = FRAME_URL.format(tax=tax, concept=concept, unit=unit, period=period)
        try:
            raw, where = self._read(f"sec-frame-{period}-{concept}", url)
        except fetch.NotFound as e:
            return [], e.evidence
        return parse_frame(raw), where

    def daily_index(self, day: dt.date) -> tuple[list[dict] | None, pathlib.Path]:
        url = INDEX_URL.format(year=day.year, q=quarter_of(day), ymd=f"{day:%Y%m%d}")
        try:
            raw, where = self._read("sec-index", url)
        except fetch.NotFound as e:
            return None, e.evidence
        return parse_daily_index(raw), where

    def owner_dates(self, cik: int) -> tuple[list[dt.date], pathlib.Path]:
        raw, where = self._read(f"sec-owner-{cik}", SUBMISSIONS_URL.format(cik=cik))
        return parse_owner_dates(raw), where

    def form4(self, path: str) -> tuple[list[dict], pathlib.Path]:
        raw, where = self._read("sec-form4", ARCHIVE_URL.format(path=path))
        return parse_form4(raw), where

    def document(self, url: str) -> pathlib.Path:
        """Any SEC document, saved as evidence before anyone reads it. Other hosts are refused:
        a thesis cites SEC filings, and other web reads go through evidence.py by hand."""
        if not url.startswith(SEC_HOSTS):
            raise fetch.FetchError(url, None, "refused: --read only fetches sec.gov documents")
        return self._read("sec-doc", url)[1]


def sec_dir(state: pathlib.Path | None = None) -> pathlib.Path:
    return (state or scoutcfg.state_dir()) / "sec"


def read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: pathlib.Path, obj) -> None:
    """Replace a cache file atomically: a crash mid-write leaves the old copy, not half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, default=str))
    os.replace(tmp, path)


def sync_tickers(client, sec: pathlib.Path) -> list[dict]:
    rows, where = client.tickers()
    write_json(sec / "tickers.json", {"fetched": dt.date.today().isoformat(), "evidence": str(where), "rows": rows})
    return rows


def frame_file(sec: pathlib.Path, job: dict) -> pathlib.Path:
    return sec / "frames" / f"{job['tax']}.{job['concept']}.{job['unit']}.{job['period']}.json"


def sync_frames(client, sec: pathlib.Path, today: dt.date, refresh_days: int) -> int:
    fetched = 0
    for job in frame_jobs(today):
        path = frame_file(sec, job)
        cached = read_json(path, None)
        if cached is not None:
            if (today - job["period_end"]).days > SETTLED_DAYS:
                continue
            if (today - dt.date.fromisoformat(cached["fetched"])).days < refresh_days:
                continue
        rows, where = client.frame(job["tax"], job["concept"], job["unit"], job["period"])
        write_json(path, {"fetched": today.isoformat(), "evidence": str(where),
                          "period_end": job["period_end"].isoformat(), "rows": rows})
        fetched += 1
    return fetched


def _index_settled(day: dt.date, today: dt.date) -> bool:
    return day.weekday() >= 5 or (today - day).days > INDEX_PUBLISHED_DAYS


def sync_form4(client, sec: pathlib.Path, days: list[dt.date], ciks: set[int], today: dt.date) -> dict:
    folder = sec / "form4"
    coverage = set(read_json(folder / "coverage.json", []))
    summary = {"days_read": 0, "filings": 0, "purchases": 0}
    for day in sorted(days):
        if day.isoformat() in coverage:
            continue
        rows, _ = client.daily_index(day)
        if rows is None:
            if _index_settled(day, today):
                coverage.add(day.isoformat())
            continue
        paths = sorted({r["path"] for r in rows if r["form"] in ("4", "4/A") and r["cik"] in ciks})
        found = []
        for path in paths:
            txns, where = client.form4(path)
            found += [{**t, "evidence": str(where)} for t in txns if t["issuer_cik"] in ciks]
        for owner in sorted({t["owner_cik"] for t in found if t["code"] == "P" and not t["plan"]}):
            _refresh_owner(client, sec, owner, today)
        write_json(folder / f"{day.isoformat()}.json", found)
        coverage.add(day.isoformat())
        summary["days_read"] += 1
        summary["filings"] += len(paths)
        summary["purchases"] += sum(t["code"] == "P" and not t["plan"] for t in found)
    write_json(folder / "coverage.json", sorted(coverage))
    return summary


def _refresh_owner(client, sec: pathlib.Path, cik: int, today: dt.date) -> None:
    path = sec / "owners" / f"{cik}.json"
    cached = read_json(path, None)
    if cached and (today - dt.date.fromisoformat(cached["fetched"])).days < OWNER_REFRESH_DAYS:
        return
    dates, where = client.owner_dates(cik)
    write_json(path, {"fetched": today.isoformat(), "evidence": str(where), "dates": [d.isoformat() for d in dates]})


def eight_ks(client, days: list[dt.date], ciks: dict[int, str]) -> list[dict]:
    found: dict[str, dict] = {}
    for day in sorted(set(days)):
        rows, _ = client.daily_index(day)
        for r in rows or []:
            if r["form"] in ("8-K", "8-K/A") and r["cik"] in ciks:
                found.setdefault(r["path"], {**r, "ticker": ciks[r["cik"]], "url": ARCHIVE_URL.format(path=r["path"])})
    return list(found.values())


def _days_back(today: dt.date, n: int) -> list[dt.date]:
    return [today - dt.timedelta(days=i) for i in range(n, 0, -1)]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--backfill-form4", action="store_true")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--selftest-live", action="store_true")
    ap.add_argument("--read", metavar="URL")
    ap.add_argument("--eight-k", action="store_true")
    a = ap.parse_args(argv)
    cfg = scoutcfg.load()
    client = Client(cfg["sec_contact"])
    sec = sec_dir()
    today = fetch.utcnow().date()
    try:
        if a.selftest_live:
            rows, _ = client.tickers()
            (y, q), = recent_quarters(today, 2)[:1]
            frame, _ = client.frame("us-gaap", "Revenues", "USD", f"CY{y}Q{q}")
            index, _ = client.daily_index(today - dt.timedelta(days=7 + today.weekday()))
            print(f"OK tickers={len(rows)} frame CY{y}Q{q} rows={len(frame)} "
                  f"index rows={'none' if index is None else len(index)}")
            return 0 if rows and frame else 1
        if a.read:
            print(f"OK {client.document(a.read)}")
            return 0
        if a.eight_k:
            rows = read_json(sec / "tickers.json", {}).get("rows", [])
            for r in eight_ks(client, _days_back(today, a.days), {t["cik"]: t["ticker"] for t in rows}):
                print(f"{r['ticker']:8} {r['date']}  {r['form']:6} {r['url']}")
            return 0
        if a.sync:
            rows = sync_tickers(client, sec)
            frames = sync_frames(client, sec, today, cfg["frames_refresh_days"])
            form4 = sync_form4(client, sec, _days_back(today, 7), {r["cik"] for r in rows}, today)
            print(f"OK tickers={len(rows)} frames_fetched={frames} form4={form4}")
            return 0
        if a.backfill_form4:
            rows = read_json(sec / "tickers.json", {}).get("rows") or sync_tickers(client, sec)
            form4 = sync_form4(client, sec, _days_back(today, a.days), {r["cik"] for r in rows}, today)
            print(f"OK backfill {a.days} days form4={form4}")
            return 0
    except fetch.Forbidden:
        print("FAIL: SEC 403 (check User-Agent / rate); stopped all SEC calls for this run")
        return 1
    except fetch.FetchError as e:
        print(f"FAIL: {e}")
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
