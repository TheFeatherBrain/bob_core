#!/usr/bin/env python3
"""The replayed ledger against the broker: shares exactly, cash to the cent.

Why exact: a share or a cent off means a fill, a dividend or a split the books did not record, and
every later order would be sized on numbers that are not real. Why an unresolved mismatch also
blocks: a person must look at each one and record a correction; the agent may never make the books
agree by itself. Fails closed: any difference stops trading.

Usage (library; trade.py and approve.py call it):
    problems = reconcile.check(rows, broker)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402

CENT = 0.005


def differences(book: ledger.Book, broker_positions: dict, broker_cash: float) -> list[str]:
    mine = book.positions()
    out = []
    for symbol in sorted(set(mine) | set(broker_positions)):
        a, b = mine.get(symbol, 0.0), float(broker_positions.get(symbol, 0.0))
        if abs(a - b) > ledger.EPS:
            out.append(f"{symbol}: ledger {a:g} shares, broker {b:g}")
    if abs(book.cash - broker_cash) >= CENT:
        out.append(f"cash: ledger {book.cash:.2f}, broker {broker_cash:.2f}")
    return out


def check(rows: list[dict], broker) -> list[str]:
    found = differences(ledger.replay(rows), broker.positions(), broker.account()["cash"])
    return found + [f"unresolved mismatch {m['id']}" for m in ledger.unresolved_mismatches(rows)]
