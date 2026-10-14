#!/usr/bin/env python3
"""Warnings on one proposed buy; only the flags named in block_flags refuse it.

Why most flags only warn: VIX and insider selling are context a person reads later next to the
result; refusing on them would turn a soft signal into a hard rule nobody tested. Liquidity blocks
by default: an order that is a large share of a day's trading moves the price against itself.
Two flags from the original comparison are left out because they could never fire here: earnings
inside the holding period (no source of future earnings dates) and weak factor agreement (every
v2 buy-worthy call already passed the Scout's agreement gate).

Usage (library; trade.py calls it):
    found = risk_flags.flags(card, macro, value, adv, cfg)
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import scout_feed  # noqa: E402


def flags(card: dict, macro: dict, value: float, adv: float | None, cfg: dict) -> list[str]:
    out = []
    vix = scout_feed.macro_value(macro, "VIXCLS")
    if vix is None or vix > cfg["vix_flag"]:
        out.append("vix")
    insider = (card.get("groups") or {}).get("insider")
    if insider is not None and insider < 50:
        out.append("insider")
    if adv is None or value > cfg["max_adv_share"] * adv:
        out.append("liquidity")
    return out


def blocking(found: list[str], cfg: dict) -> list[str]:
    return [f for f in found if f in cfg["block_flags"]]
