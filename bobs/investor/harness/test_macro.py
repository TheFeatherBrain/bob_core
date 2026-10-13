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
