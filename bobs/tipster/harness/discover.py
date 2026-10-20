#!/usr/bin/env python3
"""Discovery sweep: when due, read the round's odds once, price every upcoming game, record the
model track, and list candidates for Claude's review.

Why sweeps only when due (when a round's lines open, and on game-day mornings): odds requests are
scarce, news is free (spec 6). Why "game day" is the Athens date: the person lives by it, and a
game at 01:30 Athens time belongs to that Athens day, not the UTC one. Why the model track is
recorded here, before review: it is the control that shows whether Claude's vetoes help, so it
must not depend on them. Why a game starting within `closing_minutes` is skipped: the last check
before tip-off would come too late to act on.

Why mode off reads nothing and HALT records nothing: off means off (spec 8); HALT stops new tips
while watching and settling continue elsewhere.

Usage:
    python3 harness/discover.py --run     # CANDIDATE lines, then OK ...
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import calibrate  # noqa: E402
import fetch  # noqa: E402
import model  # noqa: E402
import news  # noqa: E402
import odds  # noqa: E402
import pricing  # noqa: E402
import ratings  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402

ATHENS = zoneinfo.ZoneInfo("Europe/Athens")
MATCH_WINDOW = dt.timedelta(hours=36)


def athens_date(when: dt.datetime) -> str:
    return when.astimezone(ATHENS).date().isoformat()


def season_of(now: dt.datetime) -> str:
    return f"E{now.year if now.month >= 7 else now.year - 1}"


def _sweeps_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "sweeps.json"


def candidates_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "candidates.json"


def sweeps() -> dict:
    return store.read_json(_sweeps_path(), {"early": [], "gameday": []})


def _upcoming(now: dt.datetime, games: list[dict], cfg: dict) -> list[dict]:
    soon = now + dt.timedelta(minutes=cfg["closing_minutes"])
    return [g for g in games if not g["played"] and stats.parse_utc(g["start_utc"]) > soon]


def _next_round(now: dt.datetime, games: list[dict], cfg: dict) -> tuple[str, dt.datetime] | None:
    upcoming = _upcoming(now, games, cfg)
    if not upcoming:
        return None
    first = min(upcoming, key=lambda g: g["start_utc"])
    rnd = f"{first['season']}-R{first['round']}"
    starts = [stats.parse_utc(g["start_utc"]) for g in games if g["season"] == first["season"]
              and g["round"] == first["round"]]
    return rnd, min(starts)


def due(now: dt.datetime, games: list[dict], cfg: dict, done: dict | None = None) -> str | None:
    done = done if done is not None else sweeps()
    today = athens_date(now)
    if today not in done["gameday"] and any(athens_date(stats.parse_utc(g["start_utc"])) == today
                                            for g in _upcoming(now, games, cfg)):
        return "gameday"
    nxt = _next_round(now, games, cfg)
    if nxt and nxt[0] not in done["early"] and nxt[1] - now <= dt.timedelta(hours=cfg["early_sweep_hours"]):
        return "early"
    return None


def mark_sweep(kind: str, now: dt.datetime, games: list[dict], cfg: dict) -> None:
    done = sweeps()
    if kind == "gameday":
        done["gameday"] = sorted(set(done["gameday"]) | {athens_date(now)})
    nxt = _next_round(now, games, cfg)
    if nxt:
        done["early"] = sorted(set(done["early"]) | {nxt[0]})
    store.write_json(_sweeps_path(), dict(done, last_at=now.isoformat()))


def pick_text(c: dict, game: dict) -> str:
    team = game["home"] if c["side"] == "home" else game["away"]
    if c["market"] == "moneyline":
        return f"{team} ML"
    if c["market"] == "handicap":
        return f"{team} {c['line']:+g}"
    return f"{c['side']} {c['line']:g}"


def _match(game: dict, board: dict) -> dict | None:
    start = stats.parse_utc(game["start_utc"])
    for b in board["games"]:
        if (b["home"], b["away"]) == (game["home"], game["away"]) and abs(stats.parse_utc(b["start_utc"]) - start) <= MATCH_WINDOW:
            return b
    return None


def _pending_closing(now: dt.datetime, games: list[dict]) -> int:
    starts = {g["key"]: stats.parse_utc(g["start_utc"]) for g in games}
    open_games = {t["game"] for track in tips.TRACKS for t in tips.replay(track)["open"]}
    return sum(1 for key in open_games if key in starts and starts[key] > now)


def _last_season(season: str) -> ratings.Season | None:
    prev = f"E{int(season[1:]) - 1}"
    lines = stats.season_lines(prev)
    return ratings.Season(lines) if lines else None


def _wallet_stake(track: str, cand: dict, season: str, rnd: int, cfg: dict) -> tuple[int, float]:
    wallet = tips.replay(track)["wallet"]
    used, base = tips.round_used(track, season, rnd)
    return pricing.stake(cand["kelly"], wallet, used, base if base is not None else wallet, cfg), wallet


def _price_game(game, lines, last, params, flags, board_game, cfg, passed):
    before = [x for x in lines if x["start_utc"] < game["start_utc"]]
    home = ratings.ratings_at(game["home"], before, last, params["k"])
    away = ratings.ratings_at(game["away"], before, last, params["k"])
    league = ratings.Season(before).league() or (last.league() if last else None)
    if home is None or away is None or league is None:
        return None, []
    exp = model.expect(home, away, league, params, game["neutral"], flags.get(game["key"], (False, False)))
    inputs = {"home": home, "away": away, "league": league, "home_adv": params["home_adv"], "k": params["k"],
              "double": flags.get(game["key"], (False, False)), "margin": exp["margin"], "total": exp["total"],
              "pace": exp["pace"], "odds_evidence": None, "stats_cache": str(stats.season_dir(game["season"]))}
    return inputs, pricing.candidates(game, exp, params, board_game, cfg, passed)


def run(now: dt.datetime | None = None, *, getter=fetch.get, environ=os.environ, sync=stats.sync) -> tuple[int, list[str]]:
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = tipstercfg.load()
    if cfg["mode"] != "on":
        return 0, ["OK mode off"]
    reason = tips.halted()
    if reason:
        return 0, [f"OK halted: {reason}"]
    season = season_of(now)
    sync(season)
    games = stats.games(season, now=now)
    kind = due(now, games, cfg)
    if kind is None:
        return 0, ["OK no sweep due"]
    passed = calibrate.passed_markets()
    params = store.read_json(calibrate.params_path(), None)
    if not passed or params is None:
        return 0, ["OK no market passed calibration; no odds read"]
    try:
        board = odds.fetch_round("discovery", cfg, environ=environ, getter=getter, now=now,
                                 pending_closing=_pending_closing(now, games))
    except (odds.OddsError, budget.Refused) as e:
        return 1, [f"FAIL: {e}"]
    out = [f"UNMAPPED {name}" for name in board["unmapped"]]
    previous = store.read_json(candidates_path(), {"next_n": 1})
    last_sweep = sweeps().get("last_at")
    lines, last = stats.season_lines_from(games), _last_season(season)
    flags = calibrate.double_flags(games)
    found, n = {}, previous.get("next_n", 1)
    for game in _upcoming(now, games, cfg):
        board_game = _match(game, board)
        if board_game is None:
            continue
        inputs, cands = _price_game(game, lines, last, params, flags, board_game, cfg, passed)
        for c in cands:
            inputs = dict(inputs, odds_evidence=board["evidence"])
            row = {**c, "season": game["season"], "round": game["round"], "at": now.isoformat(), "inputs": inputs}
            model_stake, wallet = _wallet_stake("model", c, game["season"], game["round"], cfg)
            try:
                if model_stake <= 0:
                    tips.record_refused(c, "model", "round cap", now.isoformat())
                else:
                    tips.record_tip({**row, "track": "model", "stake": model_stake, "wallet_before": wallet})
            except tips.Duplicate:
                tips.record_refused(c, "model", "duplicate", now.isoformat())
            if any(t["game"] == game["key"] and t["family"] == c["family"] for t in tips.replay("reviewed")["open"]):
                continue
            cid = f"c-{n}"
            n += 1
            found[cid] = {**row, "id": cid, "home": game["home"], "away": game["away"], "start_utc": game["start_utc"],
                          "neutral": game["neutral"], "expires": game["start_utc"]}
            stake, _ = _wallet_stake("reviewed", c, game["season"], game["round"], cfg)
            news_flag = " NEWS-AFTER-LINES" if any(not last_sweep or (i.get("seen_utc") or "") > last_sweep
                                                   for i in news.recent(game["key"])) else ""
            out.append(f"CANDIDATE {cid} {game['key']} {c['family']} {pick_text(c, game)} @{c['odds']:.2f} "
                       f"{c['book']} edge={c['edge']:.3f} stake={stake}{news_flag}")
    store.write_json(candidates_path(), {"next_n": n, "sweep_at": now.isoformat(), "candidates": found})
    mark_sweep(kind, now, games, cfg)
    s = budget.status(now)
    out.append(f"OK sweep={kind} candidates={len(found)} games_priced={len(board['games'])} "
               f"used={s['used']}/{s['allowance']}")
    return 0, out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Discovery sweep, when due.")
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        code, lines = run()
    except (stats.StatsError, fetch.FetchError) as e:
        code, lines = 1, [f"FAIL: {type(e).__name__}: {e}"]
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
