#!/usr/bin/env python3
"""Tip and update messages for the person, kept in an outbox and sent through core's telegram.py.

Why an outbox: a send that fails (network, Telegram down) must not lose the message or undo the
tip; the tip stays recorded and on the board, and the message is retried on the next flush, in
order, so an update never arrives before the tip it updates. Why the exact layout: the person
reads these on a phone and acts on them; every number in them comes from the ledger line, never
from Claude's words. Plain text only (telegram.py sends no parse mode). The match, bet and kick-off
wording is shared with the board, so the page and the message never describe a tip differently.

Usage:
    python3 harness/notify.py --flush     # SENT lines, then OK sent=N pending=M (or FAIL: ...)
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import athens  # noqa: E402
import bobcfg  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import teams  # noqa: E402
import telegram  # noqa: E402
import tipstercfg  # noqa: E402

REASON_MAX = 300


def outbox_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "outbox.jsonl"


BOOKS = {"bet365": "bet365", "stoiximan": "Stoiximan", "pinnacle": "Pinnacle"}
ACTIONS = {"still good": "keep the bet", "weaker": "smaller stake", "withdraw": "do not place it",
           "no change": "nothing to do"}


def short_id(tip_id: str) -> str:
    return tip_id.removeprefix("t-").rsplit("-", 1)[0]


def book(name: str) -> str:
    return BOOKS.get(name, name)


def match(tip: dict) -> str:
    return f"{teams.display(tip['home'])} vs {teams.display(tip['away'])}"


def kickoff(tip: dict) -> str:
    return f"{athens.local(stats.parse_utc(tip['start_utc'])):%a %d %b, %H:%M}"


def _line(market: str, line: float) -> str:
    return f"{line:+g}" if market == "handicap" else f"{line:g}"


def _team(tip: dict) -> str:
    return teams.display(tip["home"] if tip["side"] == "home" else tip["away"])


def pick(tip: dict) -> str:
    if tip["market"] == "moneyline":
        return f"{_team(tip)} to win"
    if tip["market"] == "handicap":
        return f"{_team(tip)} {_line('handicap', tip['line'])} (handicap)"
    return f"{tip['side'].capitalize()} {_line('total', tip['line'])} total points"


def _handicap_terms(team: str, line: float) -> str:
    need = math.floor(-line) + 1
    if need >= 2:
        win = f"{team} win by {need} or more"
    elif need >= 0:
        win = f"{team} win"
    else:
        win = f"{team} win, or lose by {-need} or less"
    if line % 1 or line == 0:
        return win
    return f"{win}; stake back if they {'win' if line < 0 else 'lose'} by exactly {abs(line):g}"


def _total_terms(side: str, line: float) -> str:
    points = math.floor(line) + 1 if side == "over" else math.ceil(line) - 1
    win = f"both teams together score {points} or {'more' if side == 'over' else 'fewer'} points"
    return win if line % 1 else f"{win}; stake back at exactly {line:g}"


def terms(tip: dict) -> str | None:
    """When the bet wins, in words, so +4.5 or Under 164.5 needs no betting vocabulary. Matches
    settle.result: the line is the tipped side's, a whole line can push. None for a plain winner bet."""
    if tip["market"] == "moneyline":
        return None
    if tip["market"] == "handicap":
        return _handicap_terms(_team(tip), tip["line"])
    return _total_terms(tip["side"], tip["line"])


def _other(tip: dict) -> str:
    if not tip["other_book_odds"]:
        others = [b for b in tipstercfg.load()["tip_books"] if b != tip["book"]]
        return f"{book(others[0]) if others else 'other book'} has no price"
    name, o = sorted(tip["other_book_odds"].items())[0]
    suffix = "" if o.get("line") in (None, tip["line"]) else f" at {_line(tip['market'], o['line'])}"
    return f"{book(name)} {o['odds']:.2f}{suffix}"


def _stake(tip: dict) -> str:
    halved = ", halved after review" if tip.get("review", {}).get("decision") == "halve" else ""
    return f"EUR {tip['stake']} (simulated{halved}; wallet EUR {tip['wallet_before']:,.0f})"


