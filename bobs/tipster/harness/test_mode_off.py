#!/usr/bin/env python3
"""Mode off means nothing reads or sends (spec 8), in every CLI, not only discovery. Each check
runs with a getter and a sender that fail on use, after a tip was recorded while mode was on.

Run:  python3 harness/test_mode_off.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import closing  # noqa: E402
import discover  # noqa: E402
import news  # noqa: E402
import notify  # noqa: E402
import recheck  # noqa: E402
import review  # noqa: E402
import settle  # noqa: E402
import testkit  # noqa: E402
import tips  # noqa: E402
import tipsterworld as w  # noqa: E402

LATER = dt.datetime(2026, 10, 23, 18, 30, tzinfo=dt.timezone.utc)
TID = "t-E2026-69-side-reviewed"


def _switched_off_after_a_tip() -> None:
    bob = w.world()
    w.price_paris(bet365=w.book(ml={"home": 1.76, "away": 1.80}))
    discover.run(w.NOW, getter=lambda u, h: "{}", environ=w.KEY, sync=w.no_sync)
    review.review("c-1", "keep", "ok", [w.evidence_file()], now=w.NOW)
    manifest = bob / "bob.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('mode = "on"', 'mode = "off"'), encoding="utf-8")
    bobcfg.config.cache_clear()


def _never_sends(text):
    raise AssertionError("sent while mode is off")


def test_notify_sends_nothing():
    _switched_off_after_a_tip()
    assert notify.flush(sender=_never_sends) == {"sent": 0, "pending": 1, "error": None, "off": True}


def test_news_reads_nothing():
    _switched_off_after_a_tip()
    assert news.poll(LATER, getter=w.never)["off"] is True


def test_closing_reads_nothing():
    _switched_off_after_a_tip()
    assert closing.run(LATER, getter=w.never, environ=w.KEY) == (0, ["OK mode off"])
    assert [r for r in tips.rows()[0] if r.get("kind") == "closing"] == []


def test_settle_reads_nothing():
    _switched_off_after_a_tip()
    assert settle.run(LATER, getter=w.never) == (0, ["OK mode off"])


def test_recheck_and_review_refused():
    _switched_off_after_a_tip()
    try:
        recheck.recheck(TID, "news", [w.evidence_file("n")], now=LATER, getter=w.never, environ=w.KEY)
    except recheck.RecheckError as e:
        assert "mode off" in str(e)
    else:
        raise AssertionError("recheck ran while mode is off")
    try:
        review.review("c-1", "keep", "x", [w.evidence_file()], now=w.NOW)
    except review.ReviewError as e:
        assert "mode off" in str(e)
    else:
        raise AssertionError("review ran while mode is off")


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
