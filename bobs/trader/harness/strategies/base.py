#!/usr/bin/env python3
"""What every strategy gets and returns. A strategy proposes; the core decides.

Why proposals only: sizing, flags, caps, reconcile and the approval queue live in the core, so a
new strategy (stage 2) cannot skip a guard by forgetting it. The order id is built from session,
strategy, side and symbol: a re-run of the same session produces the same ids, and trade.py drops
any id the ledger already has.

Usage (library):
    ctx = base.Context(...); proposals = scout_calls.propose(ctx)
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402


@dataclasses.dataclass
class Context:
    session: str
    today: dt.date
    book: ledger.Book
    feed: dict
    rows: list[dict]
    cfg: dict
    closes: dict[str, float]
    since: str


def order_id(session: str, strategy: str, side: str, symbol: str) -> str:
    return f"{session}-{strategy}-{side}-{symbol}"
