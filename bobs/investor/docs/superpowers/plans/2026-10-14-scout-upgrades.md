# Scout Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Investor Scout market-conditions context from FRED, sector-relative ranks for growth and quality, an Altman exemption for financials, a 3-group agreement gate, a full scorecard on every call, and a check that fails on data fetched but never used.

**Architecture:** Two new reader/library modules (`macro.py`, `sectors.py`), one new audit module (`data_audit.py`), and targeted changes to `edgar.py`, `scorecard.py`, `screen.py`, `calls.py`, `ledger_score.py`, `digest.py`, `scout_board.py` and the nightly loop. Market conditions are context only; no score reads them. `scorecard_version` goes from 1 to 2.

**Tech Stack:** Python 3.11+, standard library only. Tests are plain-assert scripts run through `harness/testkit.py` (no pytest).

**Spec:** `bobs/investor/docs/superpowers/specs/2026-10-14-scout-upgrades-design.md`

## Global Constraints

- Stdlib only. No new dependencies, no `requirements.txt`.
- Every `read_text` / `write_text` / `open` / `subprocess(text=True)` passes `encoding="utf-8"` (`harness/test_utf8_io.py` and core's `test_text_encoding.py` refuse otherwise; test files are exempt but use it anyway).
- Every network read goes through `fetch.read` so it is saved as evidence before use.
- Module shape: docstring with purpose (the *why*) and a `Usage:` block, `from __future__ import annotations`, `sys.path.insert(0, ...)` then `import scoutpath  # noqa: E402,F401`, `main(argv) -> int`, `if __name__ == "__main__":` starting with `oscompat.utf8_stdio()`.
- Output convention: `OK ...` or `FAIL: ...`, exit 1 on failure.
- No em-dashes (U+2014) anywhere: code, comments, docs, commit messages.
- Missing data is `None` / "could not tell", never a default, a zero or a carried-over value.
- Tests come in refuse/allow pairs. After a new check passes, break the guarded line once with the editor (not `sed -i`), confirm the test fails, restore it.
- Never edit `bob_core` core files from inside a Bob's work; this plan edits core only in Task 11 (`PLAYBOOK.md`), as the spec says.
- Work on `main`; commit after each task and `git push` after each commit. End every commit message with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- All commands run from `bobs/investor/` unless stated. Run one suite: `python3 harness/test_<name>.py`. Run all: `./selftest.sh`.

## Review Focus

1. A FRED CSV whose newest rows are holidays (`.` or blank) must report the latest real value, not "could not tell" (Task 1 `test_missing_value_rows_are_skipped_not_read_as_gaps`).
2. A torn `state/sec/sic.json` must not stop the nightly screen; everything ranks market-wide that night (Task 6 `test_corrupt_sic_cache_is_ignored_not_fatal`).
3. A sector with enough members but too few *known* values for a signal must rank that signal market-wide (Task 5 `test_sector_size_counts_known_values_only`).
4. Calls recorded before this change (no `card`) must still list, score and appear in the digest unchanged (Task 8 `test_old_calls_without_a_card_score_as_before`).
5. A call recorded before `macro.py` ever ran must still record, with `macro == {}` (Task 7 `test_call_without_market_conditions_records_them_as_empty`).

---

### Task 1: Market conditions reader (`macro.py`)

**Files:**
- Create: `bobs/investor/harness/macro.py`
- Test: `bobs/investor/harness/test_macro.py`

**Interfaces:**
- Consumes: `fetch.read(prefix, url, headers, *, getter)`, `fetch.FetchError`, `fetch.NotFound` (has `.evidence`), `edgar.read_json`, `edgar.write_json`, `scoutcfg.state_dir()`.
- Produces:
  - `macro.SERIES: dict[str, tuple[str, int]]` (id -> (description, stale-after days))
  - `macro.read_series(series: str, today: dt.date, getter=fetch.get) -> dict` with keys `what, value, observed, stale, stale_after_days, evidence, reason`
  - `macro.refresh(now: dt.datetime, getter=fetch.get) -> tuple[int, str]`
  - `macro.path() -> pathlib.Path` (`state/macro.json`), `macro.load() -> dict` (`{}` when absent)
  - `macro.lines(doc: dict) -> list[str]` (the one renderer for board and digest)

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_macro.py`:

```python
#!/usr/bin/env python3
"""macro: FRED reads saved as evidence, staleness by release cycle, nothing filled in.

Run:  python3 harness/test_macro.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import fetch  # noqa: E402
import macro  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 14, 6, 0, tzinfo=dt.timezone.utc)
TODAY = NOW.date()


def _csv(series: str, rows: list[tuple[str, str]]) -> str:
    return f"observation_date,{series}\n" + "".join(f"{d},{v}\n" for d, v in rows)


def _days_ago(n: int) -> str:
    return (TODAY - dt.timedelta(days=n)).isoformat()


def _monthly(n: int) -> list[tuple[str, str]]:
    """n monthly observations ending 2026-09-01, all 100.0 except the last, 103.0."""
    out, y, m = [], 2026, 9
    for _ in range(n):
        out.append((f"{y:04d}-{m:02d}-01", "100.0"))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    out = out[::-1]
    out[-1] = (out[-1][0], "103.0")
    return out


ALL_OK = {"CPIAUCSL": _csv("CPIAUCSL", _monthly(13))}


def _getter(answers: dict):
    def get(url: str, headers: dict) -> str:
        series = url.split("id=")[1].split("&")[0]
        answer = answers.get(series, _csv(series, [(_days_ago(1), "1.5")]))
        if isinstance(answer, Exception):
            raise answer
        return answer
    return get


def test_daily_series_is_stale_only_past_its_limit():
    testkit.temp_bob()
    for age, stale in ((6, False), (7, False), (8, True)):
        row = macro.read_series("T10Y2Y", TODAY, _getter({"T10Y2Y": _csv("T10Y2Y", [(_days_ago(age), "0.5")])}))
        assert row["stale"] is stale and row["value"] == 0.5, (age, row)


def test_monthly_series_is_not_stale_at_its_normal_age():
    testkit.temp_bob()
    row = macro.read_series("UNRATE", TODAY, _getter({"UNRATE": _csv("UNRATE", [(_days_ago(60), "4.1")])}))
    assert row["stale"] is False and row["value"] == 4.1
    row = macro.read_series("UNRATE", TODAY, _getter({"UNRATE": _csv("UNRATE", [(_days_ago(76), "4.1")])}))
    assert row["stale"] is True


def test_missing_value_rows_are_skipped_not_read_as_gaps():
    testkit.temp_bob()
    text = _csv("VIXCLS", [(_days_ago(3), "18.2"), (_days_ago(2), "."), (_days_ago(1), "")])
    row = macro.read_series("VIXCLS", TODAY, _getter({"VIXCLS": text}))
    assert row["value"] == 18.2 and row["observed"] == _days_ago(3) and row["reason"] is None


def test_only_the_fred_csv_endpoint_is_read():
    testkit.temp_bob()
    try:
        macro._read("X", "https://evil.example/graph/fredgraph.csv?id=X", _getter({}))
    except fetch.FetchError as e:
        assert "refused" in str(e)
    else:
        raise AssertionError("not refused")
    raw, where = macro._read("VIXCLS", macro.CSV_URL.format(series="VIXCLS", start="2025-08-10"), _getter({}))
    assert raw.startswith("observation_date") and where.exists()


def test_unreadable_csv_is_could_not_tell_with_evidence():
    testkit.temp_bob()
    row = macro.read_series("NFCI", TODAY, _getter({"NFCI": "<html>maintenance</html>"}))
    assert row["value"] is None and "unreadable" in row["reason"] and row["evidence"]


def test_cpi_year_on_year_needs_the_same_month_a_year_earlier():
    testkit.temp_bob()
    full = macro.read_series("CPIAUCSL", TODAY, _getter(ALL_OK))
    assert abs(full["value"] - 3.0) < 1e-9 and full["observed"] == "2026-09-01" and full["stale"] is False
    short = macro.read_series("CPIAUCSL", TODAY, _getter({"CPIAUCSL": _csv("CPIAUCSL", _monthly(12))}))
    assert short["value"] is None and "12 months" in short["reason"]


def test_not_found_and_network_errors_are_could_not_tell():
    testkit.temp_bob()
    row = macro.read_series("ICSA", TODAY, _getter({"ICSA": fetch.NotFound("u", 404, "404 not found")}))
    assert row["value"] is None and row["reason"] == "404 not found" and row["evidence"]
    row = macro.read_series("ICSA", TODAY, _getter({"ICSA": fetch.FetchError("u", 503, "HTTP 503")}))
    assert row["value"] is None and "503" in row["reason"]


def test_refresh_writes_every_series_and_fails_naming_the_unread():
    testkit.temp_bob()
    code, message = macro.refresh(NOW, _getter(ALL_OK))
    doc = macro.load()
    assert code == 0 and message.startswith("OK") and set(doc["series"]) == set(macro.SERIES), message
    assert all(pathlib.Path(r["evidence"]).exists() for r in doc["series"].values())
    code, message = macro.refresh(NOW, _getter({**ALL_OK, "VIXCLS": "garbage"}))
    assert code == 1 and message.startswith("FAIL") and "VIXCLS" in message
    assert macro.load()["series"]["VIXCLS"]["value"] is None


def test_lines_say_stale_and_could_not_tell():
    doc = {"series": {
        "VIXCLS": {"what": "VIX close", "value": 18.2, "observed": "2026-10-01", "stale": True,
                   "evidence": "e/1", "reason": None},
        "NFCI": {"what": "x", "value": None, "observed": None, "stale": None, "evidence": None,
                 "reason": "unreadable: y"}}}
    text = "\n".join(macro.lines(doc))
    assert "VIXCLS VIX close: 18.2 on 2026-10-01 STALE [ev:e/1]" in text
    assert "NFCI x: could not tell (unreadable: y)" in text
    assert macro.lines({}) == ["market conditions not refreshed (run macro.py --refresh)"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_macro.py`
Expected: crash on `import macro` (`ModuleNotFoundError: No module named 'macro'`).

- [ ] **Step 3: Write the implementation**

Create `bobs/investor/harness/macro.py`:

```python
#!/usr/bin/env python3
"""Market conditions from FRED: context for the board, the digest and every call, never a score input.

Why context only: a score that moved with the economy would make a March call and an October call
mean different things, and the ledger could no longer say whether the picks or the macro earned a
result. A Trader Bob may later use these values to size positions; the Scout only records them.

Why a staleness limit per series: FRED series come out daily, weekly or monthly, and a value that
stopped updating looks exactly like a current one. Each series has the age past which its latest
observation is late, not just old (a monthly series is normally 4 to 10 weeks old).

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
HEADERS = {"User-Agent": "investor-scout"}
LOOKBACK_DAYS = 430
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


def read_series(series: str, today: dt.date, getter=fetch.get) -> dict:
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


def refresh(now: dt.datetime, getter=fetch.get) -> tuple[int, str]:
    series = {s: read_series(s, now.date(), getter) for s in SERIES}
    edgar.write_json(path(), {"refreshed_at": now.isoformat(timespec="seconds"), "series": series})
    unread = [s for s, r in series.items() if r["value"] is None]
    stale = [s for s, r in series.items() if r["stale"]]
    if unread:
        return 1, f"FAIL: could not tell {', '.join(unread)}; stale={stale}"
    return 0, f"OK series={len(series)} stale={stale}"


def lines(doc: dict) -> list[str]:
    """One line per series for the board and the digest; the only renderer, so both say the same."""
    if not doc.get("series"):
        return ["market conditions not refreshed (run macro.py --refresh)"]
    out = []
    for series, r in doc["series"].items():
        ev = f" [ev:{r['evidence']}]" if r.get("evidence") else ""
        if r.get("value") is None:
            out.append(f"{series} {r['what']}: could not tell ({r['reason']}){ev}")
            continue
        flag = " STALE" if r.get("stale") else ""
        out.append(f"{series} {r['what']}: {r['value']:.4g} on {r['observed']}{flag}{ev}")
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_macro.py`
Expected: `9 of 9 passed`.

- [ ] **Step 5: Break it once, then restore**

In `macro.py` `read_series`, change `> stale_after` to `>= stale_after`. Run `python3 harness/test_macro.py`: `test_daily_series_is_stale_only_past_its_limit` must FAIL. Restore `>`. Then remove `if value.strip() in MISSING: continue` in `parse_csv`: `test_missing_value_rows_are_skipped_not_read_as_gaps` must FAIL. Restore. Re-run: `9 of 9 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/macro.py harness/test_macro.py
git commit -m "Investor Scout: macro.py reads FRED market conditions as evidence

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 2: SIC to sector mapping (`sectors.py`)

**Files:**
- Create: `bobs/investor/harness/sectors.py`
- Test: `bobs/investor/harness/test_sectors.py`

**Interfaces:**
- Produces:
  - `sectors.FF12: tuple[tuple[str, tuple[tuple[int, int], ...]], ...]`
  - `sectors.OTHER = "Other"`
  - `sectors.sector(sic: int | None) -> str`
  - `sectors.is_financial(sic: int | None) -> bool`

- [ ] **Step 1: Save the source definition as evidence**

From `bobs/investor/`:

```bash
python3 -c "import sys; sys.path.insert(0, 'harness'); import scoutpath, fetch; print(fetch.read('ff12-definition', 'https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_12_ind_port.html', {'User-Agent': 'investor-scout'})[1])"
```

Expected: one evidence folder path. Open `raw.txt` in it and compare each range below against the page. If any range differs, use the page's range (the page wins) and note it in the commit message. If the page cannot be read, stop and report; do not write the table from memory alone.

- [ ] **Step 2: Write the failing tests**

Create `bobs/investor/harness/test_sectors.py`:

```python
#!/usr/bin/env python3
"""sectors: Fama-French 12 by SIC range, no code in two sectors, financials are SIC 6000 to 6799.

Run:  python3 harness/test_sectors.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import sectors  # noqa: E402
import testkit  # noqa: E402


def test_known_codes_map_to_their_industry():
    cases = {3674: "BusEq", 7372: "BusEq", 2834: "Hlth", 3841: "Hlth", 1311: "Enrgy", 6022: "Money",
             4911: "Utils", 4813: "Telcm", 5411: "Shops", 2080: "NoDur", 3711: "Durbl", 3560: "Manuf",
             2820: "Chems"}
    for sic, name in cases.items():
        assert sectors.sector(sic) == name, (sic, sectors.sector(sic))


def test_unknown_and_unmapped_codes_are_other():
    assert sectors.sector(None) == "Other"
    assert sectors.sector(9999) == "Other" and sectors.sector(8111) == "Other"


def test_no_code_is_in_two_sectors():
    for sic in range(10000):
        hits = [name for name, ranges in sectors.FF12 if any(lo <= sic <= hi for lo, hi in ranges)]
        assert len(hits) <= 1, (sic, hits)


def test_financial_is_6000_to_6799():
    assert sectors.is_financial(6000) and sectors.is_financial(6022) and sectors.is_financial(6799)
    assert not sectors.is_financial(6800) and not sectors.is_financial(5999)
    assert not sectors.is_financial(3674) and not sectors.is_financial(None)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 harness/test_sectors.py`
Expected: `ModuleNotFoundError: No module named 'sectors'`.

- [ ] **Step 4: Write the implementation**

Create `bobs/investor/harness/sectors.py`. Replace `EVIDENCE_PATH` with the folder Step 1 printed, relative to the evidence dir (`<date>/<id>`):

```python
#!/usr/bin/env python3
"""SIC code to Fama-French 12 industry, and which SIC codes are financial companies.

Why sectors: revenue growth and Piotroski scores differ by industry (a utility growing 6% is fast,
a software firm growing 6% is slow), so ranking them against all stocks rewards the industry, not
the company. Fama-French 12 is a published, fixed mapping: fine enough to separate chipmakers from
food companies, coarse enough that most groups hold dozens of listed names.

Why is_financial: Altman Z was fitted on manufacturers. For banks, insurers and real estate the
balance-sheet ratios it uses mean something else, so its distress veto would only be noise.

Source: Kenneth R. French, "Detail for 12 Industry Portfolios", [ev:EVIDENCE_PATH].

Usage (library; screen.py calls it):
    sectors.sector(3674)        # "BusEq"
    sectors.is_financial(6022)  # True
"""
from __future__ import annotations

OTHER = "Other"
FF12: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("NoDur", ((100, 999), (2000, 2399), (2700, 2749), (2770, 2799), (3100, 3199), (3940, 3989))),
    ("Durbl", ((2500, 2519), (2590, 2599), (3630, 3659), (3710, 3711), (3714, 3714), (3716, 3716),
               (3750, 3751), (3792, 3792), (3900, 3939), (3990, 3999))),
    ("Manuf", ((2520, 2589), (2600, 2699), (2750, 2769), (3000, 3099), (3200, 3569), (3580, 3629),
               (3700, 3709), (3712, 3713), (3715, 3715), (3717, 3749), (3752, 3791), (3793, 3799),
               (3830, 3839), (3860, 3899))),
    ("Enrgy", ((1200, 1399), (2900, 2999))),
    ("Chems", ((2800, 2829), (2840, 2899))),
    ("BusEq", ((3570, 3579), (3660, 3692), (3694, 3699), (3810, 3829), (7370, 7379))),
    ("Telcm", ((4800, 4899),)),
    ("Utils", ((4900, 4949),)),
    ("Shops", ((5000, 5999), (7200, 7299), (7600, 7699))),
    ("Hlth", ((2830, 2839), (3693, 3693), (3840, 3859), (8000, 8099))),
    ("Money", ((6000, 6999),)),
)
FINANCIAL = (6000, 6799)


def sector(sic: int | None) -> str:
    if sic is None:
        return OTHER
    return next((name for name, ranges in FF12 if any(lo <= sic <= hi for lo, hi in ranges)), OTHER)


def is_financial(sic: int | None) -> bool:
    return sic is not None and FINANCIAL[0] <= sic <= FINANCIAL[1]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 harness/test_sectors.py`
Expected: `4 of 4 passed`.

- [ ] **Step 6: Break it once, then restore**

Change BusEq's `(3660, 3692)` to `(3660, 3693)`: `test_no_code_is_in_two_sectors` must FAIL (3693 is also Hlth). Restore. Change `FINANCIAL = (6000, 6799)` to `(6000, 6999)`: `test_financial_is_6000_to_6799` must FAIL. Restore. Re-run: `4 of 4 passed`.

- [ ] **Step 7: Commit**

```bash
git add harness/sectors.py harness/test_sectors.py
git commit -m "Investor Scout: sectors.py maps SIC to Fama-French 12

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 3: SIC codes cached by `edgar.py --sync`

**Files:**
- Modify: `bobs/investor/harness/edgar.py` (constants near line 42, `Client` class, new functions after `sync_frames`, `main` `--sync` branch, module docstring `Usage`)
- Test: `bobs/investor/harness/test_edgar.py` (append)

**Interfaces:**
- Consumes: `fetch.NotFound`, `fetch.Forbidden`, `fetch.FetchError`, `edgar.read_json`, `edgar.write_json`.
- Produces:
  - `edgar.SIC_URL`, `edgar.SIC_REFRESH_DAYS = 90`, `edgar.SIC_CHECKPOINT = 200`
  - `edgar.parse_sic(text: str) -> int | None`
  - `edgar.Client.sic(cik: int) -> tuple[int | None, pathlib.Path]`
  - `edgar.sync_sic(client, sec: pathlib.Path, ciks: set[int], today: dt.date) -> int` (reads made)
  - `edgar.load_sic(sec: pathlib.Path) -> dict[int, int | None]` (`{}` for a missing or torn file)

- [ ] **Step 1: Confirm the Atom feed carries the SIC code (live, one read)**

From `bobs/investor/`:

```bash
python3 -c "import re, sys; sys.path.insert(0, 'harness'); import scoutpath, edgar, scoutcfg; c = edgar.Client(scoutcfg.load()['sec_contact']); raw, where = c._read('sec-sic-check', 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001045810&type=10-K&dateb=&owner=include&count=10&output=atom'); print(where, len(raw), re.findall(r'<assigned-sic>\s*(\d+)', raw))"
```

Expected: an evidence path, a length under about 30000, and `['3674']` (NVIDIA). If the list is empty or the read fails, stop and report: the spec's size reasoning depends on this feed.

- [ ] **Step 2: Write the failing tests**

In `bobs/investor/harness/test_edgar.py`, add `import fetch  # noqa: E402` beside `import edgar  # noqa: E402`, then add before the `if __name__ == "__main__":` block:

```python
ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<company-info>
<assigned-sic>3674</assigned-sic>
<assigned-sic-desc>SEMICONDUCTORS &amp; RELATED DEVICES</assigned-sic-desc>
<cik>0001045810</cik>
</company-info>
</feed>"""
SIC_DAY = dt.date(2026, 10, 14)


class SicClient:
    def __init__(self, answers: dict):
        self.answers = answers

    def sic(self, cik: int):
        answer = self.answers[cik]
        if isinstance(answer, Exception):
            raise answer
        return answer, pathlib.Path(f"ev/sic-{cik}")


def _not_found() -> fetch.NotFound:
    e = fetch.NotFound("u", 404, "404 not found")
    e.evidence = pathlib.Path("ev/404")
    return e


def test_parse_sic_reads_the_assigned_code_or_none():
    assert edgar.parse_sic(ATOM) == 3674
    assert edgar.parse_sic(ATOM.replace(">3674<", "><")) is None
    assert edgar.parse_sic("<feed></feed>") is None


def test_sync_sic_caches_and_refreshes_after_90_days():
    sec = pathlib.Path(tempfile.mkdtemp())
    client = SicClient({1: 3674, 2: 6022})
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY) == 2
    assert edgar.load_sic(sec) == {1: 3674, 2: 6022}
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY + dt.timedelta(days=89)) == 0
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY + dt.timedelta(days=90)) == 2


def test_sync_sic_caches_not_found_but_retries_network_errors():
    sec = pathlib.Path(tempfile.mkdtemp())
    client = SicClient({1: _not_found(), 2: fetch.FetchError("u", 503, "HTTP 503")})
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY) == 1
    assert edgar.load_sic(sec) == {1: None}
    client.answers[2] = 3674
    assert edgar.sync_sic(client, sec, {1, 2}, SIC_DAY) == 1
    assert edgar.load_sic(sec) == {1: None, 2: 3674}


def test_sync_sic_stops_on_forbidden_and_keeps_what_it_read():
    sec = pathlib.Path(tempfile.mkdtemp())
    client = SicClient({1: 3674, 2: fetch.Forbidden("u", 403, "403 forbidden")})
    try:
        edgar.sync_sic(client, sec, {1, 2}, SIC_DAY)
    except fetch.Forbidden:
        pass
    else:
        raise AssertionError("403 must stop the run")
    assert edgar.load_sic(sec) == {1: 3674}


def test_load_sic_of_a_torn_file_is_empty():
    sec = pathlib.Path(tempfile.mkdtemp())
    (sec / "sic.json").write_text("{torn", encoding="utf-8")
    assert edgar.load_sic(sec) == {}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 harness/test_edgar.py`
Expected: the five new tests FAIL with `AttributeError: module 'edgar' has no attribute 'parse_sic'` (or `sync_sic` / `load_sic`); the existing tests still PASS.

- [ ] **Step 4: Write the implementation**

In `edgar.py`, after `SUBMISSIONS_URL = ...` (line 42) add:

```python
SIC_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}"
           "&type=10-K&dateb=&owner=include&count=10&output=atom")
```

After `MONTH_FILING_LAG_DAYS = 5` add:

```python
SIC_REFRESH_DAYS = 90
SIC_CHECKPOINT = 200
SIC_TAG = re.compile(r"<assigned-sic>\s*(\d+)\s*</assigned-sic>")
```

After `parse_owner_filings` add:

```python
def parse_sic(text: str) -> int | None:
    """The company's SIC code from its EDGAR Atom feed. Blank or missing is None (SEC leaves it
    empty for some funds and shells), never 0, which would read as an industry."""
    m = SIC_TAG.search(text)
    return int(m.group(1)) if m else None
```

In `class Client`, after `owner_filings` add:

```python
    def sic(self, cik: int) -> tuple[int | None, pathlib.Path]:
        raw, where = self._read(f"sec-sic-{cik}", SIC_URL.format(cik=cik))
        return parse_sic(raw), where
```

After `sync_frames` (before `sync_form4` or wherever the next top-level function starts) add:

```python
def sync_sic(client, sec: pathlib.Path, ciks: set[int], today: dt.date) -> int:
    """Each company's SIC code, read once and re-read after SIC_REFRESH_DAYS. A 404 is cached as
    None (SEC has no code for it); a network error is not cached, so the next run asks again; a 403
    stops the run. The cache is written every SIC_CHECKPOINT reads and on the way out: the first
    sync is about 5,000 reads, and a crash must not throw them away."""
    path = sec / "sic.json"
    cache = read_json(path, {})
    read = 0
    try:
        for cik in sorted(ciks):
            row = cache.get(str(cik))
            if row and (today - dt.date.fromisoformat(row["read"])).days < SIC_REFRESH_DAYS:
                continue
            try:
                code, where = client.sic(cik)
            except fetch.Forbidden:
                raise
            except fetch.NotFound as e:
                code, where = None, e.evidence
            except fetch.FetchError:
                continue
            cache[str(cik)] = {"sic": code, "read": today.isoformat(), "evidence": str(where)}
            read += 1
            if read % SIC_CHECKPOINT == 0:
                write_json(path, cache)
    finally:
        write_json(path, cache)
    return read


def load_sic(sec: pathlib.Path) -> dict[int, int | None]:
    doc = read_json(sec / "sic.json", {})
    return {int(k): v.get("sic") for k, v in doc.items()} if isinstance(doc, dict) else {}
```

In `main`, replace the `if a.sync:` block with:

```python
        if a.sync:
            rows = sync_tickers(client, sec, today)
            frames = sync_frames(client, sec, today, cfg["frames_refresh_days"])
            sic = sync_sic(client, sec, {r["cik"] for r in rows}, today)
            days = sync_days(read_json(sec / "form4" / "coverage.json", []), today)
            form4 = sync_form4(client, sec, days, {r["cik"] for r in rows}, today)
            print(f"OK tickers={len(rows)} frames_fetched={frames} sic_read={sic} form4={form4}")
            return 0
```

In the module docstring `Usage:` block, change the `--sync` line's comment to `# tickers, frames, SIC codes, last 7 days of Form 4s`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 harness/test_edgar.py`
Expected: all PASS, including the five new ones.

- [ ] **Step 6: Break it once, then restore**

Change `except fetch.FetchError:\n                continue` to cache the error (replace `continue` with `code, where = None, None`): `test_sync_sic_caches_not_found_but_retries_network_errors` must FAIL. Restore. Change `< SIC_REFRESH_DAYS` to `<= SIC_REFRESH_DAYS`: `test_sync_sic_caches_and_refreshes_after_90_days` must FAIL. Restore. Re-run: all PASS.

- [ ] **Step 7: Commit**

```bash
git add harness/edgar.py harness/test_edgar.py
git commit -m "Investor Scout: cache each company's SIC code during edgar --sync

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 4: New `[scout]` keys and scorecard version 2

**Files:**
- Modify: `bobs/investor/bob.toml` (`[scout]`)
- Modify: `bobs/investor/harness/scoutcfg.py` (`COUNTS`, `validate`)
- Modify: `bobs/investor/harness/screen.py:43` (`VERSIONED`)
- Test: `bobs/investor/harness/test_scoutcfg.py` (append), `bobs/investor/harness/test_screen.py` (`test_changed_weights_need_a_new_scorecard_version`, plus one new test)

**Interfaces:**
- Produces: `cfg["min_sector_size"]: int` (15), `cfg["min_agreeing_groups"]: int` (3, at most `len(scoutcfg.GROUPS)`), `cfg["scorecard_version"] == 2`.

- [ ] **Step 1: Write the failing tests**

Append to `test_scoutcfg.py` (before the `__main__` block):

```python
def test_agreement_above_the_group_count_refused():
    scout = _scout()
    scout["min_agreeing_groups"] = 6
    assert any("min_agreeing_groups" in e for e in scoutcfg.validate(scout))
    scout["min_agreeing_groups"] = 5
    assert scoutcfg.validate(scout) == []


def test_sector_size_must_be_a_positive_integer():
    scout = _scout()
    scout["min_sector_size"] = 0
    assert any("min_sector_size" in e for e in scoutcfg.validate(scout))
    del scout["min_sector_size"]
    assert any("min_sector_size" in e for e in scoutcfg.validate(scout))
```

In `test_screen.py`, replace `test_changed_weights_need_a_new_scorecard_version` with:

```python
def test_changed_weights_need_a_new_scorecard_version():
    bob = testkit.temp_bob(LOWER_FLOOR)
    _world(bob)
    assert screen.run(NOW)[0] == 0
    manifest = bob / "bob.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("growth = 0.30", "growth = 0.25")
                        .replace("surprise = 0.15", "surprise = 0.20"), encoding="utf-8")
    bobcfg.config.cache_clear()
    code, message = screen.run(NOW)
    assert code == 1 and "scorecard_version" in message, message
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("scorecard_version = 2", "scorecard_version = 3"),
                        encoding="utf-8")
    bobcfg.config.cache_clear()
    assert screen.run(NOW)[0] == 0


