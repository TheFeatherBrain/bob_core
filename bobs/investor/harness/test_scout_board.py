#!/usr/bin/env python3
"""scout_board: the model reads only computed files, the page and routes answer, paths cannot escape.

Run:  python3 harness/test_scout_board.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import scout_board  # noqa: E402
import testkit  # noqa: E402
from test_thesis_lint import thesis  # noqa: E402


def _world() -> pathlib.Path:
    bob = testkit.temp_bob()
    state = bob / "data" / "state"
    (state / "screen").mkdir(parents=True)
    (state / "screen" / "latest.json").write_text(json.dumps({"session": "2026-10-12", "shortlist": ["NVDA"], "cards": {
        "NVDA": {"name": "NVIDIA CORP", "score": 91.0, "coverage": 1.0, "gate": "eligible", "reason": "r"}}}))
    (state / "ledger_results.json").write_text(json.dumps({"summary": {"12": {"n": 0}}, "by_lane": {}, "calls": [],
                                                           "basket": {"dates": [], "scout": [], "spy": [], "qqq": []}}))
    (bob / "theses" / "NVDA.md").write_text(thesis("watch"))
    return bob


def test_rebuild_reads_screen_results_and_theses():
    _world()
    snap = scout_board.Model("Investor Scout").rebuild()
    assert snap["session"] == "2026-10-12" and snap["shortlist"][0]["ticker"] == "NVDA"
    assert snap["shortlist"][0]["verdict"] == "watch" and snap["theses"] == [
        {"ticker": "NVDA", "verdict": "watch", "lane": "numbers", "date": "2026-10-13"}]
    assert snap["scored_12m"] == 0 and snap["min_scored"] == 20 and snap["stale"] is True


def test_rebuild_reads_a_utf8_thesis_on_any_platform():
    bob = _world()
    (bob / "theses" / "NVDA.md").write_text(thesis("watch", bull="- Łódź [ev:x]"), encoding="utf-8")
    assert scout_board.Model("t").rebuild()["theses"][0]["verdict"] == "watch"


def test_fingerprint_changes_when_a_thesis_changes():
    bob = _world()
    model = scout_board.Model("t")
    before = model.ev.fingerprint()
    time.sleep(0.05)
    (bob / "theses" / "NVDA.md").write_text(thesis("pass"))
    assert model.ev.fingerprint() != before


def _get(port: int, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def test_routes():
    bob = _world()
    (bob / "outside.md").write_text("not a thesis")
    model = scout_board.Model("Investor Scout")
    server = scout_board.serve(model, "127.0.0.1", 0, quiet=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        status, page = _get(port, "/")
        assert status == 200 and "Scout vs S&amp;P 500 vs Nasdaq-100" in page
        status, body = _get(port, "/api/state")
        assert status == 200 and json.loads(body)["shortlist"][0]["ticker"] == "NVDA"
        status, body = _get(port, "/thesis/NVDA")
        assert status == 200 and body.startswith("# NVDA")
        assert _get(port, "/thesis/..%2Fbob.toml")[0] == 404
        assert _get(port, "/thesis/..%2Foutside")[0] == 404
        assert _get(port, "/thesis/MSFT")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
