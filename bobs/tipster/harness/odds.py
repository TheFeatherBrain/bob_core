#!/usr/bin/env python3
"""Pre-game odds from the chosen provider, as a board of games keyed by Euroleague club codes.

Why every request goes through here: the provider's free quota is small, so each request is
spent from the budget (budget.py) before it is made; a curl from a loop would not be counted.
Why the key is passed to fetch as a secret: the provider takes it in the URL, and evidence must
never hold it. Why an unmapped team name drops the game: odds attached to the wrong game make an
edge that does not exist; the name is listed in `unmapped` so the alias table can be fixed.
Why a sweep that runs out of budget keeps what it read: the games already read cost their
requests; the board says it stopped and why.

Board:
    {"fetched_utc", "evidence", "unmapped": [names], "stopped": reason | None,
     "games": [{"provider_id", "start_utc", "home", "away", "evidence",
                "books": {book: {"moneyline": {"home", "away"} | None,
                                 "handicap": {"line", "home", "away"} | None,
                                 "total": {"line", "over", "under"} | None}}}]}
`handicap.line` is the home team's handicap (-5.5: home gives 5.5). Main lines only.

A provider adapter (oddspapi.py) has `round(read, cfg, key, now, until)` and
`game(read, cfg, key, provider_id, now)`, each returning {"rows", "stopped"} with rows carrying
`home_name`/`away_name`, and `closing(read, cfg, key, provider_id, start_utc)` returning books.
`read(url)` spends one request and saves the read as evidence.

Usage:
    python3 harness/odds.py --round     # OK games=N unmapped=[...] used=K/ALLOWANCE
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import fetch  # noqa: E402
import oddspapi  # noqa: E402
import teams  # noqa: E402
import tipstercfg  # noqa: E402

KEY_ENV = "ODDS_API_KEY"
HEADERS = {"Accept": "application/json", "User-Agent": "bob-tipster/1 (personal research)"}
#: The history endpoint asks for 5 seconds between calls.
HISTORY_GAP = 5.0

PROVIDERS: dict[str, type] = {"oddspapi": oddspapi.OddsPapi}


class OddsError(Exception):
    pass


def assemble(rows: list[dict], fetched_utc: str, evidence: str | None, stopped: str | None = None) -> dict:
    games, unmapped = [], []
    for row in rows:
        home, away = teams.code(row["home_name"]), teams.code(row["away_name"])
        unmapped += [name for name, c in ((row["home_name"], home), (row["away_name"], away)) if c is None]
        if home and away:
            games.append({"provider_id": row["provider_id"], "start_utc": row["start_utc"], "home": home,
                          "away": away, "books": row["books"], "evidence": row.get("evidence", evidence)})
    return {"fetched_utc": fetched_utc, "evidence": evidence, "games": games, "unmapped": unmapped,
            "stopped": stopped}


def _provider(cfg: dict):
    provider = PROVIDERS.get(cfg["odds_provider"])
    if provider is None:
        raise OddsError(f"no adapter for odds provider {cfg['odds_provider']!r}")
    return provider


def _key(environ) -> str:
    key = environ.get(KEY_ENV)
    if not key:
        raise OddsError(f"{KEY_ENV} not set")
    return key


def _reader(kind: str | None, getter, now: dt.datetime, pending_closing: int, key: str, paths: list):
    """read(url) -> (raw, evidence path). With a kind it spends one request first; without, it is a
    free read (the history)."""
    def read(url: str) -> tuple[str, str]:
        if kind is not None:
            budget.spend(kind, pending_closing, now)
        try:
            raw, where = fetch.read(f"odds-{kind or 'history'}", url, HEADERS, getter=getter, secret=key)
        except fetch.FetchError as e:
            raise OddsError(str(e)) from None
        paths.append(str(where))
        return raw, str(where)
    return read


def _board(kind: str, call, cfg: dict, environ, getter, now, pending_closing: int) -> dict:
    provider, key = _provider(cfg), _key(environ)
    now = now or dt.datetime.now(dt.timezone.utc)
    paths: list[str] = []
    try:
        result = call(provider, _reader(kind, getter, now, pending_closing, key, paths), key, now)
    except oddspapi.AdapterError as e:
        raise OddsError(str(e)) from None
    return assemble(result["rows"], now.isoformat(), paths[0] if paths else None, result["stopped"])


def fetch_round(kind: str, cfg: dict, *, environ=os.environ, getter=fetch.get, now=None,
                pending_closing: int = 0, until: dt.datetime | None = None) -> dict:
    """Every game starting after now and up to `until` (default: now + early_sweep_hours)."""
    def call(provider, read, key, now):
        end = until or now + dt.timedelta(hours=cfg["early_sweep_hours"])
        return provider.round(read, cfg, key, now, end)
    return _board(kind, call, cfg, environ, getter, now, pending_closing)


def fetch_game(kind: str, provider_id: str, cfg: dict, *, environ=os.environ, getter=fetch.get, now=None,
               pending_closing: int = 0) -> dict:
    return _board(kind, lambda provider, read, key, now: provider.game(read, cfg, key, provider_id, now),
                  cfg, environ, getter, now, pending_closing)


def closing_books(cfg: dict, provider_id: str, start_utc: str, *, environ=os.environ, getter=fetch.get,
                  sleep=time.sleep) -> dict:
    """Closing prices from the provider's history: free, so no budget is spent."""
    provider, key = _provider(cfg), _key(environ)
    read = _reader(None, getter, dt.datetime.now(dt.timezone.utc), 0, key, [])
    sleep(HISTORY_GAP)
    return provider.closing(read, cfg, key, provider_id, start_utc)


def main(argv: list[str], now: dt.datetime | None = None) -> int:
    import discover
    import stats
    parser = argparse.ArgumentParser(description="Odds for the current round, through the budget.")
    parser.add_argument("--round", action="store_true", required=True)
    parser.parse_args(argv)
    if not tipstercfg.is_on():
        print("OK mode off")
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    pending = discover.pending_closing(now, stats.cached_games(discover.season_of(now)))
    try:
        board = fetch_round("discovery", tipstercfg.load(), now=now, pending_closing=pending)
    except (OddsError, budget.Refused) as e:
        print(f"FAIL: {e}")
        return 1
    s = budget.status(now)
    stopped = f" stopped={board['stopped']}" if board.get("stopped") else ""
    print(f"OK games={len(board['games'])} unmapped={board['unmapped']} used={s['used']}/{s['allowance']}{stopped}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
