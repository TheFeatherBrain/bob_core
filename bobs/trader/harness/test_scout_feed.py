#!/usr/bin/env python3
"""scout_feed: v2 calls only, withdrawals honoured, torn lines counted, stale macro is unknown.

Run:  python3 harness/test_scout_feed.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import scout_feed  # noqa: E402
import testkit  # noqa: E402

CARD = {"rank_pct": 95.0, "groups": {"insider": None}}


def _call(cid: str, session: str, card: dict | None = CARD) -> dict:
    row = {"kind": "call", "id": cid, "ticker": cid[:3], "symbol": cid[:3], "session": session}
    return {**row, "card": card} if card is not None else row


def test_reads_v2_calls_and_counts_v1_and_torn():
    bob = testkit.temp_bob()
    rows = [_call("AAA-1", "2026-10-01"), _call("OLD-1", "2026-09-01", card=None),
            {"kind": "verdict", "ticker": "AAA"}, {"kind": "withdrawn", "call_id": "AAA-1"}]
    testkit.put(testkit.scout_state(bob) / "calls.jsonl", "".join(json.dumps(r) + "\n" for r in rows) + "{torn\n")
    feed = scout_feed.load()
    assert [c["id"] for c in feed["calls"]] == ["AAA-1"] and feed["skipped_v1"] == 1 and feed["torn"] == 1
    assert feed["withdrawn"] == {"AAA-1"} and feed["macro"] == {}


def test_missing_ledger_is_empty_not_an_error():
    testkit.temp_bob()
    feed = scout_feed.load()
    assert feed["calls"] == [] and feed["torn"] == 0


def test_open_calls_drop_withdrawn_and_expired():
    feed = {"calls": [_call("AAA-1", "2026-10-01"), _call("BBB-1", "2025-10-01"), _call("CCC-1", "2026-10-05")],
            "withdrawn": {"CCC-1"}}
    assert [c["id"] for c in scout_feed.open_calls(feed, dt.date(2026, 10, 14))] == ["AAA-1"]


def test_macro_value_is_none_when_missing_unread_or_stale():
    macro = {"series": {"VIXCLS": {"value": 18.0, "stale": False}, "NFCI": {"value": -0.4, "stale": True},
                        "T10Y2Y": {"value": None, "stale": None}, "BAMLH0A0HYM2": {"value": 3.1, "stale": None}}}
    assert scout_feed.macro_value(macro, "VIXCLS") == 18.0
    assert scout_feed.macro_value(macro, "NFCI") is None and scout_feed.macro_value(macro, "T10Y2Y") is None
    assert scout_feed.macro_value(macro, "BAMLH0A0HYM2") is None
    assert scout_feed.macro_value(macro, "ICSA") is None and scout_feed.macro_value({}, "VIXCLS") is None


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