def test_changed_agreement_rule_needs_a_new_scorecard_version():
    bob = testkit.temp_bob(LOWER_FLOOR)
    _world(bob)
    assert screen.run(NOW)[0] == 0
    manifest = bob / "bob.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("min_agreeing_groups = 3", "min_agreeing_groups = 2"),
                        encoding="utf-8")
    bobcfg.config.cache_clear()
    code, message = screen.run(NOW)
    assert code == 1 and "scorecard_version" in message, message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_scoutcfg.py` then `python3 harness/test_screen.py`
Expected: both new `test_scoutcfg.py` tests FAIL (neither key is checked yet); in `test_screen.py` both version tests FAIL with `AssertionError: 'scorecard_version = 2' is not in the manifest` / `'min_agreeing_groups = 3' is not in the manifest`.

- [ ] **Step 3: Write the implementation**

In `bob.toml` `[scout]`, after `altman_veto = 1.8` add:

```toml
min_sector_size = 15
min_agreeing_groups = 3
```

and change `scorecard_version = 1` to `scorecard_version = 2`.

In `scoutcfg.py`, extend `COUNTS`:

```python
COUNTS = ("top_n", "max_new_theses_per_day", "story_max_per_week", "scorecard_version",
          "frames_refresh_days", "min_sector_size", "min_agreeing_groups")
