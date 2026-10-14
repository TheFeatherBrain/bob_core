#!/usr/bin/env python3
"""A person approves one queued live order by id; the books, prices and caps are checked again first.

Why again: time has passed since the order was queued; prices, cash and other fills may have moved.
Why a terminal and a typed-back id: approval is the person's act. The agent has no terminal, and
order_gate refuses any agent tool call that names this script.

Usage (the person, in their own shell or with ! in Claude Code):
    python3 harness/approve.py ORDER_ID
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import caps  # noqa: E402
import fetch  # noqa: E402
import ledger  # noqa: E402
import marketclock  # noqa: E402
import reconcile  # noqa: E402
import trade  # noqa: E402
import tradercfg  # noqa: E402
from broker import base as broker_base  # noqa: E402

Refused = ledger.Refused


def approve(order_id: str, now: dt.datetime, adapter=broker_base.adapter_for, stdin=sys.stdin, ask=input) -> list[dict]:
    cfg = tradercfg.load()
    if cfg["mode"] != "live":
        raise Refused(f"approve is for live mode; mode is {cfg['mode']}")
    ledger.confirm(order_id, stdin, ask)
    rows, _ = ledger.read()
    lines = [r for r in rows if r.get("order_id") == order_id]
    if not any(r["kind"] == "queued" for r in lines):
        raise Refused(f"no queued order {order_id}")
    if any(r["kind"] in ("approved", "submitted", "expired") for r in lines):
        raise Refused(f"{order_id} was already approved, submitted or expired")
    proposal = next(r for r in lines if r["kind"] == "proposed")
    try:
        broker = adapter("live")
    except broker_base.AdapterError as e:
        raise Refused(str(e)) from None
    problems = reconcile.check(rows, broker)
    if problems:
        raise Refused("reconcile: " + "; ".join(problems))
    session = marketclock.last_session(bars.sessions(), now)
    book = ledger.replay(rows)
    stale = [s for s in [bars.BENCHMARK, proposal["symbol"], *book.positions()] if session is None or not bars.fresh(s, session)]
    if stale:
        raise Refused(f"no current bar for {', '.join(stale)}")
    closes = {s: bars.close_on(s, session) for s in book.positions()}
    if proposal["side"] == "buy":
        exp = caps.exposure(book, closes, trade.equity_of(book, closes))
        value = round(proposal["qty"] * bars.close_on(proposal["symbol"], session), 2)
        breach = caps.check_buy(exp, proposal["symbol"], value, proposal["strategy"], cfg)
        if breach:
            raise Refused(breach)
    approved = ledger.line("approved", now, session, order_id=order_id)
    ledger.append([approved])
    broker.submit({"order_id": order_id, "symbol": proposal["symbol"], "side": proposal["side"],
                   "qty": proposal["qty"], "session": session})
    submitted = ledger.line("submitted", now, session, order_id=order_id)
    ledger.append([submitted])
    return [approved, submitted]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("order_id")
    a = ap.parse_args(argv)
    try:
        approve(a.order_id, fetch.utcnow())
    except Refused as e:
        print(f"REFUSED: {e}")
        return 1
    print(f"OK submitted {a.order_id}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
