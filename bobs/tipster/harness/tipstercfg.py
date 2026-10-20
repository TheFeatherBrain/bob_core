#!/usr/bin/env python3
"""Read and check this Bob's [tipster] section: mode, books, markets, stake caps and model seasons.

Core's bobcfg validates the sections every Bob has and ignores [tipster]. Without this check a
typo in a cap would silently change every stake, and caps that cannot all hold (a per-tip cap
above the per-round cap, a HALT level at or above the starting wallet) would refuse every tip or
halt the Bob before its first one, with a reason nobody expects.

Usage:
    python3 harness/tipstercfg.py      # OK, or FAIL: <error> per problem in [tipster]
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import bobcfg  # noqa: E402

MODES = ("off", "on")
MARKETS = ("moneyline", "handicap", "total")
SEASON = re.compile(r"^E20\d\d$")
FRACTIONS = ("min_edge", "kelly_fraction", "max_tip_share", "max_round_share")
POSITIVE = ("odds_requests_per_month", "wallet_start", "halt_below", "early_sweep_hours",
            "stats_per_second", "void_after_hours")
COUNTS = ("odds_requests_per_month", "closing_minutes", "early_sweep_hours", "void_after_hours")


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _names(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) and v for v in value)


def _seasons(value, allow_empty: bool) -> bool:
    return (isinstance(value, list) and (allow_empty or bool(value))
            and all(isinstance(v, str) and SEASON.match(v) for v in value))


def validate(t) -> list[str]:
    if not isinstance(t, dict):
        return ["tipster: required table"]
    errors: list[str] = []
    if t.get("mode") not in MODES:
        errors.append(f"tipster.mode: one of {list(MODES)}")
    for key in ("odds_provider", "sharp_book"):
        if not (isinstance(t.get(key), str) and t[key]):
            errors.append(f"tipster.{key}: a non-empty name")
    if not isinstance(t.get("closing_from_history"), bool):
        errors.append("tipster.closing_from_history: true or false")
    if not _names(t.get("tip_books")):
        errors.append("tipster.tip_books: one or more book names")
    markets = t.get("markets")
    if not (_names(markets) and set(markets) <= set(MARKETS)):
        errors.append(f"tipster.markets: one or more of {list(MARKETS)}")
    errors += [f"tipster.{k}: a number above 0 and at most 1" for k in FRACTIONS
               if not (_num(t.get(k)) and 0 < t[k] <= 1)]
    errors += [f"tipster.{k}: a number above 0" for k in POSITIVE
               if not (_num(t.get(k)) and t[k] > 0)]
    errors += [f"tipster.{k}: a whole number of at least 0" for k in COUNTS
               if not (_int(t.get(k)) and t[k] >= 0)]
    if not (isinstance(t.get("fit_from"), str) and SEASON.match(t["fit_from"])):
        errors.append('tipster.fit_from: a season code like "E2015"')
    if not _seasons(t.get("calibration_seasons"), allow_empty=False):
        errors.append('tipster.calibration_seasons: one or more season codes like "E2023"')
    if not _seasons(t.get("no_fans_seasons"), allow_empty=True):
        errors.append('tipster.no_fans_seasons: season codes like "E2019"')
    if errors:
        return errors
    if t["max_tip_share"] > t["max_round_share"]:
        errors.append("tipster.max_tip_share: at most max_round_share")
    if t["halt_below"] >= t["wallet_start"]:
        errors.append("tipster.halt_below: below wallet_start, or the Bob halts before its first tip")
    return errors


def load() -> dict:
    t = bobcfg.config().get("tipster")
    errors = validate(t)
    if errors:
        raise bobcfg.ManifestError(errors)
    return t


def state_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "state_dir")


def evidence_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "evidence_dir")


def main(argv: list[str]) -> int:
    try:
        load()
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
