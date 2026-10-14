#!/usr/bin/env python3
"""The run history and the go-live report: is the paper record good enough to consider live?

The bar was agreed before paper started and lives in bob.toml: at least min_paper_days in paper and
min_closed_trades closed trades; return after a cost estimate above SPY's total return over the same
dates; maximum drawdown no worse than SPY's; zero submitted orders without a passing cap check and
zero mismatches a person has not cleared. Every criterion is PASS, FAIL or could not tell; the
report passes only when all are PASS. Changing the bar restarts the paper clock, so the bar cannot
be moved to fit the record. The report never changes the mode; a person does.

Why costs again on top of paper fills: paper fills are usually better than real ones.

Usage:
    python3 harness/promotion.py --report     # writes state/promotion/<date>.json, prints each criterion
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import fetch  # noqa: E402
import ledger  # noqa: E402
import store  # noqa: E402
import tradercfg  # noqa: E402

LOOKBACK_DAYS = 30


def history_path() -> pathlib.Path:
    return ledger.history_path()


def read_history() -> list[dict]:
    return store.read_jsonl(history_path())[0]


def record_run(now: dt.datetime, session: str | None, cfg: dict, result: str, equity: float | None) -> None:
    store.append_jsonl(history_path(), [{
        "at": now.isoformat(timespec="seconds"), "session": session, "mode": cfg["mode"],
        "caps": tradercfg.fingerprint(cfg, tradercfg.CAPS), "bar": tradercfg.fingerprint(cfg, tradercfg.BAR),
        "result": result, "equity": equity}])


def first_active_session(history: list[dict], mode: str) -> str | None:
    return next((h["session"] for h in history if h["mode"] == mode and h.get("session")), None)


def last_ok_equity(history: list[dict], mode: str) -> float | None:
    return next((h["equity"] for h in reversed(history)
                 if h["mode"] == mode and h["result"] == "OK" and h.get("equity") is not None), None)


def _window(history: list[dict], bar: str) -> list[dict]:
    start = next((i for i, h in enumerate(history) if h["mode"] == "paper" and h["bar"] == bar), None)
    if start is None:
        return []
    by_session = {h["session"]: h for h in history[start:]
                  if h["mode"] == "paper" and h["bar"] == bar and h["result"] == "OK" and h.get("equity") is not None}
    return [by_session[s] for s in sorted(by_session)]


def _drawdown(values: list[float]) -> float:
    peak, worst = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, (peak - v) / peak)
    return worst


def _crit(name: str, ok: bool | None, detail: str) -> dict:
    return {"name": name, "status": "could not tell" if ok is None else "PASS" if ok else "FAIL", "detail": detail}


def report(now: dt.datetime, cfg: dict | None = None, rows: list[dict] | None = None,
           history: list[dict] | None = None) -> dict:
    cfg = cfg or tradercfg.load()
    rows = ledger.read()[0] if rows is None else rows
    history = read_history() if history is None else history
    window = _window(history, tradercfg.fingerprint(cfg, tradercfg.BAR))
    criteria: list[dict] = []
    if len(window) < 2:
        criteria = [_crit(n, None, "no paper runs under the current bar") for n in ("time", "trades", "return", "drawdown")]
    else:
        first, last = window[0]["session"], window[-1]["session"]
        days = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days
        criteria.append(_crit("time", days >= cfg["min_paper_days"], f"{days} days in paper, {cfg['min_paper_days']} needed"))
        closed = [c for c in ledger.replay(rows).closed if c["full"] and not c["corrected"] and first <= c["closed"] <= last]
        criteria.append(_crit("trades", len(closed) >= cfg["min_closed_trades"],
                              f"{len(closed)} closed trades, {cfg['min_closed_trades']} needed"))
        extra: dict[str, float] = {}
        for r in rows:
            if r.get("kind") == "filled" and first <= r["session"] <= last:
                extra[r["session"]] = extra.get(r["session"], 0.0) + cfg["commission_per_order"] + \
                    r["qty"] * r["price"] * cfg["slippage_bps"] / 10_000
        curve = [h["equity"] - sum(v for s, v in extra.items() if s <= h["session"]) for h in window]
        spy = {d: c for d, c in bars.load(bars.BENCHMARK)["all"]}
        spy_curve = [spy.get(h["session"]) for h in window]
        if None in spy_curve:
            criteria += [_crit("return", None, "no SPY total-return close for every paper session"),
                         _crit("drawdown", None, "no SPY total-return close for every paper session")]
        else:
            mine, theirs = curve[-1] / curve[0] - 1, spy_curve[-1] / spy_curve[0] - 1
            criteria.append(_crit("return", mine > theirs, f"after costs {mine:+.2%} vs SPY {theirs:+.2%}"))
            dd_mine, dd_spy = _drawdown(curve), _drawdown(spy_curve)
            criteria.append(_crit("drawdown", dd_mine <= dd_spy, f"max drawdown {dd_mine:.2%} vs SPY {dd_spy:.2%}"))
    checked = {r["order_id"] for r in rows if r.get("kind") == "proposed" and r.get("caps") in ("ok", "exempt")}
    breaches = [r["order_id"] for r in rows if r.get("kind") == "submitted" and r["order_id"] not in checked]
    open_ = ledger.unresolved_mismatches(rows)
    criteria.append(_crit("guards", not breaches and not open_,
                          f"{len(breaches)} orders without a cap check, {len(open_)} uncleared mismatches"))
    return {"at": now.isoformat(timespec="seconds"), "passed": all(c["status"] == "PASS" for c in criteria),
            "criteria": criteria,
            "window": {"first": window[0]["session"] if window else None, "last": window[-1]["session"] if window else None}}


def folder() -> pathlib.Path:
    return tradercfg.state_dir() / "promotion"


def write(doc: dict) -> pathlib.Path:
    path = folder() / f"{doc['at'][:10]}.json"
    store.write_json(path, doc)
    return path


def latest_passing(now: dt.datetime, days: int = LOOKBACK_DAYS) -> bool:
    cutoff = (now.date() - dt.timedelta(days=days)).isoformat()
    return any(p.stem >= cutoff and store.read_json(p, {}).get("passed") is True for p in folder().glob("*.json"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", action="store_true", required=True)
    ap.parse_args(argv)
    doc = report(fetch.utcnow())
    print(f"OK report {write(doc)} passed={'yes' if doc['passed'] else 'no'}")
    for c in doc["criteria"]:
        print(f"  {c['name']}: {c['status']} ({c['detail']})")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
