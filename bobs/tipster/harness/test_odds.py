#!/usr/bin/env python3
"""odds: provider rows become a board of mapped games; unmapped names are listed, never guessed; the
budget is spent before the request; the API key never reaches evidence.

The real provider's parser needs the step 0 recorded responses (plan Task 0); these tests drive the
provider-neutral part with a fake provider.

Run:  python3 harness/test_odds.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import odds  # noqa: E402
import store  # noqa: E402
import testkit  # noqa: E402

NOW = dt.datetime(2026, 10, 20, 9, 0, tzinfo=dt.timezone.utc)
KEY = "k3y-SECRET-123456"
LINES = {"moneyline": {"home": 1.5, "away": 2.7}, "handicap": {"line": -5.5, "home": 1.91, "away": 1.91},
         "total": {"line": 160.5, "over": 1.9, "under": 1.9}}


def _row(pid: str, home: str, away: str) -> dict:
    return {"provider_id": pid, "start_utc": "2026-10-22T17:00:00Z", "home_name": home, "away_name": away,
            "books": {"bet365": LINES, "stoiximan": LINES, "pinnacle": LINES}}


class Fake:
    host = "api.oddspapi.io"

    @staticmethod
    def round_url(cfg, key):
        return f"https://api.oddspapi.io/v4/round?apiKey={key}"

    @staticmethod
    def game_url(cfg, key, provider_id):
        return f"https://api.oddspapi.io/v4/game/{provider_id}?apiKey={key}"

    @staticmethod
    def rows(raw):
        return [_row("p1", "Olympiacos Piraeus", "Real Madrid"), _row("p2", "Olympiacos B", "Panathinaikos")]


def _setup():
    testkit.temp_bob()
    odds.PROVIDERS["fake"] = Fake
    return {**odds.tipstercfg.load(), "odds_provider": "fake"}


def test_unmapped_team_name_is_could_not_tell():
    board = odds.assemble(Fake.rows(""), "2026-10-20T09:00:00+00:00", "ev/1")
    assert [(g["home"], g["away"]) for g in board["games"]] == [("OLY", "MAD")]
    assert board["unmapped"] == ["Olympiacos B"]


def test_board_keeps_main_lines_per_book():
    board = odds.assemble(Fake.rows(""), "2026-10-20T09:00:00+00:00", "ev/1")
    game = board["games"][0]
    assert game["provider_id"] == "p1" and game["books"]["stoiximan"]["handicap"]["line"] == -5.5
    assert board["fetched_utc"] == "2026-10-20T09:00:00+00:00" and board["evidence"] == "ev/1"


def test_fetch_round_spends_budget_and_hides_key():
    cfg = _setup()
    board = odds.fetch_round("discovery", cfg, environ={"ODDS_API_KEY": KEY}, getter=lambda u, h: "{}", now=NOW)
    assert budget.status(NOW)["used"] == 1 and len(board["games"]) == 1
    evidence = pathlib.Path(board["evidence"])
    saved = "".join(p.read_text(encoding="utf-8") for p in evidence.iterdir())
    assert KEY not in saved and "{KEY}" in saved


def test_spend_written_before_request():
    cfg = _setup()

    def getter(url, headers):
        raise odds.fetch.FetchError(url, 500, "HTTP 500")
    try:
        odds.fetch_game("recheck", "p1", cfg, environ={"ODDS_API_KEY": KEY}, getter=getter, now=NOW)
    except odds.OddsError as e:
        assert KEY not in str(e)
    else:
        raise AssertionError("not raised")
    rows, _ = store.read_jsonl(budget.ledger_path())
    assert [r["kind"] for r in rows] == ["recheck"]


def test_missing_key_spends_nothing():
    cfg = _setup()
    try:
        odds.fetch_round("discovery", cfg, environ={}, getter=lambda u, h: "{}", now=NOW)
    except odds.OddsError as e:
        assert "ODDS_API_KEY" in str(e)
    else:
        raise AssertionError("not raised")
    assert budget.status(NOW)["used"] == 0


def test_provider_without_recorded_responses_spends_nothing():
    testkit.temp_bob()
    cfg = odds.tipstercfg.load()
    try:
        odds.fetch_round("discovery", cfg, environ={"ODDS_API_KEY": KEY}, getter=lambda u, h: "{}", now=NOW)
    except odds.OddsError as e:
        assert "step 0" in str(e)
    else:
        raise AssertionError("not raised")
    assert budget.status(NOW)["used"] == 0


def test_budget_refusal_is_odds_error_without_request():
    cfg = _setup()
    testkit.temp_bob({"odds_requests_per_month = 250": "odds_requests_per_month = 1"})
    odds.fetch_round("discovery", cfg, environ={"ODDS_API_KEY": KEY}, getter=lambda u, h: "{}", now=NOW)
    try:
        odds.fetch_round("discovery", cfg, environ={"ODDS_API_KEY": KEY},
                         getter=lambda u, h: (_ for _ in ()).throw(AssertionError("requested")), now=NOW)
    except budget.Refused:
        pass
    else:
        raise AssertionError("not refused")


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
