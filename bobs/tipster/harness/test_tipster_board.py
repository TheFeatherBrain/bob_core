#!/usr/bin/env python3
"""tipster_board: numbers straight from the ledger replay, banners by sample size, CLV "could not
tell" counted but not averaged, and the page and state served.

Run:  python3 harness/test_tipster_board.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import testkit  # noqa: E402
import tips  # noqa: E402
import tipster_board  # noqa: E402
import tipsterworld as w  # noqa: E402


def _tip(n: int, track="reviewed", market="moneyline", stake=10, odds=2.0) -> str:
    return tips.record_tip({"track": track, "at": f"2026-10-21T08:{n % 60:02d}:00+00:00", "season": "E2026",
                            "round": 7, "game": f"E2026-{n}", "start_utc": "2026-10-23T18:45:00Z",
                            "home": "PRS", "away": "PAM", "family": "total" if market == "total" else "side",
                            "market": market, "side": "home" if market != "total" else "over",
                            "line": None if market == "moneyline" else -4.5, "book": "bet365", "odds": odds,
                            "other_book_odds": {}, "p_win": 0.6, "p_push": 0.0, "edge": 0.2, "kelly": 0.2,
                            "stake": stake, "wallet_before": 1000, "inputs": {}, "provider_id": f"p{n}",
                            "review": {"decision": "keep", "reason": "ok", "evidence": []}})


def _settle(tid: str, result: str, pnl: float, clv, at="2026-10-24T09:15:00+00:00"):
    tips.append({"kind": "settled", "tip": tid, "at": at, "result": result, "pnl": pnl, "clv": clv,
                 "clv_note": "pinnacle" if clv is not None else "could not tell"})


def test_state_numbers_from_replay():
    w.world()
    _settle(_tip(1, stake=10, odds=2.0), "win", 10.0, 0.05)
    _settle(_tip(2, stake=20, odds=1.9), "loss", -20.0, -0.02)
    _settle(_tip(3, stake=30, market="handicap"), "win", 27.3, None)
    _settle(_tip(4, stake=40), "void", 0.0, None)
    _tip(5)
    s = tipster_board.state(now=w.NOW)
    allr = s["scores"]["reviewed"]["all"]
    # void excluded: 3 settled, profit 10 - 20 + 27.3 = 17.3 on 60 staked
    assert allr["n"] == 3 and allr["void"] == 1 and abs(allr["profit"] - 17.3) < 1e-9 and abs(allr["roi"] - 17.3 / 60) < 1e-9
    assert allr["clv_n"] == 2 and allr["clv_unknown"] == 1 and abs(allr["clv_mean"] - 0.015) < 1e-9
    assert allr["clv_positive_share"] == 0.5
    ml = s["scores"]["reviewed"]["moneyline"]
    assert ml["n"] == 2 and abs(ml["profit"] + 10) < 1e-9
    assert s["wallets"]["reviewed"]["now"] == 1017.3
    assert [p["wallet"] for p in s["wallets"]["reviewed"]["points"]][-1] == 1017.3
    assert s["scores"]["model"]["all"]["n"] == 0 and s["wallets"]["model"]["now"] == 1000


def test_banners_by_n():
    assert tipster_board.banners({"clv_n": 49, "n": 499}) == ["too early to judge CLV", "profit is mostly luck at this size"]
    assert tipster_board.banners({"clv_n": 50, "n": 499}) == ["profit is mostly luck at this size"]
    assert tipster_board.banners({"clv_n": 50, "n": 500}) == []


def test_clv_could_not_tell_excluded_from_mean_and_counted():
    w.world()
    _settle(_tip(1), "win", 10.0, None)
    allr = tipster_board.state(now=w.NOW)["scores"]["reviewed"]["all"]
    assert allr["clv_mean"] is None and allr["clv_unknown"] == 1 and allr["clv_positive_share"] is None


def test_upcoming_shows_decision_and_latest_update():
    w.world()
    _tip(7, track="model")
    tid = _tip(7)
    tips.append({"kind": "update", "tip": tid, "at": "2026-10-21T10:00:00+00:00", "status": "weaker",
                 "odds_now": 1.8, "edge_now": 0.05, "suggested_stake": 5, "reason": "x", "evidence": [], "note": None})
    _tip(8, track="model")
    tips.record_refused({"game": "E2026-8", "family": "side", "id": "c-2"}, "reviewed", "veto: star out",
                        "2026-10-21T08:30:00+00:00")
    rows = {r["game"]: r for r in tipster_board.state(now=w.NOW)["upcoming"]}
    assert rows["E2026-7"]["decision"] == "keep" and rows["E2026-7"]["update"] == "weaker"
    assert rows["E2026-8"]["decision"] == "veto: star out" and rows["E2026-8"]["stake"] is None


def test_state_has_budget_mode_halt_and_calibration():
    w.world()
    tips.halt("wallet below 500")
    s = tipster_board.state(now=w.NOW)
    assert s["mode"] == "on" and s["halt"] == "wallet below 500"
    assert s["budget"]["allowance"] == 250 and s["calibration"]["moneyline"]["pass"] is True


def test_board_serves_page_and_state():
    w.world()
    server = tipster_board.serve(tipster_board.Model("Tipster"), "127.0.0.1", 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "Tipster" in page and "/api/state" in page and "/events" in page
        assert json.loads(urllib.request.urlopen(base + "/api/state").read())["mode"] == "on"
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
