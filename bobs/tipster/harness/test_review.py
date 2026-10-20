#!/usr/bin/env python3
"""review: Claude keeps, halves or vetoes with cited evidence, and can never pass a number.

Run:  python3 harness/test_review.py
"""
from __future__ import annotations

import datetime as dt
import io
import pathlib
import sys
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import discover  # noqa: E402
import review  # noqa: E402
import testkit  # noqa: E402
import tips  # noqa: E402
import tipsterworld as w  # noqa: E402


def _candidate() -> str:
    w.world()
    w.price_paris()
    code, lines = discover.run(w.NOW, getter=lambda u, h: "{}", environ=w.KEY, sync=w.no_sync)
    assert code == 0 and any(x.startswith("CANDIDATE c-1 ") for x in lines), lines
    return "c-1"


def _main(argv: list[str]) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = review.main(argv, now=w.NOW + dt.timedelta(minutes=5))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue() + err.getvalue()


def test_keep_records_reviewed_tip():
    cid = _candidate()
    ev = w.evidence_file()
    code, out = _main([cid, "--keep", "--reason", "both teams at full strength", "--evidence", ev])
    assert code == 0 and out.startswith("OK kept t-E2026-69-side-reviewed stake=49"), out
    tip = tips.replay("reviewed")["open"][0]
    assert tip["stake"] == 49 and tip["review"] == {"decision": "keep", "reason": "both teams at full strength",
                                                    "evidence": [ev], "candidate": cid}
    assert tip["odds"] == 2.0 and tip["edge"] > 0.19


def test_halve_halves_stake():
    cid = _candidate()
    code, out = _main([cid, "--halve", "--reason", "Vezenkov questionable", "--evidence", w.evidence_file()])
    assert code == 0 and tips.replay("reviewed")["open"][0]["stake"] == 24, out


def test_veto_is_model_track_only():
    cid = _candidate()
    code, out = _main([cid, "--veto", "--reason", "Fournier out", "--evidence", w.evidence_file()])
    assert code == 0 and out.startswith("OK vetoed"), out
    assert tips.replay("reviewed")["open"] == [] and len(tips.replay("model")["open"]) == 1
    refused = [r for r in tips.rows()[0] if r.get("kind") == "refused"]
    assert refused[0]["track"] == "reviewed" and refused[0]["reason"] == "veto: Fournier out"
    again, out = _main([cid, "--keep", "--reason", "changed my mind", "--evidence", w.evidence_file()])
    assert again == 1 and "already reviewed" in out, out


def test_veto_holds_on_later_sweeps():
    cid = _candidate()
    review.review(cid, "veto", "star out", [w.evidence_file()], now=w.NOW)
    code, lines = discover.run(w.NOW + dt.timedelta(days=1), getter=lambda u, h: "{}", environ=w.KEY, sync=w.no_sync)
    assert code == 0 and not [x for x in lines if x.startswith("CANDIDATE")], lines


def test_review_refuses_probability_argument():
    cid = _candidate()
    for extra in (["--p", "0.7"], ["--stake", "100"], ["--edge", "0.2"]):
        code, out = _main([cid, "--keep", "--reason", "x", "--evidence", w.evidence_file()] + extra)
        assert code == 2 and "unrecognized arguments" in out, (extra, out)
    code, out = _main([cid, "--keep", "--reason", "x", "--evidence", w.evidence_file(), "0.7"])
    assert code == 1 and "evidence 0.7" in out, out
    assert tips.replay("reviewed")["open"] == []


def test_review_refuses_without_evidence():
    cid = _candidate()
    code, out = _main([cid, "--keep", "--reason", "x"])
    assert code == 2, out
    outside = tipsterpath.HERE / "testkit.py"
    code, out = _main([cid, "--keep", "--reason", "x", "--evidence", str(outside)])
    assert code == 1 and "evidence" in out, out
    code, out = _main([cid, "--keep", "--reason", "x", "--evidence", str(w.evidence_file()) + ".missing"])
    assert code == 1 and "evidence" in out, out
    assert tips.replay("reviewed")["open"] == []


def test_two_decisions_refused():
    cid = _candidate()
    code, out = _main([cid, "--keep", "--veto", "--reason", "x", "--evidence", w.evidence_file()])
    assert code == 2, out


def test_expired_candidate_refused():
    cid = _candidate()
    started = dt.datetime(2026, 10, 23, 18, 45, tzinfo=dt.timezone.utc)
    try:
        review.review(cid, "keep", "x", [w.evidence_file()], now=started)
    except review.ReviewError as e:
        assert "expired" in str(e)
    else:
        raise AssertionError("expired candidate accepted")
    code, out = _main(["c-99", "--keep", "--reason", "x", "--evidence", w.evidence_file()])
    assert code == 1 and "unknown or expired" in out, out


def test_halt_refuses_review():
    cid = _candidate()
    tips.halt("person says stop")
    code, out = _main([cid, "--keep", "--reason", "x", "--evidence", w.evidence_file()])
    assert code == 1 and "halted" in out and tips.replay("reviewed")["open"] == []


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