```

and in `validate`, after the `errors += [... for k in COUNTS ...]` line add:

```python
    agree = scout.get("min_agreeing_groups")
    if isinstance(agree, int) and not isinstance(agree, bool) and agree > len(GROUPS):
        errors.append(f"scout.min_agreeing_groups: at most {len(GROUPS)}, the number of groups")
```

In `screen.py`, change line 43 to:

```python
VERSIONED = ("weights", "buy_percentile", "min_coverage", "altman_veto", "min_sector_size", "min_agreeing_groups")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_scoutcfg.py` and `python3 harness/test_screen.py`
Expected: all PASS.

- [ ] **Step 5: Break it once, then restore**

Remove `"min_agreeing_groups"` from `VERSIONED`: `test_changed_agreement_rule_needs_a_new_scorecard_version` must FAIL. Restore. Re-run both suites: all PASS.

- [ ] **Step 6: Commit**

```bash
git add bob.toml harness/scoutcfg.py harness/screen.py harness/test_scoutcfg.py harness/test_screen.py
git commit -m "Investor Scout: min_sector_size and min_agreeing_groups; scorecard v2

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 5: Sector-relative ranks and the agreement gate (`scorecard.py`)

**Files:**
- Modify: `bobs/investor/harness/scorecard.py` (docstring, new `SECTOR_SIGNALS`, new `peer_ranks`, `build`, `gate`)
- Test: `bobs/investor/harness/test_scorecard.py`

