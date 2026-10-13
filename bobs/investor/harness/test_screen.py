#!/usr/bin/env python3
"""screen: a small fabricated market, end to end, including the days it must refuse to screen.

Run:  python3 harness/test_screen.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import edgar  # noqa: E402
import screen  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 13, 5, 30, tzinfo=dt.timezone.utc)
STOCKS = {"AAA": (1, 1e8, 0.01), "BBB": (2, 1e8, 0.03), "CCC": (3, 1e8, 0.06), "DDD": (4, 1e3, 0.02)}
LOWER_FLOOR = {"min_coverage = 0.70": "min_coverage = 0.60"}


def _sessions(n: int = 300) -> list[str]:
    day, out = dt.date(2026, 10, 12), []
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= dt.timedelta(days=1)
    return out[::-1]


def _put(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _world(bob: pathlib.Path) -> pathlib.Path:
    state = bob / "data" / "state"
    sec, px = state / "sec", state / "prices"
    days = _sessions()
    _put(px / "_calendar.json", {"fetched": "2026-10-13", "evidence": "ev/cal",
                                 "sessions": [{"date": d, "close": "16:00"} for d in days]})
    for symbol in ("SPY", "QQQ"):
        _put(px / f"{symbol}.json", {"bars": [[d, 100.0, 1e6] for d in days], "evidence": [f"ev/{symbol}"]})
    _put(sec / "tickers.json", {"fetched": "2026-10-13", "evidence": "ev/tickers", "rows": [
        {"cik": cik, "name": f"{t} INC", "ticker": t, "exchange": "Nasdaq"} for t, (cik, _, _) in STOCKS.items()]
        + [{"cik": 1, "name": "AAA INC", "ticker": "AAA-WT", "exchange": "Nasdaq"}]})
    quarters = edgar.recent_quarters(dt.date(2026, 10, 13), 13)
    for i, (y, q) in enumerate(quarters):
        end = edgar.quarter_end(y, q)
        py, pq = (y - 1, 4) if q == 1 else (y, q - 1)
        start = (edgar.quarter_end(py, pq) + dt.timedelta(days=1)).isoformat()
        # flat, then +g and +3g in the last two quarters: TTM growth = g, acceleration = 2g, both
        # strictly ordered by g, so the ranking below never depends on floating-point ties
        rev = [{"cik": cik, "val": 100 * (1 + g) if i == 11 else 100 * (1 + 3 * g) if i == 12 else 100.0,
                "start": start, "end": end.isoformat()} for cik, _, g in STOCKS.values()]
        eps = [{"cik": cik, "val": 1 + i * g + (i % 3) * 0.05, "start": start, "end": end.isoformat()}
               for cik, _, g in STOCKS.values()]
        _put(sec / "frames" / f"us-gaap.Revenues.USD.CY{y}Q{q}.json", {"evidence": f"ev/rev{i}", "rows": rev})
        _put(sec / "frames" / f"us-gaap.EarningsPerShareDiluted.USD-per-shares.CY{y}Q{q}.json",
             {"evidence": f"ev/eps{i}", "rows": eps})
    _put(sec / "frames" / "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q2I.json",
         {"evidence": "ev/shares", "rows": [{"cik": cik, "val": sh, "end": "2026-06-30"} for cik, sh, _ in STOCKS.values()]})
    for t, (_, _, g) in STOCKS.items():
        _put(px / f"{t}.json", {"bars": [[d, 10 * (1 + g / 20) ** k, 1e6] for k, d in enumerate(days)],
                                "evidence": [f"ev/px-{t}"]})
    return state


def _drop_last_bar(state: pathlib.Path, symbol: str) -> None:
    path = state / "prices" / f"{symbol}.json"
    doc = json.loads(path.read_text())
    doc["bars"] = doc["bars"][:-1]
    path.write_text(json.dumps(doc))


def test_candidates_one_ticker_per_cik():
    rows = [{"cik": 1, "ticker": "BRK-B", "name": "B", "exchange": "NYSE"},
            {"cik": 1, "ticker": "BRK-A", "name": "B", "exchange": "NYSE"}]
    assert [(c["ticker"], c["symbol"]) for c in screen.candidates(rows)] == [("BRK-B", "BRK.B")]


def test_run_scores_ranks_and_applies_floors():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    code, message = screen.run(NOW)
    assert code == 0 and message.startswith("OK"), message
    doc = screen.latest()
    assert doc["session"] == "2026-10-12" and doc["shortlist"] == ["CCC", "BBB", "AAA"]
    assert "DDD" not in doc["cards"] and doc["new_entrants"] == ["CCC", "BBB", "AAA"]
    assert doc["cards"]["CCC"]["gate"] == "eligible" and doc["cards"]["AAA"]["gate"] == "pass"
    assert (state / "screen" / "2026-10-12.json").is_file()


def test_low_coverage_is_could_not_tell():
    _world(testkit.temp_bob())
    code, _ = screen.run(NOW)
    doc = screen.latest()
    assert code == 0 and doc["shortlist"] == []
    assert {c["gate"] for c in doc["cards"].values()} == {"could not tell"}


def test_stale_benchmark_fails_and_writes_nothing():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    _drop_last_bar(state, "SPY")
    code, message = screen.run(NOW)
    assert code == 1 and "SPY" in message and not (state / "screen" / "latest.json").exists()


def test_stale_prices_over_the_limit_fail_but_unpriced_names_do_not():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    (state / "prices" / "DDD.json").unlink()
    code, _ = screen.run(NOW)
    assert code == 0 and screen.latest()["unpriced"] == 1
    _drop_last_bar(state, "AAA")
    code, message = screen.run(NOW)
    assert code == 1 and "no current price" in message


def test_names_without_shares_are_counted_not_dropped_silently():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    shares = state / "sec" / "frames" / "dei.EntityCommonStockSharesOutstanding.shares.CY2026Q2I.json"
    doc = json.loads(shares.read_text())
    doc["rows"] = [r for r in doc["rows"] if r["cik"] != 3]
    shares.write_text(json.dumps(doc))
    code, message = screen.run(NOW)
    assert code == 0 and "no_shares=1" in message and screen.latest()["no_shares"] == 1
    assert "CCC" not in screen.latest()["cards"]


def test_failed_screen_is_recorded_for_the_digest():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    _drop_last_bar(state, "SPY")
    code, message = screen.run(NOW)
    rows = [json.loads(line) for line in (state / "screen" / "failures.jsonl").read_text().splitlines()]
    assert code == 1 and rows == [{"at": NOW.isoformat(timespec="seconds"), "message": message}]


def test_ok_line_reports_insider_coverage():
    _world(testkit.temp_bob(LOWER_FLOOR))
    assert "insider_covered=no" in screen.run(NOW)[1]


def test_stale_price_below_the_floors_does_not_trip_the_limit():
    state = _world(testkit.temp_bob(LOWER_FLOOR))
    _drop_last_bar(state, "DDD")
    code, message = screen.run(NOW)
    assert code == 0 and "stale=0" in message, message


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


def test_rerun_same_session_keeps_new_entrants():
    _world(testkit.temp_bob(LOWER_FLOOR))
    screen.run(NOW)
    screen.run(NOW)
    assert screen.latest()["new_entrants"] == ["CCC", "BBB", "AAA"]


def test_card_text_names_gate_and_evidence():
    _world(testkit.temp_bob(LOWER_FLOOR))
    screen.run(NOW)
    text = screen.card_text("CCC", screen.latest())
    assert "eligible" in text and "ev/px-CCC" in text and "ev/rev12" in text


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
