#!/usr/bin/env python3
"""[tipster] must be checked before any module trusts it: a typo in a cap changes every stake.

Run:  python3 harness/test_tipstercfg.py
"""
from __future__ import annotations

import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import bobcfg  # noqa: E402
import testkit  # noqa: E402
import tipstercfg  # noqa: E402


def _tipster() -> dict:
    return dict(tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))["tipster"])


def _refused(key: str, value) -> bool:
    t = _tipster()
    t[key] = value
    return any(f"tipster.{key}" in e for e in tipstercfg.validate(t))


def test_repo_manifest_validates():
    cfg = tomllib.loads(testkit.MANIFEST.read_text(encoding="utf-8"))
    assert bobcfg.validate(cfg) == [], bobcfg.validate(cfg)
    assert tipstercfg.validate(cfg["tipster"]) == [], tipstercfg.validate(cfg["tipster"])


def test_committed_mode_is_off():
    assert _tipster()["mode"] == "off"


def test_mode_must_be_off_or_on():
    assert _refused("mode", "live")
    assert not _refused("mode", "on")


def test_tip_share_not_above_round_share():
    assert _refused("max_tip_share", 0.30)
    assert not _refused("max_tip_share", 0.25)


def test_halt_below_under_wallet_start():
    assert _refused("halt_below", 1000)
    assert not _refused("halt_below", 999)


def test_markets_are_known():
    assert _refused("markets", ["moneyline", "props"])
    assert _refused("markets", [])
    assert not _refused("markets", ["total"])


def test_books_non_empty():
    assert _refused("tip_books", [])
    assert _refused("sharp_book", "")
    assert not _refused("tip_books", ["bet365"])


def test_seasons_look_like_E2023():
    assert _refused("calibration_seasons", ["2023"])
    assert _refused("fit_from", "2015")
    assert _refused("no_fans_seasons", ["E19"])
    assert not _refused("calibration_seasons", ["E2023"])
    assert not _refused("no_fans_seasons", [])


def test_min_edge_positive():
    assert _refused("min_edge", 0)
    assert _refused("min_edge", True)
    assert not _refused("min_edge", 0.05)


def test_counts_and_flags_checked():
    for key, bad in (("odds_requests_per_month", 0), ("stats_per_second", 0), ("closing_minutes", -1),
                     ("closing_from_history", "no"), ("odds_provider", ""), ("void_after_hours", 1.5)):
        assert _refused(key, bad), (key, bad)


def test_load_raises_manifest_error():
    testkit.temp_bob({'mode = "off"': 'mode = "live"'})
    try:
        tipstercfg.load()
    except bobcfg.ManifestError as e:
        assert any("tipster.mode" in err for err in e.errors)
    else:
        raise AssertionError("not refused")


def test_dirs_resolve_under_data_root():
    bob = testkit.temp_bob()
    assert tipstercfg.state_dir() == bob / "data" / "state"
    assert tipstercfg.evidence_dir() == bob / "data" / "evidence"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
