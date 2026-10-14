#!/usr/bin/env python3
"""Hard limits on every buy: per position, total invested, cash floor, orders per day, strategy budget.

Why orders earlier in the same run count: ten buys that each pass alone can together breach the
invested cap; the exposure is updated after each accepted order. Why sells are exempt (they still
count toward orders per day): an exit must never wait on a cap. Each refusal names the figures,
so the ledger says exactly which limit and by how much. Fails closed: a cap that cannot be checked
refuses.

Usage (library; trade.py and approve.py call it):
    exp = caps.exposure(book, closes, equity)
    reason = caps.check_buy(exp, symbol, value, strategy, cfg)
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402

CENT = 0.005


@dataclasses.dataclass
class Exposure:
    equity: float
    cash: float
    values: dict[str, float]
    orders: int
    by_strategy: dict[str, float]

    @property
    def invested(self) -> float:
        return sum(self.values.values())


def exposure(book: ledger.Book, closes: dict[str, float], equity: float) -> Exposure:
    values: dict[str, float] = {}
    by_strategy: dict[str, float] = {}
    for lot in book.lots:
        value = lot.qty * closes[lot.symbol]
        values[lot.symbol] = values.get(lot.symbol, 0.0) + value
        by_strategy[lot.strategy or "none"] = by_strategy.get(lot.strategy or "none", 0.0) + value
    return Exposure(equity, book.cash, values, 0, by_strategy)


def check_buy(exp: Exposure, symbol: str, value: float, strategy: str, cfg: dict) -> str | None:
    if exp.orders + 1 > cfg["max_orders_per_day"]:
        return f"orders today {exp.orders + 1} above max_orders_per_day {cfg['max_orders_per_day']}"
    position = exp.values.get(symbol, 0.0) + value
    if position > cfg["max_position"] * exp.equity + CENT:
        return f"position {position:.2f} above max_position {cfg['max_position']:.0%} of equity {exp.equity:.2f}"
    if exp.invested + value > cfg["max_invested"] * exp.equity + CENT:
        return f"invested {exp.invested + value:.2f} above max_invested {cfg['max_invested']:.0%} of equity {exp.equity:.2f}"
    cash_after = exp.cash - value - cfg["commission_per_order"]
    if cash_after < cfg["min_cash"] * exp.equity - CENT:
        return f"cash after {cash_after:.2f} below min_cash {cfg['min_cash']:.0%} of equity {exp.equity:.2f}"
    budget = cfg["budgets"].get(strategy, 0.0) * exp.equity
    if exp.by_strategy.get(strategy, 0.0) + value > budget + CENT:
        return f"strategy {strategy} at {exp.by_strategy.get(strategy, 0.0) + value:.2f} above its budget {budget:.2f}"
    return None


def accept(exp: Exposure, symbol: str, value: float, strategy: str, cfg: dict) -> None:
    exp.values[symbol] = exp.values.get(symbol, 0.0) + value
    exp.by_strategy[strategy] = exp.by_strategy.get(strategy, 0.0) + value
    exp.cash -= value + cfg["commission_per_order"]
    exp.orders += 1


def count_sell(exp: Exposure) -> None:
    exp.orders += 1