**Interfaces:**
- Consumes: `sectors.OTHER` is not needed; a ticker absent from the `sectors` mapping is ranked market-wide.
- Produces:
  - `scorecard.SECTOR_SIGNALS = ("ttm_growth", "acceleration", "piotroski")`
  - `scorecard.peer_ranks(values: dict[str, float | None], sectors: dict[str, str], min_size: int) -> tuple[dict[str, float | None], dict[str, str]]` (percentiles, peer label per ticker: sector name or `"market"`)
  - `scorecard.build(raw, weights, min_coverage, sectors: dict[str, str] | None = None, min_sector_size: int = 15) -> dict[str, dict]`; each card gains `sector: str | None`, `agreeing: int`, `known_groups: int`; each signal gains `peer: str`.
  - `scorecard.gate(card, altman, cfg) -> tuple[str, str]`; `cfg` now needs `min_agreeing_groups`.

- [ ] **Step 1: Write the failing tests**

In `test_scorecard.py`, change `CFG` to:

```python
CFG = {"min_coverage": 0.70, "altman_veto": 1.8, "buy_percentile": 80, "min_agreeing_groups": 3}
```

Replace `test_gate_order` and `test_gate_accepts_coverage_exactly_at_the_floor` with:

```python
def test_gate_order():
    card = {"coverage": 1.0, "rank_pct": 90.0, "score": 70.0, "agreeing": 3, "known_groups": 4}
    assert scorecard.gate(card, 3.0, CFG)[0] == "eligible"
    assert scorecard.gate(card, None, CFG)[0] == "eligible"
    assert scorecard.gate(card, 1.2, CFG) == ("watch", "Altman Z 1.20 below 1.8")
    assert scorecard.gate({**card, "rank_pct": 79.9}, 3.0, CFG)[0] == "pass"
    assert scorecard.gate({**card, "coverage": 0.65, "rank_pct": None}, 3.0, CFG)[0] == "could not tell"


def test_agreement_only_demotes_names_that_would_be_eligible():
    card = {"coverage": 1.0, "rank_pct": 90.0, "score": 70.0, "agreeing": 2, "known_groups": 4}
    assert scorecard.gate(card, 3.0, CFG) == ("watch", "2 of 4 known groups above median, 3 needed")
    assert scorecard.gate({**card, "rank_pct": 10.0, "agreeing": 0}, 3.0, CFG)[0] == "pass"


def test_gate_accepts_coverage_exactly_at_the_floor():
    card = {"coverage": 0.30 + 0.20 + 0.20, "rank_pct": 95.0, "score": 80.0, "agreeing": 3, "known_groups": 3}
    assert scorecard.gate(card, None, CFG)[0] == "eligible"
```

Add before the `__main__` block:

```python
def test_peer_ranks_inside_big_enough_sectors_only():
    values = {"T1": 1.0, "T2": 2.0, "B1": 10.0, "B2": 20.0}
    sectors = {"T1": "BusEq", "T2": "BusEq", "B1": "Money", "B2": "Money"}
    pcts, peers = scorecard.peer_ranks(values, sectors, 2)
    assert pcts == {"T1": 0.0, "T2": 100.0, "B1": 0.0, "B2": 100.0}
    assert peers == {"T1": "BusEq", "T2": "BusEq", "B1": "Money", "B2": "Money"}
    pcts, peers = scorecard.peer_ranks(values, sectors, 3)
    assert abs(pcts["T2"] - 100 / 3) < 1e-9 and abs(pcts["B1"] - 200 / 3) < 1e-9
    assert set(peers.values()) == {"market"}


def test_sector_size_counts_known_values_only():
    values = {"T1": 1.0, "T2": 2.0, "T3": None, "X": 5.0}
    sectors = {"T1": "BusEq", "T2": "BusEq", "T3": "BusEq", "X": "Money"}
    pcts, peers = scorecard.peer_ranks(values, sectors, 3)
    assert peers["T1"] == "market" and pcts["T2"] == 50.0 and pcts["T3"] is None


def test_a_ticker_without_a_sector_ranks_market_wide():
    values = {"T1": 1.0, "T2": 2.0, "U": 3.0}
    pcts, peers = scorecard.peer_ranks(values, {"T1": "BusEq", "T2": "BusEq"}, 2)
    assert peers == {"T1": "BusEq", "T2": "BusEq", "U": "market"} and pcts["U"] == 100.0


def test_build_ranks_growth_and_quality_by_sector_and_the_rest_by_market():
    raw = {"A": _raw(1), "B": _raw(2), "C": _raw(3)}
    cards = scorecard.build(raw, WEIGHTS, 0.70, {"A": "BusEq", "B": "BusEq", "C": "Money"}, 2)
    assert cards["A"]["signals"]["ttm_growth"]["peer"] == "BusEq"
    assert cards["A"]["signals"]["piotroski"]["peer"] == "BusEq"
    assert cards["A"]["signals"]["momentum_12_1"]["peer"] == "market"
    assert cards["C"]["signals"]["ttm_growth"]["peer"] == "market"
    assert cards["B"]["signals"]["ttm_growth"]["pct"] == 100.0 and cards["B"]["sector"] == "BusEq"


def test_build_counts_agreeing_and_known_groups():
    raw = {"A": _raw(1), "B": _raw(2), "C": _raw(3, only=("ttm_growth", "acceleration", "momentum_12_1",
                                                         "high_proximity", "eps_sue", "revenue_sue"))}
    cards = scorecard.build(raw, WEIGHTS, 0.70)
    assert (cards["A"]["agreeing"], cards["A"]["known_groups"]) == (0, 5)
    assert (cards["C"]["agreeing"], cards["C"]["known_groups"]) == (3, 3)
    assert cards["A"]["sector"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_scorecard.py`
Expected: `test_gate_order`, `test_agreement_only_demotes_...` and the five new tests FAIL (`KeyError`, `AttributeError: ... peer_ranks`, or wrong gate).

- [ ] **Step 3: Write the implementation**

In `scorecard.py`, replace the paragraph `Gate order (the spec's): coverage floor, then the Altman distress veto, then the top-20% rank.` with:

