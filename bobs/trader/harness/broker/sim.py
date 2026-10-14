#!/usr/bin/env python3
"""The simulator: a broker that fills market orders at the next session's raw open, with costs.

Why next open: the Trader decides after the close, so the earliest honest fill is the next open.
Slippage moves the price against the order; commission is charged per order. A buy the cash cannot
cover, or an order with no bar for EXPIRE_SESSIONS sessions (a halted stock), expires with its
reason. Splits are found where the raw and split-adjusted closes stop moving together, and reported
as activities, as a real broker would. Dividends are not credited: it understates returns, which
is the conservative side.

Every activity keeps its id forever, so trade.py can collect them again after a crash without
recording one twice.

Usage (library; through broker.base.adapter_for("simulate")).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traderpath  # noqa: E402,F401
import bars  # noqa: E402
import ledger  # noqa: E402
import store  # noqa: E402
import tradercfg  # noqa: E402
from broker import base  # noqa: E402

EXPIRE_SESSIONS = 5
SPLIT_TOLERANCE = 1e-3


class Simulator(base.Broker):
    MODE = "simulate"

    def __init__(self, mode: str, folder: pathlib.Path | None = None, cfg: dict | None = None):
        super().__init__(mode)
        self.cfg = cfg or tradercfg.load()
        self.path = (folder or tradercfg.state_dir() / "sim") / "broker.json"
        self.state = store.read_json(self.path, None) or {
            "cash": float(self.cfg["sim_starting_cash"]), "positions": {}, "open": [], "activities": [], "seen": {}}

    def _save(self) -> None:
        store.write_json(self.path, self.state)

    def _activity(self, kind: str, session: str, **fields) -> None:
        self.state["activities"].append({"activity_id": f"sim-{len(self.state['activities']) + 1}", "kind": kind,
                                         "session": session, **fields})

    def account(self) -> dict:
        self._settle()
        return {"cash": round(self.state["cash"], 2)}

    def positions(self) -> dict[str, float]:
        self._settle()
        return {s: q for s, q in self.state["positions"].items() if q > ledger.EPS}

    def activities(self) -> list[dict]:
        self._settle()
        return list(self.state["activities"])

    def submit(self, order: dict) -> None:
        known = {o["order_id"] for o in self.state["open"]} | {a.get("order_id") for a in self.state["activities"]}
        if order["order_id"] in known:
            raise base.AdapterError(f"duplicate client order id {order['order_id']}")
        self.state["open"].append(dict(order))
        self._save()

    def _settle(self) -> None:
        self._splits()
        spy = bars.load(bars.BENCHMARK)["raw"]
        waiting = []
        for order in self.state["open"]:
            later = [r for r in bars.load(order["symbol"])["raw"] if r[0] > order["session"]]
            if later:
                self._fill(order, later[0])
                continue
            passed = [r[0] for r in spy if r[0] > order["session"]]
            if len(passed) >= EXPIRE_SESSIONS:
                self._activity("expired", passed[-1], order_id=order["order_id"],
                               reason=f"no {order['symbol']} bar in {EXPIRE_SESSIONS} sessions")
                continue
            waiting.append(order)
        self.state["open"] = waiting
        self._save()

    def _fill(self, order: dict, bar: list) -> None:
        day, open_price = bar[0], bar[1]
        slip = self.cfg["slippage_bps"] / 10_000
        commission = float(self.cfg["commission_per_order"])
        held = self.state["positions"].get(order["symbol"], 0.0)
        side = order["side"]
        price = round(open_price * (1 + slip if side == "buy" else 1 - slip), 4)
        qty = order["qty"] if side == "buy" else min(order["qty"], held)
        if side == "sell" and qty <= ledger.EPS:
            self._activity("expired", day, order_id=order["order_id"], reason="nothing to sell")
            return
        amount = ledger.fill_amount(side, qty, price, commission)
        if self.state["cash"] + amount < 0:
            self._activity("expired", day, order_id=order["order_id"], reason="insufficient cash at the open")
            return
        self.state["cash"] = round(self.state["cash"] + amount, 2)
        self.state["positions"][order["symbol"]] = round(held + (qty if side == "buy" else -qty), 6)
        self.state["seen"].setdefault(order["symbol"], day)
        self._activity("filled", day, order_id=order["order_id"], symbol=order["symbol"], side=side, qty=qty,
                       price=price, commission=commission)

    def _splits(self) -> None:
        """Compare raw and split-adjusted closes day by day from the last day seen. The adjusted
        series is rewritten after each split, so factors are always recomputed from the current
        file, never from a stored number."""
        for symbol, since in list(self.state["seen"].items()):
            if self.state["positions"].get(symbol, 0.0) <= ledger.EPS:
                del self.state["seen"][symbol]
                continue
            stored = bars.load(symbol)
            adjusted = {r[0]: r[1] for r in stored["split"]}
            raw = [(r[0], r[4]) for r in stored["raw"] if r[0] >= since and r[0] in adjusted and adjusted[r[0]]]
            for (d0, c0), (d1, c1) in zip(raw, raw[1:]):
                ratio = (c0 / adjusted[d0]) / (c1 / adjusted[d1])
                if abs(ratio - 1) > SPLIT_TOLERANCE:
                    ratio = round(ratio, 6)
                    self.state["positions"][symbol] = round(self.state["positions"][symbol] * ratio, 6)
                    self._activity("split", d1, symbol=symbol, ratio=ratio)
            if raw:
                self.state["seen"][symbol] = raw[-1][0]
