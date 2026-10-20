#!/usr/bin/env python3
"""Tip and update messages for the person, kept in an outbox and sent through core's telegram.py.

Why an outbox: a send that fails (network, Telegram down) must not lose the message or undo the
tip; the tip stays recorded and on the board, and the message is retried on the next flush, in
order, so an update never arrives before the tip it updates. Why the exact layout: the person
reads these on a phone and acts on them; every number in them comes from the ledger line, never
from Claude's words. Plain text only (telegram.py sends no parse mode).

Usage:
    python3 harness/notify.py --flush     # SENT lines, then OK sent=N pending=M (or FAIL: ...)
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import teams  # noqa: E402
import telegram  # noqa: E402
import tipstercfg  # noqa: E402

ATHENS = zoneinfo.ZoneInfo("Europe/Athens")
REASON_MAX = 300


def outbox_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "outbox.jsonl"


def short_id(tip_id: str) -> str:
    return tip_id.removeprefix("t-").rsplit("-", 1)[0]


def _line(market: str, line: float) -> str:
    return f"{line:+g}" if market == "handicap" else f"{line:g}"


def _pick(tip: dict) -> str:
    team = teams.display(tip["home"] if tip["side"] == "home" else tip["away"])
    if tip["market"] == "moneyline":
        return f"{team} to win"
    if tip["market"] == "handicap":
        return f"{team} {_line('handicap', tip['line'])}"
    return f"{tip['side'].capitalize()} {_line('total', tip['line'])}"


def _other(tip: dict) -> str:
    if not tip["other_book_odds"]:
        others = [b for b in tipstercfg.load()["tip_books"] if b != tip["book"]]
        return f"{others[0] if others else 'other book'} n/a"
    book, o = sorted(tip["other_book_odds"].items())[0]
    suffix = "" if o.get("line") in (None, tip["line"]) else f" at {_line(tip['market'], o['line'])}"
    return f"{book} {o['odds']:.2f}{suffix}"


def tip_text(tip: dict) -> str:
    when = stats.parse_utc(tip["start_utc"]).astimezone(ATHENS)
    return "\n".join([
        f"TIP {short_id(tip['id'])} (round {tip['round']})",
        f"{teams.display(tip['home'])} vs {teams.display(tip['away'])}, {when:%a %d %b %H:%M} Athens",
        f"{_pick(tip)} at {tip['odds']:.2f} ({tip['book']}); {_other(tip)}",
        f"Model {tip['p_win']:.1%}, edge {tip['edge']:.1%}",
        f"Stake EUR {tip['stake']} of EUR {tip['wallet_before']:,.0f} (simulated)",
        f"Why: {tip['review']['reason'][:REASON_MAX]}",
    ])


def update_text(update: dict, tip: dict) -> str:
    if update.get("odds_now") is None:
        now = f"Now: odds not rechecked, suggested stake EUR {update['suggested_stake']}"
    else:
        now = (f"Now: {update['odds_now']:.2f} ({update['book_now']}), edge {update['edge_now']:.1%}, "
               f"suggested stake EUR {update['suggested_stake']}")
    lines = [f"UPDATE {short_id(tip['id'])}: {update['status'].upper()}",
             f"What changed: {update['reason'][:REASON_MAX]}", now]
    if update.get("note"):
        lines.append(update["note"])
    return "\n".join(lines)


def _rows() -> list[dict]:
    return store.read_jsonl(outbox_path())[0]


def pending() -> list[dict]:
    rows = _rows()
    sent = {r["sent"] for r in rows if "sent" in r}
    return [r for r in rows if "id" in r and r["id"] not in sent]


def queue(kind: str, text: str, message_id: str) -> None:
    if any(r.get("id") == message_id for r in _rows()):
        return
    store.append_jsonl(outbox_path(), [{"id": message_id, "kind": kind, "text": text}])


def queue_tip(tip: dict) -> None:
    queue("tip", tip_text(tip), f"tip:{tip['id']}")


def queue_update(update: dict, tip: dict) -> None:
    queue("update", update_text(update, tip), f"update:{tip['id']}:{update['at']}")


def flush(sender=telegram.send) -> dict:
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
    if result["error"]:
        print(f"FAIL: {result['error']} (sent={result['sent']}, pending={result['pending']}; retried next flush)")
        return 1
    print(f"OK sent={result['sent']} pending={result['pending']}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