```
Why growth and quality rank inside the sector: they differ by industry, so a market-wide rank
rewards the industry, not the company. Momentum, surprise and insider stay market-wide: beating the
market is what a call claims, and SUE is already scaled by each company's own history. A sector
with fewer known values than min_sector_size ranks market-wide; each signal records which.

Gate order: coverage floor, the Altman distress veto, the top-20% rank, then the agreement rule
(at least min_agreeing_groups known groups above the 50th percentile). Agreement comes last so it
only demotes names that would otherwise be eligible.
```

After `SIGNALS = [...]` add:

```python
SECTOR_SIGNALS = ("ttm_growth", "acceleration", "piotroski")
MARKET = "market"
```

After `percentile_ranks` add:

```python
def peer_ranks(values: dict[str, float | None], sectors: dict[str, str],
               min_size: int) -> tuple[dict[str, float | None], dict[str, str]]:
    market = percentile_ranks(values)
    members: dict[str, dict[str, float | None]] = {}
    for ticker, value in values.items():
        if ticker in sectors:
            members.setdefault(sectors[ticker], {})[ticker] = value
    pcts, peers = dict(market), dict.fromkeys(values, MARKET)
    for name, group in members.items():
        if sum(v is not None for v in group.values()) < min_size:
            continue
        pcts.update(percentile_ranks(group))
        peers.update(dict.fromkeys(group, name))
    return pcts, peers
```

Replace `build` with:

```python
def build(raw: dict[str, dict[str, dict]], weights: dict[str, float], min_coverage: float,
          sectors: dict[str, str] | None = None, min_sector_size: int = 15) -> dict[str, dict]:
    sectors = sectors or {}
    pcts, peers = {}, {}
    for s in SIGNALS:
        values = {t: raw[t].get(s, {}).get("value") for t in raw}
        if s in SECTOR_SIGNALS:
            pcts[s], peers[s] = peer_ranks(values, sectors, min_sector_size)
        else:
            pcts[s], peers[s] = percentile_ranks(values), dict.fromkeys(raw, MARKET)
    cards = {}
    for ticker, signals in raw.items():
        sig = {s: {"value": signals.get(s, {}).get("value"), "pct": pcts[s][ticker], "peer": peers[s][ticker],
                   "evidence": signals.get(s, {}).get("evidence", [])} for s in SIGNALS}
        groups = {}
        for group, names in GROUP_SIGNALS.items():
            known = [sig[s]["pct"] for s in names if sig[s]["pct"] is not None]
            groups[group] = sum(known) / len(known) if known else None
        have = {g: w for g, w in weights.items() if groups[g] is not None}
        coverage = round(sum(have.values()), 9)
        score = sum(groups[g] * w for g, w in have.items()) / coverage if coverage > 0 else None
        known_groups = [v for v in groups.values() if v is not None]
        cards[ticker] = {"score": score, "coverage": coverage, "groups": groups, "signals": sig,
                         "sector": sectors.get(ticker), "agreeing": sum(v > 50 for v in known_groups),
                         "known_groups": len(known_groups)}
    ranks = percentile_ranks({t: c["score"] for t, c in cards.items() if c["coverage"] >= min_coverage})
    for ticker, card in cards.items():
        card["rank_pct"] = ranks.get(ticker)
    return cards
```

Replace `gate` with:

```python
def gate(card: dict, altman: float | None, cfg: dict) -> tuple[str, str]:
    if round(card["coverage"], 9) < cfg["min_coverage"]:
        return "could not tell", f"coverage {card['coverage']:.0%} below {cfg['min_coverage']:.0%}"
    if altman is not None and altman < cfg["altman_veto"]:
        return "watch", f"Altman Z {altman:.2f} below {cfg['altman_veto']}"
    if card["rank_pct"] is None or card["rank_pct"] < cfg["buy_percentile"]:
        return "pass", f"score rank {card['rank_pct'] or 0:.0f} below {cfg['buy_percentile']}"
    if card["agreeing"] < cfg["min_agreeing_groups"]:
        return "watch", (f"{card['agreeing']} of {card['known_groups']} known groups above median, "
                         f"{cfg['min_agreeing_groups']} needed")
    return "eligible", f"score rank {card['rank_pct']:.0f}, coverage {card['coverage']:.0%}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_scorecard.py`
Expected: all PASS.

- [ ] **Step 5: Break it once, then restore**

In `peer_ranks`, change `< min_size` to `< 0` (every sector ranks inside): `test_peer_ranks_inside_big_enough_sectors_only` must FAIL. Restore. Move the agreement `if` above the rank `if` in `gate`: `test_agreement_only_demotes_names_that_would_be_eligible` must FAIL. Restore. Re-run: all PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/scorecard.py harness/test_scorecard.py
git commit -m "Investor Scout: rank growth and quality within sector; agreement gate

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 6: Screen wiring: sectors, Altman for financials, card text (`screen.py`)

**Files:**
- Modify: `bobs/investor/harness/screen.py` (imports, new `altman_for`, `_run`, `card_text`)
- Test: `bobs/investor/harness/test_screen.py`

**Interfaces:**
- Consumes: `edgar.load_sic(sec)`, `sectors.sector`, `sectors.is_financial`, `scorecard.build(..., sectors, min_sector_size)`.
- Produces:
  - `screen.altman_for(current, market_cap: float, sic: int | None) -> tuple[float | None, str | None]`
  - each screen card gains `sic: int | None`, `altman_reason: str | None` (plus Task 5's `sector`, `agreeing`, `known_groups`, per-signal `peer`).

- [ ] **Step 1: Write the failing tests**

In `test_screen.py` add `import dataclasses` to the imports. Add before the `__main__` block:

```python
def _sic(state: pathlib.Path, codes: dict[int, int | None]) -> None:
    _put(state / "sec" / "sic.json", {str(cik): {"sic": code, "read": "2026-10-13", "evidence": "ev/sic"}
                                      for cik, code in codes.items()})


def test_without_sic_codes_everything_ranks_market_wide():
    _world(testkit.temp_bob(LOWER_FLOOR))
    assert screen.run(NOW)[0] == 0
    card = screen.latest()["cards"]["CCC"]
    assert card["sector"] is None and card["sic"] is None and card["signals"]["ttm_growth"]["peer"] == "market"


def test_small_sector_falls_back_to_market_and_big_enough_ranks_inside():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    _sic(state, {1: 3674, 2: 3674, 3: 3674})
    assert screen.run(NOW)[0] == 0
    card = screen.latest()["cards"]["CCC"]
    assert card["sector"] == "BusEq" and card["sic"] == 3674 and card["signals"]["ttm_growth"]["peer"] == "market"
    state = _world(testkit.temp_bob({**LOWER_FLOOR, "min_sector_size = 15": "min_sector_size = 3"}))
    _sic(state, {1: 3674, 2: 3674, 3: 3674})
    assert screen.run(NOW)[0] == 0
    card = screen.latest()["cards"]["CCC"]
    assert card["signals"]["ttm_growth"]["peer"] == "BusEq" and card["signals"]["momentum_12_1"]["peer"] == "market"


def test_corrupt_sic_cache_is_ignored_not_fatal():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    (state / "sec" / "sic.json").write_text("{torn", encoding="utf-8")
    code, message = screen.run(NOW)
    assert code == 0, message
    assert screen.latest()["cards"]["CCC"]["sector"] is None


def test_financials_get_no_altman_and_others_keep_the_veto():
    @dataclasses.dataclass
    class Facts:
        market_cap: float | None = None

    real = screen.quality.altman_z_score
    screen.quality.altman_z_score = lambda facts: 1.2
    try:
        assert screen.altman_for(Facts(), 1e9, 6022) == (None, "not valid for financials")
        assert screen.altman_for(Facts(), 1e9, 3674) == (1.2, None)
        assert screen.altman_for(Facts(), 1e9, None) == (1.2, None)
        assert screen.altman_for(None, 1e9, 3674) == (None, "no annual balance sheet")
    finally:
        screen.quality.altman_z_score = real
```

In `test_card_text_names_gate_and_evidence`, add after the existing assert:

```python
    assert "vs market" in text and "known groups above median" in text and "sector unknown" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_screen.py`
Expected: the four new tests and `test_card_text_names_gate_and_evidence` FAIL (`KeyError: 'sic'`, `AttributeError: ... altman_for`, missing text).

- [ ] **Step 3: Write the implementation**

In `screen.py` imports add `import sectors  # noqa: E402` (alphabetical, after `import scorecard`).

After `passes_floors` add:

```python
def altman_for(current, market_cap: float, sic: int | None) -> tuple[float | None, str | None]:
    """Altman Z, or None with the reason. Financials get None: the model was fitted on
    manufacturers, and a bank's balance sheet would trip the distress veto for being a bank."""
    if sectors.is_financial(sic):
        return None, "not valid for financials"
    if current is None:
        return None, "no annual balance sheet"
    return quality.altman_z_score(dataclasses.replace(current, market_cap=market_cap)), None
```

In `_run`:

1. After `store = insider.load_store(sec)` add `sic = edgar.load_sic(sec)` and change `raw, info, unpriced, stale, no_shares = {}, {}, 0, 0, 0` to `raw, info, sector_of, unpriced, stale, no_shares = {}, {}, {}, 0, 0, 0`.
2. Replace `altman = quality.altman_z_score(dataclasses.replace(current, market_cap=cap)) if current else None` with:

```python
        altman, altman_reason = altman_for(current, cap, sic.get(c["cik"]))
        if c["cik"] in sic:
            sector_of[c["ticker"]] = sectors.sector(sic[c["cik"]])
```

3. Replace the `info[c["ticker"]] = {...}` assignment with:

```python
        info[c["ticker"]] = {"name": c["name"], "cik": c["cik"], "symbol": c["symbol"], "close": view["close"],
                             "market_cap": cap, "altman": altman, "altman_reason": altman_reason,
                             "sic": sic.get(c["cik"]), "shares_evidence": shares_ev}
```

4. Replace `cards = scorecard.build(raw, cfg["weights"], cfg["min_coverage"])` with:

```python
    cards = scorecard.build(raw, cfg["weights"], cfg["min_coverage"], sector_of, cfg["min_sector_size"])
```

Replace `card_text` with:

```python
def card_text(ticker: str, doc: dict) -> str:
    card = doc.get("cards", {}).get(ticker)
    if card is None:
        return f"{ticker}: not scored in session {doc.get('session')}"
    fmt = lambda v: "could not tell" if v is None else f"{v:.4g}"  # noqa: E731
    altman = fmt(card["altman"]) + (f" ({card['altman_reason']})" if card.get("altman_reason") else "")
    lines = [f"{ticker} ({card['name']}) session {doc['session']}, scorecard v{doc['scorecard_version']}",
             f"gate: {card['gate']} ({card['reason']})",
             f"score {fmt(card['score'])}, rank {fmt(card['rank_pct'])}, coverage {card['coverage']:.0%}, "
             f"Altman Z {altman}, market cap {fmt(card['market_cap'])} [ev:{card['shares_evidence']}]",
             f"sector {card.get('sector') or 'unknown'}: {card['agreeing']} of {card['known_groups']} "
             f"known groups above median"]
    for group, names in scorecard.GROUP_SIGNALS.items():
        lines.append(f"{group}: {fmt(card['groups'][group])}")
        for name in names:
            s = card["signals"][name]
            lines.append(f"  {name}: value {fmt(s['value'])}, pct {fmt(s['pct'])} vs {s['peer']} "
                         + " ".join(f"[ev:{e}]" for e in s["evidence"]))
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_screen.py`
Expected: all PASS (the existing `test_run_scores_ranks_and_applies_floors` still sees CCC eligible: 3 of 3 known groups above 50).

- [ ] **Step 5: Break it once, then restore**

In `altman_for`, delete the `if sectors.is_financial(sic):` block: `test_financials_get_no_altman_and_others_keep_the_veto` must FAIL. Restore. Re-run: all PASS.

- [ ] **Step 6: Run every suite**

Run: `./selftest.sh`
Expected: every line `ok`. `test_calls.py`, `test_scout_board.py` and `test_digest.py` fixtures do not hold the new card keys; if one fails, it is reading a key this task added. Fix it by reading with `.get()` in the module, not by changing the fixture.

- [ ] **Step 7: Commit**

```bash
git add harness/screen.py harness/test_screen.py
git commit -m "Investor Scout: screen uses sectors and exempts financials from Altman

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 7: Full scorecard, market conditions and screen hash on each call (`calls.py`)

**Files:**
- Modify: `bobs/investor/harness/calls.py` (docstring, imports, the `new.append({"kind": "call", ...})` in `record_verdict`)
- Test: `bobs/investor/harness/test_calls.py`

**Interfaces:**
- Consumes: `macro.load() -> dict`, `screen.screen_dir()`.
- Produces: call lines gain `card: dict` (the screen card as read), `macro: dict` (`{}` when never refreshed), `screen_sha256: str`.

- [ ] **Step 1: Write the failing tests**

In `test_calls.py` add `import hashlib` to the imports. Add before the `__main__` block:

```python
def test_call_carries_its_card_market_conditions_and_screen_hash():
    bob = _bob()
    state = bob / "data" / "state"
    _put(state / "macro.json", {"refreshed_at": "2026-10-13T05:00:00+00:00", "series": {"VIXCLS": {"value": 18.2}}})
    call = calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)[1]
    latest = state / "screen" / "latest.json"
    assert call["card"] == json.loads(latest.read_text(encoding="utf-8"))["cards"]["NVDA"]
    assert call["macro"]["series"]["VIXCLS"]["value"] == 18.2
    assert call["screen_sha256"] == hashlib.sha256(latest.read_bytes()).hexdigest()
    assert calls.read()[0][1]["card"] == call["card"]


