#!/usr/bin/env python3
"""The OddsPapi adapter: Euroleague fixtures, main-line prices per book, and closing prices.

Written from the step 0 responses (docs/step0/FINDINGS.md), which settled the shape:
- Every line is its own market id. The bundled table (harness/oddspapi_markets.json, the
  basketball full-game moneyline, spreads and totals from /v4/markets) gives each id its type and
  line (`handicap`). Ids outside the table (quarters, halves, team totals, props) are ignored.
- The spreads line is the home team's: Paris, home underdog, is +4.5. That is the board's
  convention, so it is used as is.
- A book's main line is the market whose prices carry `mainLine: true`.
- Stoiximan prices come only per game (/v4/odds), so a sweep reads the fixture list once and each
  game once, with every book in the same call. Each read is one request on the budget.
- Closing prices come from /v4/historical-odds, which is free: the last price before the start,
  on the line whose two prices are closest to even (the history carries no mainLine flag).

Usage (library, through odds.py):
    rows = OddsPapi.round(read, cfg, key, now, until)
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import budget  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import tipstercfg  # noqa: E402

BASE = "https://api.oddspapi.io/v4"
TOURNAMENT = 138
KIND = {"moneyline": "moneyline", "spreads": "handicap", "totals": "total"}
SIDE = {"1": "home", "2": "away", "Over": "over", "Under": "under"}
EMPTY = {"moneyline": None, "handicap": None, "total": None}


class AdapterError(Exception):
    pass


@functools.cache
def markets() -> dict[int, dict]:
    rows = json.loads((tipsterpath.HERE / "oddspapi_markets.json").read_text(encoding="utf-8"))
    return {m["marketId"]: {**m, "names": {o["outcomeId"]: o["outcomeName"] for o in m["outcomes"]}} for m in rows}


def _z(stamp: str) -> str:
    return stats.parse_utc(stamp).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_fixtures(raw: str) -> list[dict]:
    return [{"provider_id": f["fixtureId"], "start_utc": _z(f["startTime"]), "home_name": f["participant1Name"],
             "away_name": f["participant2Name"], "status": f["statusId"], "has_odds": bool(f["hasOdds"])}
            for f in json.loads(raw)]


def _entry(meta: dict, sides: dict) -> dict:
    return sides if KIND[meta["marketType"]] == "moneyline" else {"line": meta["handicap"], **sides}


def _gap(entry: dict) -> float:
    a, b = [v for k, v in entry.items() if k != "line"]
    return abs(1 / a - 1 / b)


def parse_books(raw: str) -> dict:
    out = {}
    for book, data in json.loads(raw).get("bookmakerOdds", {}).items():
        found = dict(EMPTY)
        for mid, market in data.get("markets", {}).items():
            meta = markets().get(int(mid))
            if meta is None:
                continue
            sides, main = {}, False
            for oid, outcome in market.get("outcomes", {}).items():
                player = outcome.get("players", {}).get("0") or {}
                price = player.get("price")
                if player.get("active", True) and isinstance(price, (int, float)) and price > 1:
                    sides[SIDE[meta["names"][int(oid)]]] = price
                    main |= bool(player.get("mainLine"))
            if main and len(sides) == 2:
                entry, kind = _entry(meta, sides), KIND[meta["marketType"]]
                if found[kind] is None or _gap(entry) < _gap(found[kind]):
                    found[kind] = entry
        out[book] = found
    return out


def parse_closing(raw: str, start_utc: str) -> dict:
    start = stats.parse_utc(start_utc)
    out = {}
    for book, data in json.loads(raw).get("bookmakers", {}).items():
        found = dict(EMPTY)
        for mid, market in data.get("markets", {}).items():
            meta = markets().get(int(mid))
            if meta is None:
                continue
            sides = {}
            for oid, outcome in market.get("outcomes", {}).items():
                ticks = [t for player in outcome.get("players", {}).values() for t in player
                         if stats.parse_utc(t["createdAt"]) < start and isinstance(t.get("price"), (int, float))]
                if ticks:
                    last = max(ticks, key=lambda t: t["createdAt"])
                    if last["price"] > 1:
                        sides[SIDE[meta["names"][int(oid)]]] = last["price"]
            if len(sides) == 2:
                entry, kind = _entry(meta, sides), KIND[meta["marketType"]]
                if found[kind] is None or _gap(entry) < _gap(found[kind]):
                    found[kind] = entry
        out[book] = found
    return out


def _books(cfg: dict) -> str:
    return ",".join(dict.fromkeys(cfg["tip_books"] + [cfg["sharp_book"]]))


def fixtures_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "odds-fixtures.json"


class OddsPapi:
    built = True
    host = "api.oddspapi.io"

    @staticmethod
    def round(read, cfg: dict, key: str, now: dt.datetime, until: dt.datetime) -> dict:
        raw, _ = read(f"{BASE}/fixtures?tournamentId={TOURNAMENT}&from={now:%Y-%m-%d}"
                      f"&to={until + dt.timedelta(days=1):%Y-%m-%d}&apiKey={key}")
        fixtures = parse_fixtures(raw)
        store.write_json(fixtures_path(), {f["provider_id"]: f for f in fixtures})
        rows, stopped = [], None
        for f in sorted(fixtures, key=lambda f: f["start_utc"]):
            if f["status"] != 0 or not f["has_odds"] or not now < stats.parse_utc(f["start_utc"]) <= until:
                continue
            try:
                raw, where = read(f"{BASE}/odds?fixtureId={f['provider_id']}&bookmakers={_books(cfg)}"
                                  f"&oddsFormat=decimal&apiKey={key}")
            except budget.Refused as e:
                stopped = str(e)
                break
            rows.append({**f, "books": parse_books(raw), "evidence": where})
        return {"rows": rows, "stopped": stopped}

    @staticmethod
    def game(read, cfg: dict, key: str, provider_id: str, now: dt.datetime) -> dict:
        fixture = store.read_json(fixtures_path(), {}).get(provider_id)
        if fixture is None:
            raise AdapterError(f"{provider_id}: not in the cached fixture list; a discovery sweep reads it")
        raw, where = read(f"{BASE}/odds?fixtureId={provider_id}&bookmakers={_books(cfg)}&oddsFormat=decimal&apiKey={key}")
        return {"rows": [{**fixture, "books": parse_books(raw), "evidence": where}], "stopped": None}

    @staticmethod
    def closing(read, cfg: dict, key: str, provider_id: str, start_utc: str) -> dict:
        books = ",".join(dict.fromkeys([cfg["sharp_book"]] + cfg["tip_books"]))
        raw, _ = read(f"{BASE}/historical-odds?fixtureId={provider_id}&bookmakers={books}&apiKey={key}")
        return parse_closing(raw, start_utc)
