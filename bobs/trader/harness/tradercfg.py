#!/usr/bin/env python3
"""Read and check this Bob's [trader] section: mode, caps, costs, exit rules and the go-live bar.

Core's bobcfg validates the sections every Bob has and ignores [trader]. Without this check a typo
in a cap would silently change every order, and caps that cannot all hold (a base weight above the
position cap) would refuse every buy with a reason nobody expects.

Why the Scout's paths come from the Scout's manifest: the Trader reads the Scout's ledger as data,
and a hard-coded path would go quietly blind the day the Scout's data root moves.

Usage:
    python3 harness/tradercfg.py      # OK, or FAIL: <error> per problem in [trader]
"""
from __future__ import annotations

import json
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bobcfg  # noqa: E402

MODES = ("off", "simulate", "paper", "live")
FLAGS = ("vix", "insider", "liquidity")
FRACTIONS = ("base_weight", "max_position", "max_invested", "min_cash", "daily_loss_limit",
             "max_adv_share", "stop_loss", "throttle_floor")
NON_NEGATIVE = ("commission_per_order", "slippage_bps", "take_profit", "min_order_value", "vix_flag",
                "hy_spread_flag")
COUNTS = ("max_orders_per_day", "max_hold_days", "min_paper_days", "min_closed_trades")
CAPS = ("base_weight", "max_position", "max_invested", "min_cash", "daily_loss_limit", "max_orders_per_day",
        "min_order_value", "max_adv_share", "block_flags", "budgets", "stop_loss", "take_profit",
        "max_hold_days", "vix_flag", "hy_spread_flag", "throttle_floor")
BAR = ("min_paper_days", "min_closed_trades", "commission_per_order", "slippage_bps")


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(trader) -> list[str]:
    if not isinstance(trader, dict):
        return ["trader: required table"]
    errors: list[str] = []
    if trader.get("mode") not in MODES:
        errors.append(f"trader.mode: one of {list(MODES)}")
    if not (isinstance(trader.get("scout_manifest"), str) and trader["scout_manifest"]):
        errors.append("trader.scout_manifest: path to the Scout's bob.toml, relative to this Bob")
    if not (_num(trader.get("sim_starting_cash")) and trader["sim_starting_cash"] > 0):
        errors.append("trader.sim_starting_cash: a number above 0")
    errors += [f"trader.{k}: a number above 0 and at most 1" for k in FRACTIONS
               if not (_num(trader.get(k)) and 0 < trader[k] <= 1)]
    errors += [f"trader.{k}: a number of at least 0" for k in NON_NEGATIVE
               if not (_num(trader.get(k)) and trader[k] >= 0)]
    errors += [f"trader.{k}: an integer of at least 1" for k in COUNTS
               if not (isinstance(trader.get(k), int) and not isinstance(trader[k], bool) and trader[k] >= 1)]
    if not errors and trader["base_weight"] > trader["max_position"]:
        errors.append("trader.base_weight: at most max_position, or every buy is refused")
    if not errors and trader["max_invested"] + trader["min_cash"] > 1 + 1e-9:
        errors.append("trader.max_invested + min_cash: at most 1")
    flags = trader.get("block_flags")
    if not (isinstance(flags, list) and set(flags) <= set(FLAGS)):
        errors.append(f"trader.block_flags: a list drawn from {list(FLAGS)}")
    if not isinstance(trader.get("override_promotion"), str):
        errors.append('trader.override_promotion: a string ("" for none)')
    budgets = trader.get("budgets")
    if not (isinstance(budgets, dict) and budgets and all(_num(v) and 0 < v <= 1 for v in budgets.values())):
        errors.append("trader.budgets: one or more strategies, each a share above 0 and at most 1")
    elif sum(budgets.values()) > 1 + 1e-9:
        errors.append("trader.budgets: shares summing to at most 1")
    return errors


def load() -> dict:
    trader = bobcfg.config().get("trader")
    errors = validate(trader)
    if errors:
        raise bobcfg.ManifestError(errors)
    return trader


def state_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "state_dir")


def evidence_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "evidence_dir")


def bob_dir() -> pathlib.Path:
    return bobcfg.bob_root()


def scout() -> dict:
    manifest = (bob_dir() / load()["scout_manifest"]).resolve()
    try:
        cfg = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise bobcfg.ManifestError([f"trader.scout_manifest: cannot read {manifest} ({type(e).__name__})"]) from None
    paths = cfg.get("paths", {})
    root = manifest.parent / paths.get("data_root", "")
    return {"state_dir": (root / paths.get("state_dir", "state")).resolve(),
            "buy_percentile": cfg.get("scout", {}).get("buy_percentile")}


def fingerprint(cfg: dict, keys) -> str:
    return json.dumps({k: cfg.get(k) for k in keys}, sort_keys=True)


def main(argv: list[str]) -> int:
    try:
        load()
        scout()
    except bobcfg.ManifestError as e:
        for err in e.errors:
            print(f"FAIL: {err}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