def test_call_without_market_conditions_records_them_as_empty():
    _bob()
    call = calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)[1]
    assert call["macro"] == {} and call["card"]["gate"] == "eligible"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_calls.py`
Expected: both new tests FAIL with `KeyError: 'card'`.

- [ ] **Step 3: Write the implementation**

In `calls.py` imports add `import macro  # noqa: E402` (after `import fetch`). In the docstring, after the paragraph ending `...so dividends and splits never skew a result.`, add:

```
A call also carries the full scorecard it passed, the market conditions when it was made and the
hash of the screen file it was read from: a re-run of the same session rewrites the screen, and the
call must still say exactly what it stood on.
```

In `record_verdict`, replace the `new.append({"kind": "call", ...})` call with:

```python
            new.append({"kind": "call", "id": f"{ticker}-{session}-{number}", "ticker": ticker,
                        "symbol": card["symbol"], "lane": lane, "session": session, "at": at,
                        "scorecard_version": doc["scorecard_version"], "score": card["score"],
                        "coverage": card["coverage"], "rank_pct": card["rank_pct"],
                        "thesis": f"theses/{ticker}.md",
                        "thesis_sha256": hashlib.sha256(thesis_path.read_bytes()).hexdigest(), "source": source,
                        "card": card, "macro": macro.load(),
                        "screen_sha256": hashlib.sha256((screen.screen_dir() / "latest.json").read_bytes()).hexdigest()})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_calls.py`
Expected: all PASS.

- [ ] **Step 5: Break it once, then restore**

Remove `"card": card, ` from the call line: `test_call_carries_its_card_market_conditions_and_screen_hash` must FAIL. Restore. Re-run: all PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/calls.py harness/test_calls.py
git commit -m "Investor Scout: each call stores its scorecard, macro and screen hash

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 8: Attribution by group and sector (`ledger_score.py --by-signal`)

**Files:**
- Modify: `bobs/investor/harness/ledger_score.py` (docstring `Usage`, imports, `score_call`, new `by_signal`, `run`, `main`)
- Test: `bobs/investor/harness/test_ledger_score.py`

**Interfaces:**
- Consumes: call lines with optional `card` (`card["groups"]: dict[str, float | None]`, `card["sector"]`), `scoutcfg.GROUPS`, existing `summarize`.
- Produces:
  - scored rows gain `groups_agreeing: list[str] | None` (None for calls without a card) and `sector: str | None`
  - `ledger_score.by_signal(rows: list[dict]) -> dict | None` (`{"calls": n, "by_group": {g: {"agreed": summary, "not": summary}}, "by_sector": {name: summary}}`)
  - `ledger_results.json` gains `"by_signal"`
  - CLI `--by-signal`: `OK by-signal calls=N` then the split as JSON, or `FAIL: ...` and exit 1 below `MIN_SCORED` carded calls

- [ ] **Step 1: Write the failing tests**

In `test_ledger_score.py` add before the `__main__` block:

```python
def _carded(n: int, groups: list[str], sector: str = "BusEq", excess: float = 0.1) -> list[dict]:
    return [{"groups_agreeing": groups, "sector": sector,
             "horizons": {"3": {"status": "scored", "excess_spy": excess, "excess_qqq": excess},
                          "6": {"status": "pending"}, "12": {"status": "pending"}}} for _ in range(n)]


def test_by_signal_needs_twenty_carded_calls():
    old = {"groups_agreeing": None, "sector": None, "horizons": {}}
    assert ls.by_signal(_carded(19, ["growth"]) + [old]) is None
    assert ls.by_signal(_carded(20, ["growth"]))["calls"] == 20


def test_by_signal_splits_by_group_and_sector():
    rows = _carded(10, ["growth", "momentum"], "BusEq", 0.2) + _carded(10, ["momentum"], "Money", -0.1)
    split = ls.by_signal(rows)
    growth = split["by_group"]["growth"]
    assert growth["agreed"]["3"]["n"] == 10 and abs(growth["agreed"]["3"]["avg_excess_spy"] - 0.2) < 1e-12
    assert growth["not"]["3"]["n"] == 10 and abs(growth["not"]["3"]["avg_excess_spy"] + 0.1) < 1e-12
    assert split["by_group"]["momentum"]["not"]["3"]["n"] == 0
    assert split["by_sector"]["Money"]["3"]["n"] == 10 and set(split["by_sector"]) == {"BusEq", "Money"}


def test_old_calls_without_a_card_score_as_before():
    row = ls.score_call(CALL, _series(), {"PICK"}, "2026-07-31", set())
    assert row["groups_agreeing"] is None and row["sector"] is None
    assert row["horizons"]["3"]["status"] == "scored"


def test_card_groups_above_median_are_recorded():
    call = {**CALL, "card": {"groups": {"growth": 80.0, "momentum": 50.0, "insider": None, "quality": 51.0},
                             "sector": "BusEq"}}
    row = ls.score_call(call, _series(), {"PICK"}, "2026-07-31", set())
    assert row["groups_agreeing"] == ["growth", "quality"] and row["sector"] == "BusEq"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_ledger_score.py`
Expected: the four new tests FAIL (`AttributeError: ... by_signal`, `KeyError: 'groups_agreeing'`).

- [ ] **Step 3: Write the implementation**

In `ledger_score.py`:

1. Docstring `Usage:` block becomes:

```
Usage:
    python3 harness/ledger_score.py --run
    python3 harness/ledger_score.py --by-signal   # excess return split by agreeing group and sector
```

2. Add `import json` to the stdlib imports.
3. In `score_call`, right after the `row.update({...})` line, add:

