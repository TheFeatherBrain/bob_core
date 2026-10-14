#!/usr/bin/env python3
"""trader_board: mode, HALT, positions with their stops, the queue, refusals and the go-live report.

Run:  python3 harness/test_trader_board.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import threading
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import ledger  # noqa: E402
import testkit  # noqa: E402
import trade  # noqa: E402
import trader_board  # noqa: E402

NOW = dt.datetime(2026, 1, 9, 6, 30, tzinfo=dt.timezone.utc)


def test_empty_world_shows_mode_off_and_nothing_else():
    testkit.temp_bob()
    s = trader_board.Model("Trader").rebuild()
    assert s["mode"] == "off" and s["halt"] is None and s["positions"] == [] and s["queue"] == []


def test_positions_queue_refusals_and_halt():
    bob = testkit.temp_bob()
    testkit.put(bob / "data" / "state" / "bars" / "AAA.json", {"raw": [["2026-01-08", 90, 90, 90, 90, 1]],
                                                              "split": [["2026-01-08", 90.0]], "all": [], "evidence": []})
    ledger.append([
        ledger.line("cash", NOW, "2026-01-05", amount=1000.0),
        ledger.line("proposed", NOW, "2026-01-05", order_id="b", strategy="scout_calls", symbol="AAA", side="buy",
                    exit_rules={"stop_loss": 0.2, "take_profit": 0, "max_hold_days": 365}, decision="accepted"),
        ledger.line("filled", NOW, "2026-01-06", order_id="b", symbol="AAA", side="buy", qty=2, price=100.0, commission=1.0),
        ledger.line("queued", NOW, "2026-01-08", order_id="q"),
        ledger.line("refused", NOW, "2026-01-08", order_id="r", reason="max_position")])
    trade.halt_path().write_text("2026-01-08 vix spike\n", encoding="utf-8")
    s = trader_board.Model("Trader").rebuild()
    assert s["halt"] == "2026-01-08 vix spike"
    pos = s["positions"][0]
    assert (pos["symbol"], pos["qty"], pos["stop"], pos["close"]) == ("AAA", 2.0, 80.0, 90.0)
    assert [q["order_id"] for q in s["queue"]] == ["q"] and s["refusals"][0]["reason"] == "max_position"


def test_routes_serve_the_page_and_state():
    testkit.temp_bob()
    server = trader_board.serve(trader_board.Model("Trader"), "127.0.0.1", 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert b"Trader" in urllib.request.urlopen(base + "/").read()
        assert json.loads(urllib.request.urlopen(base + "/api/state").read())["mode"] == "off"
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
