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
