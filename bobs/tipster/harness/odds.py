#!/usr/bin/env python3
"""Pre-game odds from the chosen provider, as a board of games keyed by Euroleague club codes.

Why every request goes through here: the provider's free quota is small, so each request is
spent from the budget (budget.py) before it is made; a curl from a loop would not be counted.
Why the key is passed to fetch as a secret: the provider takes it in the URL, and evidence must
never hold it. Why an unmapped team name drops the game: odds attached to the wrong game make an
edge that does not exist; the name is listed in `unmapped` so the alias table can be fixed.

Board:
    {"fetched_utc", "evidence", "unmapped": [names], "games": [{"provider_id", "start_utc", "home",
     "away", "books": {book: {"moneyline": {"home", "away"} | None,
                              "handicap": {"line", "home", "away"} | None,
                              "total": {"line", "over", "under"} | None}}}]}
`handicap.line` is the home team's handicap (-5.5: home gives 5.5). Main lines only.

A provider adapter supplies its host, its round and game URLs, and `rows(raw)`: one row per game
with `home_name`/`away_name` instead of codes. The adapter for the chosen provider is written from
the responses recorded in plan Task 0; until then it refuses before spending anything.

Usage:
    python3 harness/odds.py --round     # OK games=N unmapped=[...] used=K/ALLOWANCE
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import fetch  # noqa: E402
import teams  # noqa: E402
import tipstercfg  # noqa: E402

KEY_ENV = "ODDS_API_KEY"
HEADERS = {"Accept": "application/json", "User-Agent": "bob-tipster/1 (personal research)"}


class OddsError(Exception):
    pass


class OddsPapi:
    """Not built: needs the recorded step 0 responses (market ids, line encoding, round scope)."""
    built = False
    host = "api.oddspapi.io"


PROVIDERS: dict[str, type] = {"oddspapi": OddsPapi}


def assemble(rows: list[dict], fetched_utc: str, evidence: str) -> dict:
    games, unmapped = [], []
    for row in rows:
        home, away = teams.code(row["home_name"]), teams.code(row["away_name"])
        unmapped += [name for name, c in ((row["home_name"], home), (row["away_name"], away)) if c is None]
        if home and away:
            games.append({"provider_id": row["provider_id"], "start_utc": row["start_utc"], "home": home,
                          "away": away, "books": row["books"]})
    return {"fetched_utc": fetched_utc, "evidence": evidence, "games": games, "unmapped": unmapped}


def _provider(cfg: dict):
    provider = PROVIDERS.get(cfg["odds_provider"])
    if provider is None or not getattr(provider, "built", True):
        raise OddsError(f"odds provider {cfg['odds_provider']!r} has no parser yet: it is written from the "
                        "step 0 recorded responses (plan Task 0)")
    return provider


def _key(environ) -> str:
    key = environ.get(KEY_ENV)
    if not key:
        raise OddsError(f"{KEY_ENV} not set")
    return key


def _read(kind: str, url_for, cfg: dict, environ, getter, now, pending_closing: int) -> dict:
    provider = _provider(cfg)
    key = _key(environ)
    now = now or dt.datetime.now(dt.timezone.utc)
    budget.spend(kind, pending_closing, now)
    try:
        raw, where = fetch.read(f"odds-{kind}", url_for(provider, key), HEADERS, getter=getter, secret=key)
    except fetch.FetchError as e:
        raise OddsError(str(e)) from None
    return assemble(provider.rows(raw), now.isoformat(), str(where))


def fetch_round(kind: str, cfg: dict, *, environ=os.environ, getter=fetch.get, now=None,
                pending_closing: int = 0) -> dict:
    return _read(kind, lambda p, key: p.round_url(cfg, key), cfg, environ, getter, now, pending_closing)


def fetch_game(kind: str, provider_id: str, cfg: dict, *, environ=os.environ, getter=fetch.get, now=None,
               pending_closing: int = 0) -> dict:
    return _read(kind, lambda p, key: p.game_url(cfg, key, provider_id), cfg, environ, getter, now,
                 pending_closing)


def main(argv: list[str], now: dt.datetime | None = None) -> int:
    import discover
    import stats
    parser = argparse.ArgumentParser(description="Odds for the current round, through the budget.")
    parser.add_argument("--round", action="store_true", required=True)
    parser.parse_args(argv)
    now = now or dt.datetime.now(dt.timezone.utc)
    pending = discover.pending_closing(now, stats.cached_games(discover.season_of(now)))
    try:
        board = fetch_round("discovery", tipstercfg.load(), now=now, pending_closing=pending)
    except (OddsError, budget.Refused) as e:
        print(f"FAIL: {e}")
        return 1
    s = budget.status(now)
    print(f"OK games={len(board['games'])} unmapped={board['unmapped']} used={s['used']}/{s['allowance']}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
