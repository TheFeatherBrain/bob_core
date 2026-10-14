#!/usr/bin/env python3
"""[trader] must be checked before any module trusts it: a typo in a cap changes every order.

Run:  python3 harness/test_tradercfg.py
"""
from __future__ import annotations

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import testkit  # noqa: E402
import tradercfg  # noqa: E402


def _trader() -> dict:
    return dict(tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))["trader"])


def test_real_manifest_passes_core_and_trader_checks():
    cfg = tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))
    assert bobcfg.validate(cfg) == [], bobcfg.validate(cfg)
    assert tradercfg.validate(cfg["trader"]) == [], tradercfg.validate(cfg["trader"])


def test_committed_mode_is_off():
    assert _trader()["mode"] == "off"


def test_unknown_mode_refused():
    t = _trader()
    t["mode"] = "yolo"
    assert any("trader.mode" in e for e in tradercfg.validate(t))


def test_caps_that_cannot_all_hold_refused():
    t = _trader()
    t["base_weight"] = 0.2
    assert any("base_weight" in e for e in tradercfg.validate(t))
    t = _trader()
    t["min_cash"] = 0.3
    assert any("max_invested + min_cash" in e for e in tradercfg.validate(t))
    t = _trader()
    t["min_cash"] = 0.2
    assert tradercfg.validate(t) == []


def test_fractions_counts_and_flags_checked():
    for key, bad in (("max_position", 0), ("max_position", 1.5), ("max_orders_per_day", 0),
                     ("stop_loss", None), ("block_flags", ["moon"]), ("budgets", {"scout_calls": 0.6, "x": 0.6})):
        t = _trader()
        t[key] = bad
        assert any(f"trader.{key}" in e for e in tradercfg.validate(t)), (key, bad)


def test_scout_paths_come_from_the_scout_manifest():
    bob = testkit.temp_bob()
    scout = tradercfg.scout()
    assert scout["state_dir"] == testkit.scout_state(bob).resolve() and scout["buy_percentile"] == 80


def test_missing_scout_manifest_is_a_manifest_error():
    bob = testkit.temp_bob()
    (bob.parent / "investor" / "bob.toml").unlink()
    try:
        tradercfg.scout()
    except bobcfg.ManifestError as e:
        assert "scout_manifest" in e.errors[0]
    else:
        raise AssertionError("not refused")


def test_fingerprint_changes_with_its_keys_only():
    cfg = _trader()
    base = tradercfg.fingerprint(cfg, tradercfg.BAR)
    assert tradercfg.fingerprint({**cfg, "max_position": 0.09}, tradercfg.BAR) == base
    assert tradercfg.fingerprint({**cfg, "min_paper_days": 200}, tradercfg.BAR) != base


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
