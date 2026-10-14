#!/usr/bin/env python3
"""Stage 1 strategy: buy the Scout's open v2 calls, sell by the exit rules fixed at entry.

Why calls since the Trader started: switching the Trader on must not buy a year of old calls at
once. Why a refused buy is proposed again: a refusal for HALT, a daily loss or a stale price is
about that day, not about the call; only an accepted proposal that reached the broker (submitted,
queued or filled) ends the call's entry, so a buy lost to a crash or a broker refusal is retried.
Exit rules are copied onto the buy when it is proposed, so changing them in bob.toml later never
moves the stop of a position already held. A holding whose sell is already queued or submitted
and not yet filled or expired gets no second sell. Order: the Scout withdrew the call, the stop on the
split-adjusted close, the target, the holding limit.

Usage (library; trade.py calls propose(ctx)).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traderpath  # noqa: E402,F401
import scout_feed  # noqa: E402
from strategies.base import Context, order_id  # noqa: E402

NAME = "scout_calls"
TOLERANCE = 1e-9


def exit_rules(cfg: dict) -> dict:
    return {"stop_loss": cfg["stop_loss"], "take_profit": cfg["take_profit"], "max_hold_days": cfg["max_hold_days"]}


def _exit_reason(close: float | None, entry: float, opened: str, today: dt.date, rules: dict,
                 withdrawn: bool) -> str | None:
    if withdrawn:
        return "the Scout withdrew the call"
    stop = entry * (1 - rules["stop_loss"])
    if close is not None and close <= stop + TOLERANCE:
        return f"stop: close {close:.2f} at or below {stop:.2f}"
    target = entry * (1 + rules["take_profit"])
    if rules.get("take_profit") and close is not None and close >= target - TOLERANCE:
        return f"target: close {close:.2f} at or above {target:.2f}"
    if (today - dt.date.fromisoformat(opened)).days >= rules["max_hold_days"]:
        return f"held {rules['max_hold_days']} days"
    return None


def _sells_on_their_way(rows: list[dict]) -> set[str]:
    settled = {r.get("order_id") for r in rows if r.get("kind") in ("filled", "expired", "refused")}
    waiting = {r.get("order_id") for r in rows if r.get("kind") in ("queued", "submitted")} - settled
    return {r["symbol"] for r in rows if r.get("kind") == "proposed" and r.get("side") == "sell"
            and r.get("order_id") in waiting}


def sells(ctx: Context) -> list[dict]:
    out = []
    pending = _sells_on_their_way(ctx.rows)
    for symbol in sorted({lot.symbol for lot in ctx.book.lots if lot.strategy == NAME} - pending):
        lots = [lot for lot in ctx.book.lots_of(symbol) if lot.strategy == NAME]
        qty = round(sum(lot.qty for lot in lots), 6)
        entry = sum(lot.qty * lot.price for lot in lots) / qty
        first = lots[0]
        reason = _exit_reason(ctx.closes.get(symbol), entry, first.opened, ctx.today, first.exit_rules,
                              first.call_id in ctx.feed["withdrawn"])
        if reason:
            out.append({"order_id": order_id(ctx.session, NAME, "sell", symbol), "strategy": NAME, "symbol": symbol,
                        "side": "sell", "qty": qty, "reason": reason, "call_id": first.call_id})
    return out


def buys(ctx: Context) -> list[dict]:
    held = set(ctx.book.positions())
    reached = {r.get("order_id") for r in ctx.rows if r.get("kind") in ("submitted", "queued", "filled")}
    entered = {r.get("call_id") for r in ctx.rows if r.get("kind") == "proposed" and r.get("side") == "buy"
               and r.get("decision") == "accepted" and r.get("order_id") in reached}
    out = []
    for call in scout_feed.open_calls(ctx.feed, ctx.today):
        if call["symbol"] in held or call["id"] in entered or call["session"] < ctx.since:
            continue
        out.append({"order_id": order_id(ctx.session, NAME, "buy", call["symbol"]), "strategy": NAME,
                    "symbol": call["symbol"], "side": "buy", "qty": None, "reason": f"Scout call {call['id']}",
                    "call_id": call["id"], "card": call["card"], "exit_rules": exit_rules(ctx.cfg)})
    return out


def propose(ctx: Context) -> list[dict]:
    return sells(ctx) + buys(ctx)
