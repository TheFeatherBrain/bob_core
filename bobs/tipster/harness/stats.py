#!/usr/bin/env python3
"""Euroleague games and team box lines from the official feed, cached so a finished game is read once.

Why the care: the league's data rights holder acts against unauthorised collection, so a personal
tool must stay polite (at most `stats_per_second` requests, every response cached, a finished game
never refetched). Why the games list is cached for 2 hours: discovery and news runs ask for it
every few minutes on game days, and its scores only change after a game ends.

Why `neutral` is not just the feed's `isNeutralVenue`: the feed sets it for some Final Four games
and not others (E2024: the semifinals yes, the final and third-place game no; E2015: none), and a
home advantage added to a neutral game is a systematic error in exactly the games that matter.
So every Final Four game (phase FF) is neutral, plus any game the feed marks.

Why a box score whose points disagree with the result is refused: ratings built on it would be
silently wrong, and "could not tell" is better than a wrong number.

Usage:
    python3 harness/stats.py --games E2026         # OK season=E2026 games=N played=M
    python3 harness/stats.py --sync E2026          # fetch missing finished box scores
    python3 harness/stats.py --selftest-live       # one real read of the games list
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import fetch  # noqa: E402
import store  # noqa: E402
import tipstercfg  # noqa: E402

BASE = "https://api-live.euroleague.net"
HEADERS = {"Accept": "application/json", "User-Agent": "bob-tipster/1 (personal research)"}
GAMES_TTL = dt.timedelta(hours=2)
_shared_limiter: fetch.Limiter | None = None


class StatsError(Exception):
    pass


def parse_utc(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def make_limiter(**kwargs) -> fetch.Limiter:
    return fetch.Limiter(tipstercfg.load()["stats_per_second"], **kwargs)


def _limiter() -> fetch.Limiter:
    global _shared_limiter
    if _shared_limiter is None:
        _shared_limiter = make_limiter()
    return _shared_limiter


def season_dir(season: str) -> pathlib.Path:
    return tipstercfg.state_dir() / "stats" / season


def _game(item: dict) -> dict:
    played = bool(item["played"])
    phase = item["phaseType"]["code"]
    return {"key": f"{item['season']['code']}-{item['gameCode']}", "season": item["season"]["code"],
            "code": int(item["gameCode"]), "round": int(item["round"]), "phase": phase,
            "start_utc": item["utcDate"], "home": item["local"]["club"]["code"],
            "away": item["road"]["club"]["code"],
            "home_pts": int(item["local"]["score"]) if played else None,
            "away_pts": int(item["road"]["score"]) if played else None,
            "played": played, "neutral": phase == "FF" or bool(item.get("isNeutralVenue"))}


def parse_games(raw: str) -> list[dict]:
    try:
        items = json.loads(raw)["data"]
        return sorted((_game(x) for x in items), key=lambda g: (g["start_utc"], g["code"]))
    except (ValueError, KeyError, TypeError) as e:
        raise StatsError(f"games list not in the expected shape: {type(e).__name__}: {e}") from None


def _line(game: dict, side: dict, other: dict, home: bool) -> dict:
    t = side["total"]
    return {"game": game["key"], "team": game["home"] if home else game["away"],
            "opp": game["away"] if home else game["home"], "home": home, "neutral": game["neutral"],
            "pts": int(t["points"]), "fga": t["fieldGoalsAttemptedTotal"], "fta": t["freeThrowsAttempted"],
            "oreb": t["offensiveRebounds"], "tov": t["turnovers"],
            "minutes": round(t["timePlayed"] * 5 / 60), "opp_pts": int(other["total"]["points"])}


def parse_box(raw: str, game: dict) -> list[dict]:
    try:
        box = json.loads(raw)
        lines = [_line(game, box["local"], box["road"], True), _line(game, box["road"], box["local"], False)]
    except (ValueError, KeyError, TypeError) as e:
        raise StatsError(f"{game['key']}: box score not in the expected shape: {type(e).__name__}: {e}") from None
    if [x["pts"] for x in lines] != [game["home_pts"], game["away_pts"]]:
        raise StatsError(f"{game['key']}: box points {[x['pts'] for x in lines]} disagree with the result "
                         f"{[game['home_pts'], game['away_pts']]}")
    return lines


def _write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def games(season: str, *, refresh: bool = False, getter=fetch.get, now: dt.datetime | None = None) -> list[dict]:
    now = now or utcnow()
    cache = season_dir(season) / "games.json"
    saved = store.read_json(cache, None)
    if saved and not refresh and now - parse_utc(saved["fetched_utc"]) < GAMES_TTL:
        return saved["games"]
    raw, _ = fetch.read(f"el-games-{season}", f"{BASE}/v2/competitions/E/seasons/{season}/games", HEADERS,
                        limiter=_limiter(), getter=getter)
    parsed = parse_games(raw)
    store.write_json(cache, {"fetched_utc": now.isoformat(), "games": parsed})
    return parsed


def box_lines(game: dict, *, getter=fetch.get) -> list[dict]:
    if not game["played"]:
        return []
    cache = season_dir(game["season"]) / f"box-{game['code']}.json"
    if cache.is_file():
        return parse_box(cache.read_text(encoding="utf-8"), game)
    url = f"{BASE}/v3/competitions/E/seasons/{game['season']}/games/{game['code']}/stats"
    raw, _ = fetch.read(f"el-box-{game['key']}", url, HEADERS, limiter=_limiter(), getter=getter)
    lines = parse_box(raw, game)
    _write_text(cache, raw)
    return lines


def season_lines_from(season_games: list[dict], *, getter=fetch.get) -> list[dict]:
    played = sorted((g for g in season_games if g["played"]), key=lambda g: (g["start_utc"], g["code"]))
    return [line for g in played for line in box_lines(g, getter=getter)]


def season_lines(season: str, *, getter=fetch.get) -> list[dict]:
    return season_lines_from(games(season, getter=getter), getter=getter)


def sync(season: str, *, getter=fetch.get) -> dict:
    season_games = games(season, refresh=True, getter=getter)
    played = [g for g in season_games if g["played"]]
    missing = [g for g in played if not (season_dir(season) / f"box-{g['code']}.json").is_file()]
    refused = []
    for g in missing:
        try:
            box_lines(g, getter=getter)
        except (StatsError, fetch.FetchError) as e:
            refused.append(f"{g['key']}: {e}")
    return {"played": len(played), "fetched": len(missing) - len(refused), "refused": refused}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Euroleague games and box scores, cached.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--games", metavar="SEASON")
    group.add_argument("--sync", metavar="SEASON")
    group.add_argument("--selftest-live", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.sync:
            result = sync(args.sync)
            for line in result["refused"]:
                print(f"REFUSED {line}")
            print(f"OK season={args.sync} played={result['played']} fetched={result['fetched']}"
                  f" refused={len(result['refused'])}")
            return 0
        season = args.games or "E2026"
        listed = games(season, refresh=bool(args.selftest_live))
        print(f"OK season={season} games={len(listed)} played={sum(g['played'] for g in listed)}")
        return 0
    except (StatsError, fetch.FetchError) as e:
        print(f"FAIL: {e}")
        return 1


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
