#!/usr/bin/env python3
"""The Trader's daily run: collect, reconcile, check inputs, propose, flag, size, cap, act, record.

Why this order: fills first, so the books include last night's orders before they are compared;
reconcile before anything new, because an order placed on books that disagree with the broker
compounds the error; sells before buys, so exits never wait on cash or caps. Every decision is a
ledger line, refusals included: a refused order is an answer, not something to work around.

Stops on: mode off (no broker or network call at all); live without a passing go-live report or an
override; a missing calendar; any reconcile difference; an unreadable Scout ledger; a stale bar
for SPY or for a held symbol. A stale bar for a buy candidate refuses that buy only, so one halted
stock never blocks the exits of the whole book. HALT and the daily loss limit stop buys, not sells.

Usage:
    python3 harness/trade.py --run      # OK session=... placed=N refused=M queued=K, or FAIL: ...
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
import promotion  # noqa: E402
import reconcile  # noqa: E402
import risk_flags  # noqa: E402
import scout_feed  # noqa: E402
import sizing  # noqa: E402
import tradercfg  # noqa: E402
from broker import base as broker_base  # noqa: E402
from strategies import base as strategy_base, scout_calls  # noqa: E402

STRATEGIES = (scout_calls,)


def halt_path() -> pathlib.Path:
    return tradercfg.state_dir() / "HALT"


def equity_of(book: ledger.Book, closes: dict[str, float]) -> float:
    return round(book.cash + sum(q * closes[s] for s, q in book.positions().items()), 2)


def run(now: dt.datetime, adapter=broker_base.adapter_for) -> tuple[int, str]:
    cfg = tradercfg.load()
    if cfg["mode"] == "off":
        promotion.record_run(now, None, cfg, "OK", None)
        return 0, "OK mode=off"
    history = promotion.read_history()
    code, message, session, equity = _run(now, cfg, adapter, history)
    promotion.record_run(now, session, cfg, "OK" if code == 0 else "FAIL", equity)
    return code, message


def _collect(now: dt.datetime, session: str, broker, cfg: dict) -> None:
    rows, _ = ledger.read()
    new = []
    if cfg["mode"] == "simulate" and not any(r.get("kind") == "cash" for r in rows):
        new.append(ledger.line("cash", now, session, amount=float(cfg["sim_starting_cash"]),
                               note="simulator starting cash"))
    seen = {r.get("activity_id") for r in rows}
    for a in broker.activities():
        if a["activity_id"] not in seen:
            new.append(ledger.line(a["kind"], now, a["session"], **{k: v for k, v in a.items() if k not in ("kind", "session")}))
    if new:
        ledger.append(new)


def _no_buys(equity: float, history: list[dict], cfg: dict) -> str | None:
    if halt_path().exists():
        return "HALT: " + (halt_path().read_text(encoding="utf-8").strip().splitlines() or ["no reason given"])[-1]
    prev = promotion.last_ok_equity(history)
    if prev and equity < prev * (1 - cfg["daily_loss_limit"]):
        return f"equity {equity:.2f} down more than {cfg['daily_loss_limit']:.0%} since the last run ({prev:.2f})"
    return None


def _buy(p: dict, cfg: dict, exp: caps.Exposure, equity: float, blocked: str | None, buy_pct, macro: dict,
         session: str) -> tuple[dict, str | None]:
    info = {"card": p["card"], "exit_rules": p["exit_rules"], "macro": macro, "caps": None, "qty": None,
            "price": None, "sizing": None, "flags": []}
    if blocked:
        return info, blocked
    if not bars.fresh(p["symbol"], session):
        return info, f"no {session} bar for {p['symbol']}"
    price = bars.close_on(p["symbol"], session)
    sized = sizing.size(equity, price, p["card"].get("rank_pct"), macro, cfg, buy_pct)
    found = risk_flags.flags(p["card"], macro, sized["value"], bars.adv(p["symbol"]), cfg)
    info.update(qty=sized["qty"], price=price, sizing=sized, flags=found)
    if sized["qty"] < 1 or sized["value"] < cfg["min_order_value"]:
        return info, f"order value {sized['value']:.2f} below min_order_value {cfg['min_order_value']}"
    stop = risk_flags.blocking(found, cfg)
    if stop:
        return info, f"blocked by flags {stop}"
    breach = caps.check_buy(exp, p["symbol"], sized["value"], p["strategy"], cfg)
    if breach:
        info["caps"] = "refused"
        return info, breach
    caps.accept(exp, p["symbol"], sized["value"], p["strategy"], cfg)
    info["caps"] = "ok"
    return info, None


def _act(now: dt.datetime, session: str, p: dict, info: dict, why: str | None, mode: str, broker) -> str:
    proposed = ledger.line("proposed", now, session, order_id=p["order_id"], strategy=p["strategy"], symbol=p["symbol"],
                           side=p["side"], call_id=p.get("call_id"), reason=p["reason"],
                           decision="refused" if why else "accepted", **info)
    if why:
        ledger.append([proposed, ledger.line("refused", now, session, order_id=p["order_id"], reason=why)])
        return "refused"
    if mode == "live":
        ledger.append([proposed, ledger.line("queued", now, session, order_id=p["order_id"])])
        return "queued"
    ledger.append([proposed])
    broker.submit({"order_id": p["order_id"], "symbol": p["symbol"], "side": p["side"], "qty": info["qty"],
                   "session": session})
    ledger.append([ledger.line("submitted", now, session, order_id=p["order_id"])])
    return "placed"


def _run(now: dt.datetime, cfg: dict, adapter, history: list[dict]) -> tuple[int, str, str | None, float | None]:
    mode, warn = cfg["mode"], ""
    if mode == "live" and not cfg["override_promotion"] and not promotion.latest_passing(now):
        return 1, ("FAIL: live needs a passing go-live report from the last 30 days (promotion.py --report) "
                   "or override_promotion in bob.toml"), None, None
    if mode == "live" and cfg["override_promotion"]:
        warn = f" WARN override_promotion: {cfg['override_promotion']}"
    try:
        broker = adapter(mode)
    except broker_base.AdapterError as e:
        return 1, f"FAIL: {e}", None, None
    session = marketclock.last_session(bars.sessions(), now)
    if session is None:
        return 1, "FAIL: no market calendar; run bars.py --refresh", None, None
    _collect(now, session, broker, cfg)
    rows, _ = ledger.read()
    problems = reconcile.check(rows, broker)
    if problems:
        if not ledger.unresolved_mismatches(rows):
            ledger.append([ledger.line("mismatch", now, session, details=problems)])
        return 1, "FAIL: reconcile " + "; ".join(problems) + " (a person clears it with ledger.py correct)", session, None
    try:
        feed = scout_feed.load()
    except scout_feed.FeedError as e:
        return 1, f"FAIL: {e}", session, None
    book = ledger.replay(rows)
    held = sorted(book.positions())
    stale = [s for s in [bars.BENCHMARK, *held] if not bars.fresh(s, session)]
    if stale:
        return 1, f"FAIL: no {session} bar for {', '.join(stale)}; run bars.py --refresh", session, None
    closes = {s: bars.close_on(s, session) for s in held}
    equity = equity_of(book, closes)
    blocked = _no_buys(equity, history, cfg)
    ctx = strategy_base.Context(session=session, today=dt.date.fromisoformat(session), book=book, feed=feed, rows=rows,
                                cfg=cfg, closes={s: bars.close_on(s, session, "split") for s in held},
                                since=promotion.first_active_session(history) or session)
    done = {r.get("order_id") for r in rows}
    proposals = [p for strategy in STRATEGIES for p in strategy.propose(ctx) if p["order_id"] not in done]
    exp = caps.exposure(book, closes, equity)
    buy_pct = tradercfg.scout()["buy_percentile"]
    counts = {"placed": 0, "refused": 0, "queued": 0}
    for p in sorted(proposals, key=lambda p: p["side"] != "sell"):
        if p["side"] == "sell":
            caps.count_sell(exp)
            info, why = {"caps": "exempt", "qty": p["qty"], "price": closes.get(p["symbol"])}, None
        else:
            info, why = _buy(p, cfg, exp, equity, blocked, buy_pct, feed["macro"], session)
        counts[_act(now, session, p, info, why, mode, broker)] += 1
    return 0, (f"OK session={session} placed={counts['placed']} refused={counts['refused']} "
               f"queued={counts['queued']}{warn}"), session, equity


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true", required=True)
    ap.parse_args(argv)
    code, message = run(fetch.utcnow())
    print(message)
    return code


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
