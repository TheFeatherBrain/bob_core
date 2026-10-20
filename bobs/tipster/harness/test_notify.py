#!/usr/bin/env python3
"""notify: exact tip and update texts, an outbox sent in order, a failed send retried next flush.

Run:  python3 harness/test_notify.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import discover  # noqa: E402
import notify  # noqa: E402
import recheck  # noqa: E402
import review  # noqa: E402
import store  # noqa: E402
import telegram  # noqa: E402
import testkit  # noqa: E402
import tipsterworld as w  # noqa: E402

TIP = {"id": "t-E2026-69-side-reviewed", "round": 7, "game": "E2026-69", "home": "PRS", "away": "PAM",
       "start_utc": "2026-10-23T18:45:00Z", "market": "handicap", "side": "home", "line": -4.5,
       "book": "stoiximan", "odds": 1.91, "other_book_odds": {"bet365": {"line": -5.5, "odds": 1.95}},
       "p_win": 0.5723, "edge": 0.0931, "stake": 34, "wallet_before": 1000,
       "review": {"decision": "keep", "reason": "Both rotations full; Valencia on a back-to-back.", "evidence": []}}


def test_tip_text_exact():
    assert notify.tip_text(TIP) == (
        "TIP E2026-69-side (round 7)\n"
        "Paris Basketball vs Valencia Basket, Fri 23 Oct 21:45 Athens\n"
        "Paris Basketball -4.5 at 1.91 (stoiximan); bet365 1.95 at -5.5\n"
        "Model 57.2%, edge 9.3%\n"
        "Stake EUR 34 of EUR 1,000 (simulated)\n"
        "Why: Both rotations full; Valencia on a back-to-back.")


def test_tip_text_moneyline_total_and_no_other_book():
    ml = dict(TIP, market="moneyline", side="away", line=None, other_book_odds={})
    assert "Valencia Basket to win at 1.91 (stoiximan); bet365 n/a" in notify.tip_text(ml)
    tot = dict(TIP, market="total", side="over", line=160.5, other_book_odds={"bet365": {"line": 160.5, "odds": 1.87}})
    assert "Over 160.5 at 1.91 (stoiximan); bet365 1.87\n" in notify.tip_text(tot)


def test_long_reason_cut_to_300():
    text = notify.tip_text(dict(TIP, review=dict(TIP["review"], reason="x" * 500)))
    assert text.endswith("Why: " + "x" * 300)


def test_update_text_with_trap_note():
    update = {"tip": TIP["id"], "status": "still good", "reason": "Valencia's starting guard ruled out",
              "odds_now": 2.10, "book_now": "bet365", "edge_now": 0.2015, "suggested_stake": 34,
              "note": "the market moved; the model does not see injuries"}
    assert notify.update_text(update, TIP) == (
        "UPDATE E2026-69-side: STILL GOOD\n"
        "What changed: Valencia's starting guard ruled out\n"
        "Now: 2.10 (bet365), edge 20.2%, suggested stake EUR 34\n"
        "the market moved; the model does not see injuries")


def test_update_text_without_odds():
    update = {"tip": TIP["id"], "status": "withdraw", "reason": "star out", "odds_now": None, "book_now": None,
              "edge_now": None, "suggested_stake": 0, "note": "odds not rechecked: budget"}
    assert notify.update_text(update, TIP).splitlines()[2] == "Now: odds not rechecked, suggested stake EUR 0"


def test_texts_pass_lint():
    assert telegram.lint(notify.tip_text(TIP)) == []
    assert telegram.lint(notify.tip_text(dict(TIP, review=dict(TIP["review"], reason="y" * 5000)))) == []


def test_flush_sends_in_order_and_marks_sent():
    w.world()
    notify.queue("tip", "first", "a")
    notify.queue("update", "second", "b")
    sent = []
    result = notify.flush(sender=lambda text: sent.append(text) or len(sent))
    assert sent == ["first", "second"] and result == {"sent": 2, "pending": 0, "error": None}
    assert notify.flush(sender=lambda text: 1 / 0) == {"sent": 0, "pending": 0, "error": None}


def test_failed_send_retried_next_flush():
    w.world()
    notify.queue("tip", "first", "a")
    notify.queue("tip", "second", "b")

    def broken(text):
        raise telegram.TelegramError("HTTP 502")
    result = notify.flush(sender=broken)
    assert result == {"sent": 0, "pending": 2, "error": "HTTP 502"}
    sent = []
    assert notify.flush(sender=lambda text: sent.append(text) or 7)["sent"] == 2 and sent == ["first", "second"]


def test_message_failing_lint_is_held_and_does_not_block():
    w.world()
    notify.queue("update", "token 123456789:" + "A" * 35, "a")
    notify.queue("tip", "second", "b")
    sent = []
    assert notify.flush(sender=lambda text: sent.append(text) or 1)["sent"] == 1 and sent == ["second"]
    held = [r for r in notify._rows() if r.get("id") == "a"][0]
    assert held["held"] and "token" in held["held"][0]


def test_queue_is_idempotent_by_id():
    w.world()
    notify.queue("tip", "first", "a")
    notify.queue("tip", "first again", "a")
    assert notify.pending() == [{"id": "a", "kind": "tip", "text": "first"}]


def test_review_and_recheck_queue_messages():
    w.world()
    w.price_paris(bet365=w.book(ml={"home": 1.76, "away": 1.80}))
    discover.run(w.NOW, getter=lambda u, h: "{}", environ=w.KEY, sync=w.no_sync)
    review.review("c-1", "keep", "full strength", [w.evidence_file()], now=w.NOW)
    later = w.NOW.replace(hour=10)
    recheck.recheck("t-E2026-69-side-reviewed", "news", [w.evidence_file("n")], now=later,
                    getter=lambda u, h: "{}", environ=w.KEY)
    recheck.recheck("t-E2026-69-side-reviewed", "football", [w.evidence_file("n")], now=later,
                    getter=w.never, environ=w.KEY, no_change=True)
    kinds = [m["kind"] for m in notify.pending()]
    assert kinds == ["tip", "update"], kinds
    assert notify.pending()[0]["text"].startswith("TIP E2026-69-side (round 7)\nParis Basketball vs Valencia Basket")


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
