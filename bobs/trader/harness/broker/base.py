#!/usr/bin/env python3
"""The one interface every broker adapter implements; trade.py and approve.py talk only to it.

Why an interface: the broker is decided later, and the guards (caps, reconcile, the approval queue)
must not change when it is. Why one mode per adapter: a paper key must never end up placing live
orders, so each adapter names the single mode it serves and refuses any other before it touches
the network.

Usage (library):
    broker = base.adapter_for(cfg["mode"])
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traderpath  # noqa: E402,F401


class AdapterError(Exception):
    pass


class Broker:
    MODE = ""

    def __init__(self, mode: str):
        if mode != self.MODE:
            raise AdapterError(f"{type(self).__name__} serves mode {self.MODE!r}, not {mode!r}")

    def account(self) -> dict:
        raise NotImplementedError

    def positions(self) -> dict[str, float]:
        raise NotImplementedError

    def submit(self, order: dict) -> None:
        raise NotImplementedError

    def activities(self) -> list[dict]:
        raise NotImplementedError


def adapter_for(mode: str) -> Broker:
    if mode == "simulate":
        from broker import sim
        return sim.Simulator(mode)
    raise AdapterError(f"no broker adapter for mode {mode!r} yet; stage 1 builds only the simulator")
