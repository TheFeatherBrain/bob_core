#!/usr/bin/env python3
"""News watch: read the free feeds, keep each new item with its evidence, and flag the tipped games
it touches.

Why feeds and not odds: news is free and odds requests are scarce, so news decides when a tipped
game's odds are worth rechecking (spec 6). Why the item's own time decides "after the tip": news
published before the tip was already public when the tip was made. Why only reviewed tips are
touched: those are the ones the person holds; the model track is a control and gets no updates.
Why players match by full name, or by surname only when it is 7 letters or longer: "Jones" or
"Butler" alone would touch a game on every NBA story.

Why the official injury report page is never read here: euroleaguebasketball.net answers scripts
with a bot challenge (429, X-Vercel-Mitigated: challenge, 2026-10-20), and getting around a
challenge is not ours to do. Eurohoops publishes the report as an article each round, and its feed
carries the full text: such items are flagged `injury_report` and kept whole for Claude to read.
BasketNews is not read (its feed mixes NBA and empty dates; spec 3.3 makes it a filler only).

Usage:
    python3 harness/news.py --poll             # TOUCHED <tip id> <title> [ev:...] lines, then OK
    python3 harness/news.py --recent E2026-69  # ITEM lines naming either club, with their evidence
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import fetch  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import teams  # noqa: E402
import tips  # noqa: E402
import tipstercfg  # noqa: E402

FEEDS = {
    "eurohoops": "https://www.eurohoops.net/en/feed/",
    "sportando": "https://sportando.basketball/en/feed/",
    "gazzetta": "https://www.gazzetta.gr/rss",
    "novasports": "https://www.novasports.gr/rss",
}
HEADERS = {"User-Agent": "bob-tipster/1 (personal research)"}
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
TAG = re.compile(r"<[^>]+>")
SURNAME_MIN = 7
POLL_GAP = dt.timedelta(hours=3)


class NewsError(Exception):
    pass


def items_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "news.jsonl"


def _state_path() -> pathlib.Path:
    return tipstercfg.state_dir() / "news-poll.json"


def _text(markup: str | None) -> str:
    return " ".join(html.unescape(TAG.sub(" ", markup or "")).split())


def _utc(stamp: str | None) -> str | None:
    try:
        when = email.utils.parsedate_to_datetime(stamp)
    except (TypeError, ValueError):
        return None
    if when is None or when.tzinfo is None:
        return None
    return when.astimezone(dt.timezone.utc).isoformat()


def parse_rss(raw: str) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise NewsError(f"not a readable feed: {e}") from None
    out = []
    for item in root.iter("item"):
        title = _text(item.findtext("title"))
        out.append({"title": title, "link": (item.findtext("link") or "").strip(),
                    "published_utc": _utc(item.findtext("pubDate")),
                    "summary": _text(item.findtext("description"))[:500],
                    "body": _text(item.findtext(CONTENT))[:6000],
                    "injury_report": "injury report" in title.lower()})
    return out


def _athens_dates_with_games(games: list[dict]) -> set[str]:
    import discover
    return {discover.athens_date(stats.parse_utc(g["start_utc"])) for g in games}


def poll_due(now: dt.datetime, games: list[dict], state: dict) -> bool:
    import discover
    today = discover.athens_date(now)
    tomorrow = discover.athens_date(now + dt.timedelta(days=1))
    if {today, tomorrow} & _athens_dates_with_games(games):
        return True
    last = state.get("last_poll")
    return last is None or now - dt.datetime.fromisoformat(last) >= POLL_GAP


def _pattern(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)")


def roster(season: str, club: str) -> set[str]:
    """Full names ("jared butler") and long surnames of players seen for `club` in cached box scores."""
    names = set()
    for path in stats.season_dir(season).glob("box-*.json"):
        box = store.read_json(path, {})
        for side in ("local", "road"):
            for p in (box.get(side) or {}).get("players", []):
                person = p.get("player", {})
                if person.get("club", {}).get("code") != club:
                    continue
                surname, _, first = person.get("person", {}).get("name", "").partition(",")
                full = teams.normal(f"{first} {surname}")
                if full:
                    names.add(full)
                if len(teams.normal(surname)) >= SURNAME_MIN:
                    names.add(teams.normal(surname))
    return names


def _club_names(club: str) -> set[str]:
    return {teams.normal(a) for a, c in teams.ALIASES.items() if c == club}


def _names_club(text: str, club: str, season: str | None) -> bool:
    phrases = _club_names(club) | (roster(season, club) if season else set())
    return any(_pattern(p).search(text) for p in phrases)


def _item_text(item: dict) -> str:
    return teams.normal(" ".join([item.get("title", ""), item.get("summary", ""), item.get("body", "")]))


def touches(item: dict, games: list[dict], now: dt.datetime) -> list[str]:
    by_key = {g["key"]: g for g in games}
    when = item.get("published_utc") or item.get("seen_utc")
    text = _item_text(item)
    hit = []
    for tip in tips.replay("reviewed")["open"]:
        game = by_key.get(tip["game"])
        if game is None or stats.parse_utc(game["start_utc"]) <= now or not when or when <= tip["at"]:
            continue
        if any(_names_club(text, club, game["season"]) for club in (game["home"], game["away"])):
            hit.append(tip["id"])
    return hit


def recent(game_key: str, limit: int = 20) -> list[dict]:
    season = game_key.split("-")[0]
    game = next((g for g in stats.cached_games(season) if g["key"] == game_key), None)
    if game is None:
        return []
    items, _ = store.read_jsonl(items_path())
    named = [i for i in items if any(_names_club(_item_text(i), club, None) for club in (game["home"], game["away"]))]
    return named[-limit:]


def poll(now: dt.datetime, *, getter=fetch.get) -> dict:
    if not tipstercfg.is_on():
        return {"items": 0, "touched": [], "failed": [], "off": True}
    known = {i.get("link") for i in store.read_jsonl(items_path())[0]}
    new, failed = [], []
    for name, url in FEEDS.items():
        try:
            raw, where = fetch.read(f"news-{name}", url, HEADERS, getter=getter)
            parsed = parse_rss(raw)
        except (fetch.FetchError, NewsError):
            failed.append(name)
            continue
        for item in parsed:
            if item["link"] and item["link"] not in known:
                known.add(item["link"])
                new.append({**item, "source": name, "seen_utc": now.isoformat(), "evidence": str(where)})
    if new:
        store.append_jsonl(items_path(), new)
    store.write_json(_state_path(), {"last_poll": now.isoformat()})
    games = stats.cached_games(_season(now))
    touched = [(tid, item) for item in new for tid in touches(item, games, now)]
    return {"items": len(new), "touched": touched, "failed": failed}


def _season(now: dt.datetime) -> str:
    return f"E{now.year if now.month >= 7 else now.year - 1}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Read the news feeds and flag touched tips.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--poll", action="store_true")
    group.add_argument("--recent", metavar="GAME")
    args = parser.parse_args(argv)
    if args.recent:
        items = recent(args.recent)
        for i in items:
            print(f"ITEM {i.get('published_utc') or i.get('seen_utc')} {i.get('source')} {i['title']} [ev:{i['evidence']}]")
        print(f"OK items={len(items)}")
        return 0
    now = dt.datetime.now(dt.timezone.utc)
    if not tipstercfg.is_on():
        print("OK mode off")
        return 0
    games = stats.cached_games(_season(now))
    if not poll_due(now, games, store.read_json(_state_path(), {})):
        print("OK not due")
        return 0
    result = poll(now)
    for name in result["failed"]:
        print(f"FAILED {name}: could not read the feed this time")
    for tid, item in result["touched"]:
        flag = " INJURY-REPORT" if item["injury_report"] else ""
        print(f"TOUCHED {tid} {item['title']}{flag} [ev:{item['evidence']}]")
    print(f"OK items={result['items']} touched={len(result['touched'])}")
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