```python
    card = call.get("card") or {}
    groups = card.get("groups")
    row["groups_agreeing"] = (None if groups is None
                              else sorted(g for g, v in groups.items() if v is not None and v > 50))
    row["sector"] = card.get("sector")
```

4. After `summarize` add:

```python
def by_signal(rows: list[dict]) -> dict | None:
    """Excess return split by each group (agreed or not when the call was made) and by sector.
    None below MIN_SCORED calls that carry a card: a split of a handful of calls is noise that
    looks like a finding."""
    carded = [r for r in rows if r.get("groups_agreeing") is not None]
    if len(carded) < MIN_SCORED:
        return None
    names = sorted({r["sector"] or "unknown" for r in carded})
    return {"calls": len(carded),
            "by_group": {g: {"agreed": summarize([r for r in carded if g in r["groups_agreeing"]]),
                             "not": summarize([r for r in carded if g not in r["groups_agreeing"]])}
                         for g in scoutcfg.GROUPS},
            "by_sector": {n: summarize([r for r in carded if (r["sector"] or "unknown") == n]) for n in names}}
```

5. In `run`, add `"by_signal": by_signal(scored),` to `doc` after the `"by_lane": ...` entry.
6. Replace `main` with:

```python
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--by-signal", action="store_true")
    a = ap.parse_args(argv)
    doc = run(fetch.utcnow())
    if a.by_signal:
        split = doc["by_signal"]
        if split is None:
            carded = sum(c.get("groups_agreeing") is not None for c in doc["calls"])
            print(f"FAIL: {carded} calls carry a scorecard; {MIN_SCORED} needed before a split means anything")
            return 1
        print(f"OK by-signal calls={split['calls']}")
        print(json.dumps(split, indent=1, sort_keys=True))
        return 0
    twelve = doc["summary"]["12"]
    print(f"OK calls={doc['n_calls']} scored_12m={twelve['n']} torn={doc['torn_lines']}")
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_ledger_score.py`
Expected: all PASS.

- [ ] **Step 5: Break it once, then restore**

Change `if len(carded) < MIN_SCORED:` to `if not carded:`: `test_by_signal_needs_twenty_carded_calls` must FAIL. Restore. Change `v > 50` to `v >= 50`: `test_card_groups_above_median_are_recorded` must FAIL (momentum at 50 would count). Restore. Re-run: all PASS.

- [ ] **Step 6: Commit**