def tip_text(tip: dict) -> str:
    when = terms(tip)
    back = tip["stake"] * tip["odds"]
    return "\n".join([
        f"NEW TIP: {match(tip)}",
        f"{kickoff(tip)} Athens time, round {tip['round']}",
        "",
        f"• Bet: {pick(tip)}",
        *([f"• Wins if {when}"] if when else []),
        f"• Odds: {tip['odds']:.2f} on {book(tip['book'])}; {_other(tip)}",
        f"• Stake: {_stake(tip)}",
        f"• If it wins: EUR {back:,.2f} back, EUR {back - tip['stake']:,.2f} profit",
        f"• Model: {tip['p_win']:.1%} chance, edge {tip['edge']:.1%}",
        "",
        f"Why: {tip['review']['reason'][:REASON_MAX]}",
        f"Ref: {short_id(tip['id'])}",
    ])


def update_text(update: dict, tip: dict) -> str:
    action = ACTIONS.get(update["status"])
    if update.get("odds_now") is None:
        now = "not rechecked"
    else:
        now = f"{update['odds_now']:.2f} on {book(update['book_now'])}, edge {update['edge_now']:.1%}"
    return "\n".join([
        f"UPDATE: {update['status'].upper()}" + (f", {action}" if action else ""),
        f"{match(tip)}, {kickoff(tip)} Athens time",
        "",
        f"• Bet: {pick(tip)} at {tip['odds']:.2f} on {book(tip['book'])}",
        f"• What changed: {update['reason'][:REASON_MAX]}",
        f"• Odds now: {now}",
        f"• Suggested stake: EUR {update['suggested_stake']} (was EUR {tip['stake']})",
        *([f"• Note: {update['note']}"] if update.get("note") else []),
        "",
        f"Ref: {short_id(tip['id'])}",
    ])


def _rows() -> list[dict]:
    return store.read_jsonl(outbox_path())[0]


def pending() -> list[dict]:
    rows = _rows()
    sent = {r["sent"] for r in rows if "sent" in r}
    return [r for r in rows if "id" in r and r["id"] not in sent and not r.get("held")]


def queue(kind: str, text: str, message_id: str) -> None:
    """A message telegram.py would refuse (empty, too long, token-shaped) is kept with the reason as
    `held`, so it is visible and never blocks the messages after it."""
    if any(r.get("id") == message_id for r in _rows()):
        return
    row = {"id": message_id, "kind": kind, "text": text}
    problems = telegram.lint(text)
    if problems:
        row["held"] = problems
    store.append_jsonl(outbox_path(), [row])


def queue_tip(tip: dict) -> None:
    queue("tip", tip_text(tip), f"tip:{tip['id']}")


def queue_update(update: dict, tip: dict) -> None:
    queue("update", update_text(update, tip), f"update:{tip['id']}:{update['at']}")


def flush(sender=telegram.send) -> dict:
    if not tipstercfg.is_on():
        return {"sent": 0, "pending": len(pending()), "error": None, "off": True}
    sent = 0
    for message in pending():
        try:
            message_id = sender(message["text"])
        except Exception as e:  # noqa: BLE001 - any failure keeps the message for the next flush
            return {"sent": sent, "pending": len(pending()), "error": str(e)}
        store.append_jsonl(outbox_path(), [{"sent": message["id"], "message_id": message_id}])
        sent += 1
    return {"sent": sent, "pending": 0, "error": None}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Send the outbox to Telegram, in order.")
    parser.add_argument("--flush", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        result = flush()
    except bobcfg.ManifestError as e:
        print(f"FAIL: {e}")
        return 1
    if result.get("off"):
        print(f"OK mode off: nothing sent (pending={result['pending']})")
        return 0
    if result["error"]:
        print(f"FAIL: {result['error']} (sent={result['sent']}, pending={result['pending']}; retried next flush)")
        return 1
    print(f"OK sent={result['sent']} pending={result['pending']}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
