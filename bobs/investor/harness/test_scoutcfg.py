#!/usr/bin/env python3
"""[scout] must be checked before any module trusts it: a bad weight changes every score silently.

Run:  python3 harness/test_scoutcfg.py
"""
from __future__ import annotations

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import scoutcfg  # noqa: E402
import testkit  # noqa: E402


def _scout() -> dict:
    return dict(tomllib.loads(testkit.MANIFEST.read_text())["scout"])


def test_real_manifest_passes_core_and_scout_checks():
    cfg = tomllib.loads(testkit.MANIFEST.read_text())
    assert bobcfg.validate(cfg) == [], bobcfg.validate(cfg)
    assert scoutcfg.validate(cfg["scout"]) == [], scoutcfg.validate(cfg["scout"])


def test_weights_not_summing_to_one_refused():
    scout = _scout()
    scout["weights"] = {**scout["weights"], "growth": 0.5}
    assert any("summing to 1" in e for e in scoutcfg.validate(scout))


def test_unknown_weight_group_refused():
    scout = _scout()
    scout["weights"] = {**scout["weights"], "hype": 0.0}
    assert any("scout.weights" in e for e in scoutcfg.validate(scout))


def test_missing_sec_contact_refused():
    scout = _scout()
    scout.pop("sec_contact")
    assert any("sec_contact" in e for e in scoutcfg.validate(scout))


def test_coverage_above_one_refused():
    scout = _scout()
    scout["min_coverage"] = 70
    assert any("min_coverage" in e for e in scoutcfg.validate(scout))


def test_missing_section_refused():
    assert scoutcfg.validate(None) == ["scout: required table"]


def test_data_dirs_land_under_data_root():
    bob = testkit.temp_bob()
    assert scoutcfg.state_dir() == bob / "data" / "state"
    assert scoutcfg.evidence_dir() == bob / "data" / "evidence"
    assert scoutcfg.load()["top_n"] == 20


def test_agreement_above_the_group_count_refused():
    scout = _scout()
    scout["min_agreeing_groups"] = 6
    assert any("min_agreeing_groups" in e for e in scoutcfg.validate(scout))
    scout["min_agreeing_groups"] = 5
    assert scoutcfg.validate(scout) == []


def test_sector_size_must_be_a_positive_integer():
    scout = _scout()
    scout["min_sector_size"] = 0
    assert any("min_sector_size" in e for e in scoutcfg.validate(scout))
    del scout["min_sector_size"]
    assert any("min_sector_size" in e for e in scoutcfg.validate(scout))


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