```bash
git add harness/ledger_score.py harness/test_ledger_score.py
git commit -m "Investor Scout: ledger_score --by-signal splits results by group and sector

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 9: Fetched-but-unused check (`data_audit.py`)

**Files:**
- Create: `bobs/investor/harness/data_audit.py`
- Test: `bobs/investor/harness/test_data_audit.py`

**Interfaces:**
- Consumes: `edgar.QUARTERLY`, `edgar.RECENT_QUARTERLY`, `edgar.ANNUAL`, `edgar.INSTANT`, `xbrl.FIELDS`, `xbrl.INSTANT_FIELDS`, `macro.SERIES`, `macro.lines`, `screen.latest()`, `scorecard.SIGNALS`.
- Produces:
  - `data_audit.unused_concepts() -> list[str]`
  - `data_audit.unshown_series() -> list[str]`
  - `data_audit.signal_coverage(doc: dict) -> dict[str, float | None]`
  - `data_audit.report_lines() -> tuple[bool, list[str]]` (ok, three lines)

- [ ] **Step 1: Write the failing tests**

Create `bobs/investor/harness/test_data_audit.py`:

```python
#!/usr/bin/env python3
"""data_audit: a concept fetched but read by nothing fails; coverage is reported, never failed on.

Run:  python3 harness/test_data_audit.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import data_audit  # noqa: E402
import edgar  # noqa: E402
import scorecard  # noqa: E402
import testkit  # noqa: E402
import xbrl  # noqa: E402


def test_every_fetched_concept_is_read():
    assert data_audit.unused_concepts() == []


def test_a_concept_nobody_reads_is_reported():
    saved = edgar.INSTANT
    edgar.INSTANT = saved + ("goodwill",)
    try:
        assert data_audit.unused_concepts() == ["goodwill"]
    finally:
        edgar.INSTANT = saved


def test_a_mapped_field_nobody_reads_is_reported():
    saved = dict(xbrl.INSTANT_FIELDS)
    xbrl.INSTANT_FIELDS["assets"] = "total_assets_nobody_reads"
    try:
        assert data_audit.unused_concepts() == ["assets"]
    finally:
        xbrl.INSTANT_FIELDS.clear()
        xbrl.INSTANT_FIELDS.update(saved)


def test_every_fred_series_is_shown():
    assert data_audit.unshown_series() == []


def test_coverage_counts_known_values():
    doc = {"cards": {"A": {"signals": {s: {"value": 1.0} for s in scorecard.SIGNALS}},
                     "B": {"signals": {s: {"value": None if s == "insider" else 1.0} for s in scorecard.SIGNALS}}}}
    cover = data_audit.signal_coverage(doc)
    assert cover["insider"] == 0.5 and cover["ttm_growth"] == 1.0
    assert data_audit.signal_coverage({})["insider"] is None


def test_report_is_ok_and_says_when_there_is_no_screen():
    testkit.temp_bob()
    ok, lines = data_audit.report_lines()
    assert ok and lines[0] == "fetched but never read: none" and lines[1] == "FRED series not shown: none"
    assert lines[2] == "signal coverage in the latest screen: no screen"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_data_audit.py`
Expected: `ModuleNotFoundError: No module named 'data_audit'`.

- [ ] **Step 3: Write the implementation**

Create `bobs/investor/harness/data_audit.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_data_audit.py`
Expected: `6 of 6 passed`.

- [ ] **Step 5: Break it once, then restore**

In `edgar.py`, add `"goodwill": [("us-gaap", "Goodwill", "USD")],` to `CONCEPTS` and `"goodwill"` to `INSTANT`. Run `python3 harness/test_data_audit.py`: `test_every_fetched_concept_is_read` must FAIL naming `goodwill`. Restore `edgar.py` exactly. Re-run: `6 of 6 passed`.

- [ ] **Step 6: Commit**

```bash
git add harness/data_audit.py harness/test_data_audit.py
git commit -m "Investor Scout: data_audit fails on data fetched but never read

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 10: Show market conditions and the audit on the board and in the digest

**Files:**
- Modify: `bobs/investor/harness/digest.py` (imports, `build`)
- Modify: `bobs/investor/harness/scout_board.py` (imports, `Model.__init__`, `Model.rebuild`, `PAGE`)
- Test: `bobs/investor/harness/test_digest.py`, `bobs/investor/harness/test_scout_board.py`

**Interfaces:**
- Consumes: `macro.load()`, `macro.lines(doc)`, `macro.path()`, `data_audit.report_lines()`.
- Produces: digest JSON gains `macro` and `data_audit: {"ok": bool, "lines": [...]}`; board state gains `macro: list[str]`.

- [ ] **Step 1: Write the failing tests**

In `test_digest.py` add before the `__main__` block:

```python
def test_market_conditions_and_data_audit_are_in_the_digest():
    bob = _world()
    (bob / "data" / "state" / "macro.json").write_text(json.dumps({"series": {"VIXCLS": {
        "what": "VIX close", "value": 18.2, "observed": "2026-10-16", "stale": False, "evidence": "e/1",
        "reason": None}}}), encoding="utf-8")
    text, data = digest.build(TODAY)
    assert "- VIXCLS VIX close: 18.2 on 2026-10-16 [ev:e/1]" in text
    assert "## Data audit" in text and "- fetched but never read: none" in text
    assert data["data_audit"]["ok"] is True and data["macro"]["series"]["VIXCLS"]["value"] == 18.2


def test_digest_without_market_conditions_says_so():
    _world()
    text, data = digest.build(TODAY)
    assert "- market conditions not refreshed (run macro.py --refresh)" in text and data["macro"] == {}
```

In `test_scout_board.py` add before the `__main__` block:

```python
def test_rebuild_shows_market_conditions():
    bob = _world()
    model = scout_board.Model("Investor Scout")
    assert model.rebuild()["macro"] == ["market conditions not refreshed (run macro.py --refresh)"]
    (bob / "data" / "state" / "macro.json").write_text(json.dumps({"series": {"NFCI": {
        "what": "x", "value": None, "observed": None, "stale": None, "evidence": None, "reason": "404 not found"}}}),
        encoding="utf-8")
    assert model.rebuild()["macro"] == ["NFCI x: could not tell (404 not found)"]
    assert b'id="macro"' in scout_board.PAGE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 harness/test_digest.py` and `python3 harness/test_scout_board.py`
Expected: the new tests FAIL (`KeyError: 'data_audit'`, `KeyError: 'macro'`).

- [ ] **Step 3: Write the implementation**

`digest.py`: add `import data_audit  # noqa: E402` and `import macro  # noqa: E402` to the imports (alphabetical). In `build`:

1. After `scored_12m = ...` add:

```python
    macro_doc = macro.load()
    audit_ok, audit = data_audit.report_lines()
```

2. Add to the `data = {...}` dict: `"macro": macro_doc, "data_audit": {"ok": audit_ok, "lines": audit},`.
3. Right after the noise-banner block (`if data["noise"]: ...`), add:

```python
    lines += ["## Market conditions (context only, not in any score)", ""]
    lines += [f"- {line}" for line in macro.lines(macro_doc)] + [""]
```

4. Before `if torn:` add:

```python
    lines += ["", "## Data audit", ""] + [f"- {line}" for line in audit]
```

`scout_board.py`: add `import macro  # noqa: E402` to the imports. In `Model.__init__`, add `macro.path()` to the `Watched([...])` file list:

```python
        self.ev = Watched([state / "screen" / "latest.json", ledger_score.results_path(), state / "calls.jsonl",
                           macro.path()], self.theses)
```

In `rebuild`, add `"macro": macro.lines(macro.load()),` to the returned dict (after `"stale": stale,`).

In `PAGE`, after `<div id="banners"></div>` add:

```html
<h2>Market conditions <span class="muted">context only, not in any score</span></h2><ul id="macro"></ul>
```

and in the `show(s)` function, after the `if (s.stale) ...` line, add:

```js
  var mc = document.getElementById("macro"); mc.textContent = ""; (s.macro || []).forEach(function(l){ mc.appendChild(el("li", l)); });
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 harness/test_digest.py` and `python3 harness/test_scout_board.py`
Expected: all PASS.

- [ ] **Step 5: Break it once, then restore**

Remove `"macro": macro.lines(macro.load()),` from `rebuild`: `test_rebuild_shows_market_conditions` must FAIL. Restore. Re-run: all PASS.

- [ ] **Step 6: Look at the page**

Run `python3 harness/scout_board.py` (port 8791) in the background, note its PID, open `http://127.0.0.1:8791/`. Before looking, write down what must show: a "Market conditions" heading under the title with one bullet per series (or the "not refreshed" line). Look, confirm, then stop the board by its PID.

- [ ] **Step 7: Commit**

```bash
git add harness/digest.py harness/scout_board.py harness/test_digest.py harness/test_scout_board.py
git commit -m "Investor Scout: market conditions and data audit on board and digest

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

---

### Task 11: Loop, docs and PLAYBOOK

**Files:**
- Modify: `bobs/investor/loops/nightly-screen.txt`
- Modify: `bobs/investor/README.md`, `bobs/investor/CLAUDE.md`, `bobs/investor/docs/RELEASE-NOTES.md`, `bobs/investor/docs/TRACKER.md`
- Modify: `PLAYBOOK.md` (core, repo root)
- Test: `bobs/investor/harness/test_loops.py` (existing; checks loop CLIs and flags exist)

- [ ] **Step 1: Update the nightly loop**

Replace the body of `loops/nightly-screen.txt` from `Scripts do the numbers...` to the end with:

```
Scripts do the numbers; you do the reading. Stop at the first FAIL, report it, never work around it,
except step 1: market conditions are context, so a FAIL there is noted for step 7 and the run goes on.

1. python3 harness/macro.py --refresh
2. python3 harness/prices.py --refresh
3. python3 harness/edgar.py --sync
   The first run after this change reads every company's SIC code once (about 10 minutes).
4. python3 harness/screen.py --run
   A FAIL here means no screen today: no theses and no verdicts. Go to step 5, then 7.
5. python3 harness/ledger_score.py --run
6. Only if step 4 printed OK: for at most 5 names from its new= list, in that order:
   a. python3 harness/screen.py --card TICKER
   b. Find the latest 10-Q or 10-K and the earnings release (8-K, exhibit 99.1) on SEC, and read each with
      python3 harness/edgar.py --read URL
      It prints the evidence folder; read raw.txt there. Never read a filing any other way.
   c. Copy templates/thesis.md to theses/TICKER.md (or update the one there). Fill every section. Every line
      with a number ends with [ev:PATH] for the folder it came from. Paste the card unchanged under Scorecard.
      Reverse DCF: python3 harness/fundamentals/valuation.py --reverse --target MARKETCAP --fcf CASHFLOW
   d. python3 harness/thesis_lint.py theses/TICKER.md until it prints OK.
   e. python3 harness/calls.py verdict TICKER --verdict VERDICT --lane numbers
      REFUSED is the answer, not an obstacle. Write the reason in the step 7 tracker row; never edit files
      to get past it.
7. One row in docs/TRACKER.md: steps run, OK or FAIL lines, theses written, verdicts recorded or refused.
```

Run: `python3 harness/test_loops.py`
Expected: all PASS (`macro.py` has `"--refresh"`).

- [ ] **Step 2: README run table and limits**

In `README.md`, add these rows to the `| what | command |` table after `| refresh data | ... |`:

```
| market conditions (context only) | `python3 harness/macro.py --refresh`, view: `--show` |
| results by group and sector | `python3 harness/ledger_score.py --by-signal` (needs 20 calls with a scorecard) |
| data fetched but never used | `python3 harness/data_audit.py --report` |
```

Add to `## Known limits`:

```
- Market conditions (FRED) are context only: shown and stored with each call, never in a score.
- Growth and quality rank within the company's Fama-French 12 sector once its SIC code is cached
  and the sector has 15 known values; until then they rank against all stocks, and the card says so.
- Altman Z is not computed for SIC 6000 to 6799 (banks, insurers, real estate); the card says why.
```

- [ ] **Step 3: Domain rules**

In `bobs/investor/CLAUDE.md` under `## Domain rules`, change the **Sources** bullet's first sentence to:

```
- **Sources.** SEC through `harness/edgar.py` (`--read URL` for any filing), Alpaca market data
  through `harness/prices.py`, and FRED market conditions through `harness/macro.py` (context only;
  never argue a verdict from them).
```

(keep the rest of that bullet unchanged).

- [ ] **Step 4: Release notes and tracker**

In `docs/RELEASE-NOTES.md`, add above `## Scaffold`:

```
## Scorecard v2: sectors, agreement, market conditions

- Growth and quality now rank within each company's sector (Fama-French 12 from its SEC SIC code),
  or against all stocks when the sector is small; each signal on the card says which.
- A top-ranked name needs at least 3 known factor groups above median to be eligible; otherwise
  it is "watch", with the count as the reason.
- Banks, insurers and real estate (SIC 6000 to 6799) no longer get an Altman Z distress veto.
- Every new call stores its full scorecard, the market conditions and the screen file's hash.
- New: `macro.py` (FRED, context only), `ledger_score.py --by-signal`, `data_audit.py`.
- What it means for you: scores from v2 are not comparable with v1; calls made under v1 stay in the
  ledger and are scored as before. Expect fewer eligible names than under v1.
```

In `docs/TRACKER.md`, add a row:

```
| 2026-10-14 | Scout upgrades (scorecard v2) built per docs/superpowers/plans/2026-10-14-scout-upgrades.md | selftest result and live checks in the next rows |
```

- [ ] **Step 5: PLAYBOOK (core)**

In `PLAYBOOK.md` (repo root), in `## 4. Make it good at its job`, add after the `**A board panel**` bullet:

```
- **"What we are not building":** every Bob design (and every later change to it) lists what is out
  of scope, so the next session does not build it by accident.
- **Back up before you migrate:** any change that rewrites a Bob's state, evidence or ledger starts
  with step 0: copy the state folder and the ledger to a dated backup folder and write its path in
  the tracker. A duplicate is recoverable; a migration that went wrong without a copy is not.
```

From the repo root run: `./selftest.sh`
Expected: every line `ok`.

- [ ] **Step 6: Commit**

```bash
git add bobs/investor/loops/nightly-screen.txt bobs/investor/README.md bobs/investor/CLAUDE.md bobs/investor/docs/RELEASE-NOTES.md bobs/investor/docs/TRACKER.md PLAYBOOK.md
git commit -m "Investor Scout: nightly loop reads FRED first; docs for scorecard v2

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```

(Run from the repo root, `bob_core/`.)

---

### Task 12: Full suite and live checks (spec section 7)

**Files:**
- Modify: `bobs/investor/docs/TRACKER.md` (one row per check)

- [ ] **Step 1: Every suite**

Run from `bob_core/`: `./selftest.sh`, then from `bobs/investor/`: `./selftest.sh`
Expected: every line `ok` in both.

- [ ] **Step 2: Live FRED read (done-when 2)**

From `bobs/investor/`: `python3 harness/macro.py --refresh`, then `python3 harness/macro.py --show`
Expected: `OK series=8 stale=[...]`, eight lines each with `[ev:...]`. If FRED refuses the request (403, HTML instead of CSV), record the exact line in the tracker and stop; report it (the `User-Agent` may need changing, which is a person's call).

- [ ] **Step 3: Live SEC sync and screen (done-when 3)**

Needs `ALPACA_PAPER_KEY_ID` and `ALPACA_PAPER_SECRET_KEY` in the environment. If they are not set, write "blocked: Alpaca keys not in environment" in the tracker, skip to Step 4 and report the blocker at the end.

```bash
python3 harness/prices.py --refresh
python3 harness/edgar.py --sync
python3 harness/screen.py --run
```

Expected: `edgar.py` prints `sic_read=` in the thousands on its first run; `screen.py` prints `OK ...`. Then check that no financial company was vetoed by Altman:

```bash
python3 -c "import sys; sys.path.insert(0, 'harness'); import scoutpath, screen, sectors; d = screen.latest(); bad = [t for t, c in d['cards'].items() if sectors.is_financial(c.get('sic')) and 'Altman' in c['reason']]; print('OK' if not bad else f'FAIL: {bad}')"
```

Expected: `OK`. Pick one financial name and one chipmaker from the shortlist or cards and run `python3 harness/screen.py --card TICKER` for each: the financial shows `Altman Z could not tell (not valid for financials)`, the chipmaker a number, and both show `sector ...` and `vs BusEq` / `vs market` per signal.

- [ ] **Step 4: Data audit (done-when 4)**

Run: `python3 harness/data_audit.py --report`
Expected: `OK`, `fetched but never read: none`, `FRED series not shown: none`, and a coverage line per signal.

- [ ] **Step 5: Record and commit**

Add one tracker row per step above with the OK/FAIL line and the evidence path where there is one.

```bash
git add docs/TRACKER.md
git commit -m "Investor Scout: record scorecard v2 live checks

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push
```
