#!/usr/bin/env python3
"""Read and check this Bob's [scout] section: thresholds, weights and the SEC contact.

Core's bobcfg validates the sections every Bob has and ignores [scout]. Without this check a typo in
a weight would silently change every score, and a missing SEC contact gets the machine's IP blocked
by SEC on the first request.

Usage:
    python3 harness/scoutcfg.py      # OK, or FAIL: <error> per problem in [scout]
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import bobcfg  # noqa: E402

GROUPS = ("growth", "surprise", "momentum", "quality", "insider")
POSITIVE = ("min_market_cap", "min_price", "min_dollar_volume")
COUNTS = ("top_n", "max_new_theses_per_day", "story_max_per_week", "scorecard_version",
          "frames_refresh_days", "min_sector_size", "min_agreeing_groups")


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(scout) -> list[str]:
    if not isinstance(scout, dict):
        return ["scout: required table"]
    errors: list[str] = []
    contact = scout.get("sec_contact")
    if not (isinstance(contact, str) and "@" in contact and " " in contact.strip()):
        errors.append('scout.sec_contact: "<name> <email>"; SEC refuses requests without a contact')
    errors += [f"scout.{k}: a number above 0" for k in POSITIVE
               if not (_num(scout.get(k)) and scout[k] > 0)]
    errors += [f"scout.{k}: an integer of at least 1" for k in COUNTS
               if not (isinstance(scout.get(k), int) and not isinstance(scout[k], bool) and scout[k] >= 1)]
    agree = scout.get("min_agreeing_groups")
    if isinstance(agree, int) and not isinstance(agree, bool) and agree > len(GROUPS):
        errors.append(f"scout.min_agreeing_groups: at most {len(GROUPS)}, the number of groups")
    for key, low, high in (("buy_percentile", 0, 100), ("min_coverage", 0, 1),
                           ("max_missing_price_share", 0, 1), ("altman_veto", 0, 10)):
        if not (_num(scout.get(key)) and low < scout[key] <= high):
            errors.append(f"scout.{key}: a number above {low} and at most {high}")
    weights = scout.get("weights")
    if not isinstance(weights, dict) or set(weights) != set(GROUPS):
        errors.append(f"scout.weights: exactly {list(GROUPS)}")
    elif not all(_num(w) and w >= 0 for w in weights.values()) or abs(sum(weights.values()) - 1) > 1e-9:
        errors.append("scout.weights: non-negative numbers summing to 1")
    return errors


def load() -> dict:
    scout = bobcfg.config().get("scout")
    errors = validate(scout)
    if errors:
        raise bobcfg.ManifestError(errors)
    return scout


def state_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "state_dir")


def evidence_dir() -> pathlib.Path:
    return bobcfg.path(bobcfg.config(), "evidence_dir")


def bob_dir() -> pathlib.Path:
    return bobcfg.bob_root()


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
