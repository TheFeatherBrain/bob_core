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
import settle  # noqa: E402
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
        "NEW TIP: Paris Basketball vs Valencia Basket\n"
        "Fri 23 Oct, 21:45 Athens time, round 7\n"
        "\n"
        "• Bet: Paris Basketball -4.5 (handicap)\n"
        "• Wins if Paris Basketball win by 5 or more\n"
        "• Odds: 1.91 on Stoiximan; bet365 1.95 at -5.5\n"
        "• Stake: EUR 34 (simulated; wallet EUR 1,000)\n"
        "• If it wins: EUR 64.94 back, EUR 30.94 profit\n"
        "• Model: 57.2% chance, edge 9.3%\n"
        "\n"
        "Why: Both rotations full; Valencia on a back-to-back.\n"
        "Ref: E2026-69-side")


def test_tip_text_moneyline_total_and_no_other_book():
    ml = notify.tip_text(dict(TIP, market="moneyline", side="away", line=None, other_book_odds={}))
    assert "• Bet: Valencia Basket to win\n• Odds: 1.91 on Stoiximan; bet365 has no price\n" in ml
    assert "Wins if" not in ml
    tot = dict(TIP, market="total", side="over", line=160.5, other_book_odds={"bet365": {"line": 160.5, "odds": 1.87}})
    assert ("• Bet: Over 160.5 total points\n• Wins if both teams together score 161 or more points\n"
            "• Odds: 1.91 on Stoiximan; bet365 1.87\n") in notify.tip_text(tot)


def test_halved_stake_says_so():
    assert "• Stake: EUR 34 (simulated, halved after review; wallet EUR 1,000)" in notify.tip_text(
        dict(TIP, review=dict(TIP["review"], decision="halve")))
    assert "halved" not in notify.tip_text(TIP)


def _terms(market: str, side: str, line: float | None) -> str | None:
    return notify.terms(dict(TIP, market=market, side=side, line=line))


def test_terms_match_settlement_for_every_line_shape():
    assert _terms("handicap", "home", -4.5) == "Paris Basketball win by 5 or more"
    assert _terms("handicap", "home", -1.5) == "Paris Basketball win by 2 or more"
    assert _terms("handicap", "home", -0.5) == "Paris Basketball win"
    assert _terms("handicap", "away", 0.5) == "Valencia Basket win"
    assert _terms("handicap", "away", 4.5) == "Valencia Basket win, or lose by 4 or less"
    assert _terms("handicap", "home", -4.0) == ("Paris Basketball win by 5 or more; "
                                                "stake back if they win by exactly 4")
    assert _terms("handicap", "away", 4.0) == ("Valencia Basket win, or lose by 3 or less; "
                                               "stake back if they lose by exactly 4")
    assert _terms("total", "under", 164.5) == "both teams together score 164 or fewer points"
    assert _terms("total", "over", 160.0) == ("both teams together score 161 or more points; "
                                              "stake back at exactly 160")
    assert _terms("moneyline", "home", None) is None


def _settled(tip: dict, margin: int, total: int = 160) -> str:
    """settle.result for a game where the tipped side won by `margin` (negative: lost) and `total` was scored."""
    home = (total + margin) / 2 if tip["side"] == "home" else (total - margin) / 2
    return settle.result(tip, {"home_pts": home, "away_pts": total - home})


def test_terms_agree_with_settle_result():
    """The words are checked against what settlement pays, not against how terms() computes them."""
    for side, line in [("home", -4.5), ("away", 4.5), ("home", -4.0), ("away", 4.0), ("home", -0.5), ("away", 7.5)]:
        tip = dict(TIP, market="handicap", side=side, line=line)
        words = notify.terms(tip)
        wins = [m for m in range(-20, 21) if m and _settled(tip, m) == "win"]
        lowest = min(wins)
        expected = (f"by {lowest} or more" if lowest > 1 else "win" if lowest == 1 else f"lose by {-lowest} or less")
        assert expected in words, (line, words)
        pushes = [m for m in range(-20, 21) if m and _settled(tip, m) == "push"]
        assert all(f"by exactly {abs(m)}" in words for m in pushes) and ("exactly" in words) == bool(pushes), (line, words)
    for side, line in [("over", 160.5), ("under", 164.5), ("over", 160.0), ("under", 160.0)]:
        tip = dict(TIP, market="total", side=side, line=line)
        words = notify.terms(tip)
        wins = [t for t in range(100, 250) if _settled(tip, 0, t) == "win"]
        edge = min(wins) if side == "over" else max(wins)
        assert f"score {edge} or {'more' if side == 'over' else 'fewer'}" in words, (side, line, words)
        pushes = [t for t in range(100, 250) if _settled(tip, 0, t) == "push"]
        assert all(f"exactly {t}" in words for t in pushes) and ("exactly" in words) == bool(pushes), (line, words)


def test_long_reason_cut_to_300():
    text = notify.tip_text(dict(TIP, review=dict(TIP["review"], reason="x" * 500)))
    assert "Why: " + "x" * 300 + "\nRef:" in text


def test_update_text_with_trap_note():
    update = {"tip": TIP["id"], "status": "still good", "reason": "Valencia's starting guard ruled out",
              "odds_now": 2.10, "book_now": "bet365", "edge_now": 0.2015, "suggested_stake": 34,
              "note": "the market moved; the model does not see injuries"}
    assert notify.update_text(update, TIP) == (
        "UPDATE: STILL GOOD, keep the bet\n"
        "Paris Basketball vs Valencia Basket, Fri 23 Oct, 21:45 Athens time\n"
        "\n"
        "• Bet: Paris Basketball -4.5 (handicap) at 1.91 on Stoiximan\n"
        "• What changed: Valencia's starting guard ruled out\n"
        "• Odds now: 2.10 on bet365, edge 20.2%\n"
        "• Suggested stake: EUR 34 (was EUR 34)\n"
        "• Note: the market moved; the model does not see injuries\n"
        "\n"
        "Ref: E2026-69-side")


def test_update_text_without_odds():
    update = {"tip": TIP["id"], "status": "withdraw", "reason": "star out", "odds_now": None, "book_now": None,
              "edge_now": None, "suggested_stake": 0, "note": None}
    lines = notify.update_text(update, TIP).splitlines()
    assert lines[0] == "UPDATE: WITHDRAW, do not place it"
    assert "• Odds now: not rechecked" in lines and "• Suggested stake: EUR 0 (was EUR 34)" in lines
    assert not any(line.startswith("• Note") for line in lines)


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
    assert notify.pending()[0]["text"].startswith("NEW TIP: Paris Basketball vs Valencia Basket\n")


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
