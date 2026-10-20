#!/usr/bin/env python3
"""tips: append-only ledger, one tip per game family and track, wallets from settlements only, HALT.

Run:  python3 harness/test_tips.py
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import testkit  # noqa: E402
import tips  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def _tip(track="model", game="E2026-71", family="side", stake=50, rnd=8, wallet=1000) -> dict:
    return {"track": track, "at": "2026-10-20T10:00:00+00:00", "season": "E2026", "round": rnd, "game": game,
            "family": family, "market": "handicap", "side": "home", "line": -5.5, "book": "stoiximan",
            "odds": 2.0, "other_book_odds": {}, "p_win": 0.55, "p_push": 0.0, "edge": 0.1, "kelly": 0.1,
            "stake": stake, "wallet_before": wallet, "inputs": {}}


def _settle(tip_id: str, pnl: float, result="loss"):
    tips.append({"kind": "settled", "tip": tip_id, "result": result, "pnl": pnl, "clv": None,
                 "clv_note": "could not tell"})


def test_tip_id_names_game_family_and_track():
    testkit.temp_bob()
    assert tips.record_tip(_tip()) == "t-E2026-71-side-model"


def test_duplicate_tip_refused():
    testkit.temp_bob()
    tips.record_tip(_tip())
    try:
        tips.record_tip(_tip(stake=10))
    except tips.Duplicate as e:
        assert "t-E2026-71-side-model" in str(e)
    else:
        raise AssertionError("second tip accepted")
    assert tips.record_tip(_tip(family="total")) == "t-E2026-71-total-model"
    assert tips.record_tip(_tip(track="reviewed")) == "t-E2026-71-side-reviewed"


def test_wallet_changes_only_on_settlement():
    testkit.temp_bob()
    tid = tips.record_tip(_tip(stake=50))
    state = tips.replay("model")
    assert state["wallet"] == 1000 and [t["id"] for t in state["open"]] == [tid]
    _settle(tid, 50, "win")
    state = tips.replay("model")
    assert state["wallet"] == 1050 and state["open"] == [] and state["settled"][0]["result"] == "win"


def test_tracks_have_separate_wallets():
    testkit.temp_bob()
    m = tips.record_tip(_tip(track="model"))
    r = tips.record_tip(_tip(track="reviewed", stake=25))
    _settle(m, -50)
    _settle(r, -25)
    assert tips.replay("model")["wallet"] == 950 and tips.replay("reviewed")["wallet"] == 975


def test_torn_line_skipped_and_counted():
    testkit.temp_bob()
    tid = tips.record_tip(_tip())
    with tips.ledger_path().open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "settled", "tip": \n')
    state = tips.replay("model")
    assert state["torn"] == 1 and [t["id"] for t in state["open"]] == [tid]


def test_auto_halt_below_500():
    testkit.temp_bob()
    tid = tips.record_tip(_tip(track="reviewed", stake=100))
    _settle(tid, -500)
    tips.replay("reviewed")
    assert tips.halted() is None
    tid2 = tips.record_tip(_tip(track="reviewed", game="E2026-72", stake=1, wallet=500))
    _settle(tid2, -1)
    tips.replay("reviewed")
    assert tips.halted() == "wallet below 500"


def test_model_track_loss_does_not_halt():
    testkit.temp_bob()
    tid = tips.record_tip(_tip(track="model", stake=100))
    _settle(tid, -600)
    assert tips.replay("model")["wallet"] == 400 and tips.halted() is None


def test_update_never_edits_tip_line():
    testkit.temp_bob()
    tips.record_tip(_tip())
    before = tips.ledger_path().read_bytes()
    tips.append({"kind": "update", "tip": "t-E2026-71-side-model", "status": "weaker", "odds_now": 1.9,
                 "edge_now": 0.04, "suggested_stake": 30, "reason": "x", "evidence": [], "note": None})
    after = tips.ledger_path().read_bytes()
    assert after.startswith(before) and len(after) > len(before)
    assert tips.replay("model")["open"][0]["updates"][0]["status"] == "weaker"


def test_append_refuses_unknown_kind_and_tip_kind():
    testkit.temp_bob()
    for row in ({"kind": "tip", "id": "x"}, {"kind": "edit", "tip": "x"}):
        try:
            tips.append(row)
        except tips.TipError:
            continue
        raise AssertionError(row)


def test_round_used_and_base():
    testkit.temp_bob()
    tips.record_tip(_tip(stake=50, wallet=1000))
    tips.record_tip(_tip(game="E2026-72", stake=30, wallet=1000))
    tips.record_tip(_tip(game="E2026-80", stake=40, rnd=9, wallet=990))
    tips.record_tip(_tip(track="reviewed", stake=20))
    assert tips.round_used("model", "E2026", 8) == (80, 1000)
    assert tips.round_used("model", "E2026", 9) == (40, 990)
    assert tips.round_used("model", "E2026", 10) == (0, None)


def test_halt_cli_creates_and_never_removes():
    bob = testkit.temp_bob()
    env = {**os.environ, "BOB_MANIFEST": str(bob / "bob.toml"), "CLAUDE_CONFIG_DIR": str(bob / "config")}
    out = subprocess.run([sys.executable, str(HERE / "halt.py"), "--reason", "person says stop"],
                         env=env, capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0 and out.stdout.startswith("OK"), out.stdout + out.stderr
    assert tips.halted() == "person says stop"
    again = subprocess.run([sys.executable, str(HERE / "halt.py"), "--reason", "other"],
                           env=env, capture_output=True, text=True, encoding="utf-8")
    assert again.returncode == 0 and tips.halted() == "person says stop"
    bad = subprocess.run([sys.executable, str(HERE / "halt.py"), "--remove"],
                         env=env, capture_output=True, text=True, encoding="utf-8")
    assert bad.returncode == 2 and tips.halted() == "person says stop"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
