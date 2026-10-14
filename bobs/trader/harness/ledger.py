#!/usr/bin/env python3
"""The Trader's ledger: every proposal, refusal, order, fill, dividend, split, cash movement,
mismatch and correction, append-only. Lots, positions, cash and realized gains are rebuilt by
replaying it, never stored anywhere else.

Why replay: one source of truth means one thing to check against the broker. A positions file that
drifted from the order history would make reconcile compare two copies of the same mistake.
Why append-only: a duplicate is recoverable, an overwrite is not. A torn line is skipped and
counted; the replay then disagrees with the broker and reconcile stops trading, so nothing is lost
quietly.
Why oldest lot first: one fixed rule, so realized gains are the same however often they are
recomputed.
Why corrections are the person's: the agent may never make its own books agree with the broker.
correct needs a terminal and a typed confirmation, and order_gate refuses it to the agent.

Usage:
    python3 harness/ledger.py book                       # cash, positions and open lots
    python3 harness/ledger.py summary --days 7           # fills and refusals in the last N days
    python3 harness/ledger.py correct --note "..." [--clears MISMATCH_ID] [--cash-delta X] [--shares SYM=QTY@PRICE ...]
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import store  # noqa: E402
import tradercfg  # noqa: E402

KINDS = ("proposed", "refused", "queued", "approved", "submitted", "filled", "expired", "dividend", "split",
         "cash", "mismatch", "correction")
EPS = 1e-6
CONFIRM_WORD = "CORRECT"


class Refused(Exception):
    pass


@dataclasses.dataclass
class Lot:
    symbol: str
    qty: float
    price: float
    opened: str
    order_id: str
    strategy: str | None
    exit_rules: dict
    call_id: str | None


@dataclasses.dataclass
class Book:
    cash: float = 0.0
    lots: list[Lot] = dataclasses.field(default_factory=list)
    closed: list[dict] = dataclasses.field(default_factory=list)

    def positions(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for lot in self.lots:
            out[lot.symbol] = round(out.get(lot.symbol, 0.0) + lot.qty, 6)
        return {s: q for s, q in out.items() if q > EPS}

    def lots_of(self, symbol: str) -> list[Lot]:
        return [lot for lot in self.lots if lot.symbol == symbol]


def line(kind: str, now: dt.datetime, session: str | None, **fields) -> dict:
    return {"id": f"{kind}-{session or 'none'}-{uuid.uuid4().hex[:8]}", "kind": kind,
            "at": now.isoformat(timespec="seconds"), "session": session, **fields}


def fill_amount(side: str, qty: float, price: float, commission: float) -> float:
    gross = round(qty * price, 2)
    return round(-(gross + commission), 2) if side == "buy" else round(gross - commission, 2)


def path() -> pathlib.Path:
    return tradercfg.state_dir() / "ledger.jsonl"


def read(where: pathlib.Path | None = None) -> tuple[list[dict], int]:
    return store.read_jsonl(where or path())


def append(rows: list[dict], where: pathlib.Path | None = None) -> None:
    store.append_jsonl(where or path(), rows)


def _consume(book: Book, symbol: str, qty: float, price: float, session: str) -> None:
    left = qty
    for lot in book.lots_of(symbol):
        if left <= EPS:
            break
        take = min(lot.qty, left)
        lot.qty = round(lot.qty - take, 6)
        left = round(left - take, 6)
        book.closed.append({"symbol": symbol, "qty": take, "buy_price": lot.price, "sell_price": price,
                            "opened": lot.opened, "closed": session, "pnl": round(take * (price - lot.price), 2),
                            "strategy": lot.strategy, "order_id": lot.order_id, "full": lot.qty <= EPS})
    book.lots = [lot for lot in book.lots if lot.qty > EPS]


def replay(rows: list[dict]) -> Book:
    book = Book()
    proposals = {r["order_id"]: r for r in rows if r.get("kind") == "proposed"}
    for r in rows:
        kind = r.get("kind")
        if kind in ("cash", "dividend"):
            book.cash = round(book.cash + r["amount"], 2)
        elif kind == "filled":
            book.cash = round(book.cash + fill_amount(r["side"], r["qty"], r["price"], r["commission"]), 2)
            if r["side"] == "buy":
                origin = proposals.get(r["order_id"], {})
                book.lots.append(Lot(r["symbol"], float(r["qty"]), float(r["price"]), r["session"], r["order_id"],
                                     origin.get("strategy"), origin.get("exit_rules") or {}, origin.get("call_id")))
            else:
                _consume(book, r["symbol"], float(r["qty"]), float(r["price"]), r["session"])
        elif kind == "split":
            for lot in book.lots_of(r["symbol"]):
                lot.qty = round(lot.qty * r["ratio"], 6)
                lot.price = lot.price / r["ratio"]
        elif kind == "correction":
            book.cash = round(book.cash + r.get("cash_delta", 0.0), 2)
            for symbol, (qty, price) in (r.get("share_deltas") or {}).items():
                if qty > 0:
                    book.lots.append(Lot(symbol, float(qty), float(price), r["session"] or "", r["id"], None, {}, None))
                else:
                    _consume(book, symbol, -float(qty), float(price), r["session"] or "")
    return book


def unresolved_mismatches(rows: list[dict]) -> list[dict]:
    cleared = {r.get("clears") for r in rows if r.get("kind") == "correction"}
    return [r for r in rows if r.get("kind") == "mismatch" and r["id"] not in cleared]


def confirm(expected: str, stdin=sys.stdin, ask=input) -> None:
    if not stdin.isatty():
        raise Refused("needs a person at a terminal; the agent may not run this (order_gate refuses it too)")
    if ask(f"Type {expected} to confirm: ").strip() != expected:
        raise Refused("confirmation did not match; nothing written")


def correct(note: str, now: dt.datetime, clears: str | None = None, cash_delta: float = 0.0,
            share_deltas: dict | None = None, stdin=sys.stdin, ask=input) -> dict:
    confirm(clears or CONFIRM_WORD, stdin, ask)
    row = line("correction", now, now.date().isoformat(), note=note, clears=clears, cash_delta=cash_delta,
               share_deltas=share_deltas or {})
    append([row])
    return row


def summary(rows: list[dict], since: str) -> list[str]:
    out = []
    for r in rows:
        if (r.get("session") or "") < since:
            continue
        if r.get("kind") == "filled":
            out.append(f"{r['session']} filled {r['side']} {r['qty']:g} {r['symbol']} @ {r['price']:.2f}")
        elif r.get("kind") == "refused":
            out.append(f"{r['session']} refused {r['order_id']}: {r['reason']}")
        elif r.get("kind") in ("mismatch", "correction", "expired"):
            out.append(f"{r['session']} {r['kind']} {r['id']}")
    return out or ["nothing in this window"]


def _shares(items: list[str]) -> dict:
    out = {}
    for item in items:
        symbol, _, rest = item.partition("=")
        qty, _, price = rest.partition("@")
        out[symbol.upper()] = [float(qty), float(price)]
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("book")
    s = sub.add_parser("summary")
    s.add_argument("--days", type=int, default=7)
    c = sub.add_parser("correct")
    c.add_argument("--note", required=True)
    c.add_argument("--clears")
    c.add_argument("--cash-delta", type=float, default=0.0)
    c.add_argument("--shares", nargs="*", default=[])
    a = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)
    if a.command == "correct":
        try:
            print(f"OK {correct(a.note, now, a.clears, a.cash_delta, _shares(a.shares))['id']}")
        except Refused as e:
            print(f"REFUSED: {e}")
            return 1
        return 0
    rows, torn = read()
    if a.command == "summary":
        print("\n".join(summary(rows, (now.date() - dt.timedelta(days=a.days)).isoformat())))
    else:
        book = replay(rows)
        print(f"cash {book.cash:.2f}")
        for lot in book.lots:
            print(f"{lot.symbol:8} {lot.qty:g} @ {lot.price:.4f} opened {lot.opened} ({lot.strategy}, {lot.order_id})")
    if torn:
        print(f"WARN: {torn} torn line(s) skipped in {path()}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
