#!/usr/bin/env python3
"""digest: the weekly file states the board's numbers, for the week and the season, both tracks.

Run:  python3 harness/test_digest.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import digest  # noqa: E402
import testkit  # noqa: E402
import tips  # noqa: E402
import tipster_board  # noqa: E402
import tipsterworld as w  # noqa: E402
from test_tipster_board import _settle, _tip  # noqa: E402

MONDAY = dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.timezone.utc)


def test_digest_matches_board_numbers():
    w.world()
    _settle(_tip(1, stake=10), "win", 10.0, 0.05, at="2026-10-28T09:15:00+00:00")
    _settle(_tip(2, stake=20), "loss", -20.0, -0.02, at="2026-10-20T09:15:00+00:00")
    _settle(_tip(3, track="model", stake=30), "win", 30.0, 0.04, at="2026-10-29T09:15:00+00:00")
    board = tipster_board.state(now=MONDAY)
    week = digest.numbers(MONDAY, days=7)
    season = digest.numbers(MONDAY, days=None)
    assert season["reviewed"]["all"] == board["scores"]["reviewed"]["all"]
    assert season["model"]["all"] == board["scores"]["model"]["all"]
    assert week["reviewed"]["all"]["n"] == 1 and week["reviewed"]["all"]["profit"] == 10.0
    path = digest.write(MONDAY)
    text = path.read_text(encoding="utf-8")
    assert path.name == "2026-11-02.md"
    assert "| reviewed | all | 2 | -10.00 | -33.3% | 0.015 (n=2) | 50% |" in text, text
    assert "| model | all | 1 | 30.00 | 100.0% | 0.040 (n=1) | 100% |" in text
    assert "Wallets: reviewed EUR 990.00, model EUR 1,030.00" in text
    assert "too early to judge CLV" in text


def test_digest_never_overwrites():
    w.world()
    first = digest.write(MONDAY)
    second = digest.write(MONDAY)
    assert first != second and second.name == "2026-11-02-2.md"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
