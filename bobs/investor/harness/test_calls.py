#!/usr/bin/env python3
"""calls: every refusal the spec names, each next to the case it must allow.

Run:  python3 harness/test_calls.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import calls  # noqa: E402
import testkit  # noqa: E402
from test_thesis_lint import thesis  # noqa: E402

NOW = dt.datetime(2026, 10, 13, 5, 30, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 10, 14, 5, 30, tzinfo=dt.timezone.utc)


def _put(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))


def _screen(bob: pathlib.Path, session: str, gate: str = "eligible") -> None:
    state = bob / "data" / "state"
    _put(state / "prices" / "_calendar.json", {"sessions": [
        {"date": "2026-10-12", "close": "16:00"}, {"date": "2026-10-13", "close": "16:00"}]})
    _put(state / "screen" / "latest.json", {"session": session, "scorecard_version": 1, "cards": {
        "NVDA": {"symbol": "NVDA", "gate": gate, "reason": "test", "score": 91.0, "coverage": 1.0, "rank_pct": 95.0}}})


def _bob(gate: str = "eligible", verdict: str = "buy-worthy", lane: str = "numbers") -> pathlib.Path:
    bob = testkit.temp_bob()
    _put(bob / "data" / "evidence" / "2026-10-13" / "sec-frame-x" / "meta.json", "{}")
    _screen(bob, "2026-10-12", gate)
    _put(bob / "theses" / "NVDA.md", thesis(verdict, lane))
    return bob


def _refused(fn) -> str:
    try:
        fn()
    except calls.Refused as e:
        return str(e)
    raise AssertionError("not refused")


def test_valid_buy_worthy_records_verdict_and_call():
    _bob()
    rows = calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    assert [r["kind"] for r in rows] == ["verdict", "call"]
    assert rows[1]["id"] == "NVDA-2026-10-12-1" and rows[1]["session"] == "2026-10-12" and rows[1]["thesis_sha256"]
    assert calls.read()[0] == rows


def test_utf8_thesis_records_on_any_platform():
    bob = _bob()
    (bob / "theses" / "NVDA.md").write_text(thesis(bull="- Łódź plant ships 94% more [ev:2026-10-13/sec-frame-x]"),
                                            encoding="utf-8")
    assert [r["kind"] for r in calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)] == ["verdict", "call"]


def test_refused_when_gate_is_not_eligible():
    _bob(gate="pass")
    assert "gate is pass" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))
    assert calls.read() == ([], 0)


def test_watch_is_allowed_whatever_the_gate():
    _bob(gate="pass", verdict="watch")
    assert [r["kind"] for r in calls.record_verdict("NVDA", "watch", "numbers", None, NOW)] == ["verdict"]


def test_refused_without_thesis():
    bob = _bob()
    (bob / "theses" / "NVDA.md").unlink()
    assert "no thesis" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))


def test_refused_when_thesis_fails_lint():
    bob = _bob()
    _put(bob / "theses" / "NVDA.md", thesis(bull="- Revenue grew 94%"))
    assert "uncited number" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))


def test_refused_when_thesis_verdict_differs():
    _bob(verdict="watch")
    assert "thesis says watch" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))


def test_refused_when_screen_is_stale():
    _bob()
    message = _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, LATER))
    assert "screen is for 2026-10-12" in message and "2026-10-13" in message


def test_same_verdict_same_day_refused():
    _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    assert "already recorded" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))
    assert len(calls.read()[0]) == 2


def test_open_call_blocks_a_second_call():
    bob = _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    _screen(bob, "2026-10-13")
    assert "already called" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, LATER))


def test_lower_verdict_withdraws_the_open_call():
    bob = _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    _screen(bob, "2026-10-13")
    _put(bob / "theses" / "NVDA.md", thesis("watch"))
    rows = calls.record_verdict("NVDA", "watch", "numbers", None, LATER)
    assert [r["kind"] for r in rows] == ["verdict", "withdrawn"] and rows[1]["call_id"] == "NVDA-2026-10-12-1"
    assert calls.open_call(calls.read()[0], "NVDA", dt.date(2026, 10, 14)) is None


def test_called_name_that_left_the_universe_can_be_downgraded():
    bob = _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    _put(bob / "data" / "state" / "screen" / "latest.json", {"session": "2026-10-13", "scorecard_version": 1, "cards": {}})
    _put(bob / "theses" / "NVDA.md", thesis("pass"))
    rows = calls.record_verdict("NVDA", "pass", "numbers", None, LATER)
    assert [r["kind"] for r in rows] == ["verdict", "withdrawn"]


def test_a_name_never_scored_still_cannot_be_called():
    bob = _bob()
    _put(bob / "data" / "state" / "screen" / "latest.json", {"session": "2026-10-12", "scorecard_version": 1, "cards": {}})
    assert "not scored" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW))


def test_recording_waits_for_the_ledger_lock():
    import threading
    import time
    _bob()
    lock_path = calls.ledger_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    done = []
    with lock_path.open("a", encoding="utf-8") as held:
        calls.oscompat.lock(held)
        worker = threading.Thread(target=lambda: done.append(calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)))
        worker.start()
        time.sleep(0.4)
        assert worker.is_alive() and not done
        calls.oscompat.unlock(held)
    worker.join(10)
    assert done and len(calls.read()[0]) == 2


def test_story_lane_needs_a_resolving_source():
    _bob(lane="story")
    assert "source" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "story", None, NOW))
    assert "source" in _refused(lambda: calls.record_verdict("NVDA", "buy-worthy", "story", "2026-10-13/nope", NOW))
    rows = calls.record_verdict("NVDA", "buy-worthy", "story", "2026-10-13/sec-frame-x", NOW)
    assert rows[1]["lane"] == "story" and rows[1]["source"] == "2026-10-13/sec-frame-x"


def test_torn_line_is_skipped_and_counted():
    _bob()
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    with calls.ledger_path().open("a") as fh:
        fh.write('{"kind": "verdict", "tick\n')
    rows, torn = calls.read()
    assert len(rows) == 2 and torn == 1


def test_correction_needs_an_existing_call():
    _bob()
    assert "no call" in _refused(lambda: calls.correct("NVDA-2026-10-12-1", "typo", NOW))
    calls.record_verdict("NVDA", "buy-worthy", "numbers", None, NOW)
    assert calls.correct("NVDA-2026-10-12-1", "thesis link moved", NOW)["kind"] == "correction"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
