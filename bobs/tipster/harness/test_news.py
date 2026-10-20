#!/usr/bin/env python3
"""news: feeds parsed, items matched to tipped games by club or player, only news after the tip
counts, and feeds are read often on game days and rarely otherwise.

Fixtures in harness/fixtures/news/ are the four feeds as read on 2026-10-20.

Run:  python3 harness/test_news.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import news  # noqa: E402
import stats  # noqa: E402
import testkit  # noqa: E402
import tips  # noqa: E402
import tipsterworld as w  # noqa: E402

FEEDS = ("eurohoops", "sportando", "gazzetta", "novasports")


def _open_tip(game="E2026-66", at="2026-10-21T08:00:00+00:00", track="reviewed") -> str:
    return tips.record_tip({"track": track, "at": at, "season": "E2026", "round": 7, "game": game,
                            "family": "side", "market": "moneyline", "side": "away", "line": None,
                            "book": "bet365", "odds": 2.0, "other_book_odds": {}, "p_win": 0.6, "p_push": 0.0,
                            "edge": 0.2, "kelly": 0.2, "stake": 40, "wallet_before": 1000, "inputs": {},
                            "provider_id": "p66"})


def _item(title: str, seen: str = "2026-10-21T09:00:00+00:00", summary: str = "") -> dict:
    return {"title": title, "summary": summary, "link": f"https://x/{abs(hash(title))}", "published_utc": seen,
            "seen_utc": seen, "evidence": "ev/1", "source": "eurohoops"}


def test_parse_each_feed_fixture():
    for name in FEEDS:
        items = news.parse_rss(testkit.fixture(f"news/{name}.xml"))
        assert len(items) >= 10, (name, len(items))
        assert all(i["title"] and i["link"].startswith("http") for i in items), name
        assert all(i["published_utc"] is None or i["published_utc"].endswith("+00:00") for i in items), name
        assert all("<" not in i["summary"] for i in items), name


def test_published_times_are_utc():
    items = news.parse_rss(testkit.fixture("news/gazzetta.xml"))
    # Gazzetta stamps +0200; "Tue, 20 Oct 2026 11:13:11 +0200" is 09:13:11 UTC
    assert any(i["published_utc"] == "2026-10-20T09:13:11+00:00" for i in items)


INJURY_RSS = ('<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><item>'
              '<title>EuroLeague breaks down Round 7 injury report</title>'
              '<link>https://www.eurohoops.net/en/euroleague/1/injury-report-round-7/</link>'
              '<pubDate>Wed, 21 Oct 2026 09:00:00 +0000</pubDate><description>&lt;p&gt;Short&lt;/p&gt;</description>'
              '<content:encoded><![CDATA[<p>Partizan: Carlik Jones (out).</p><p>Real Madrid: all available.</p>]]>'
              '</content:encoded></item></channel></rss>')


def test_injury_report_item_kept_whole_and_flagged():
    items = news.parse_rss(INJURY_RSS)
    assert items[0]["injury_report"] is True and items[0]["body"] == "Partizan: Carlik Jones (out). Real Madrid: all available."
    assert news.parse_rss(testkit.fixture("news/sportando.xml"))[0]["injury_report"] is False


def test_injury_report_touches_by_its_body():
    w.world()
    tid = _open_tip()
    games = stats.games("E2026", now=w.NOW, getter=w.never)
    item = dict(news.parse_rss(INJURY_RSS)[0], seen_utc="2026-10-21T09:00:00+00:00", evidence="ev/1")
    assert news.touches(item, games, w.NOW) == [tid]


def test_item_touches_game_by_club_or_player():
    w.world()
    tid = _open_tip()  # E2026-66 Partizan v Crvena Zvezda, 22 Oct
    games = stats.games("E2026", now=w.NOW, getter=w.never)
    assert news.touches(_item("Partizan prepare for the derby"), games, w.NOW) == [tid]
    assert news.touches(_item("Ερυθρός Αστέρας: νέα"), games, w.NOW) == [tid]
    # Jared Butler plays for Crvena Zvezda in the recorded box score of game 1
    assert news.touches(_item("Jared Butler questionable for Thursday"), games, w.NOW) == [tid]
    assert news.touches(_item("Butler-Carter trade rumours", summary="NBA"), games, w.NOW) == []
    assert news.touches(_item("Panathinaikos sign a guard"), games, w.NOW) == []


def test_item_before_tip_does_not_touch():
    w.world()
    _open_tip(at="2026-10-21T10:00:00+00:00")
    games = stats.games("E2026", now=w.NOW, getter=w.never)
    assert news.touches(_item("Partizan injury update", seen="2026-10-21T09:00:00+00:00"), games, w.NOW) == []


def test_model_track_tips_are_not_touched():
    w.world()
    _open_tip(track="model")
    games = stats.games("E2026", now=w.NOW, getter=w.never)
    assert news.touches(_item("Partizan injury update"), games, w.NOW) == []


def test_poll_cadence_off_days():
    w.world()
    games = stats.games("E2026", now=w.NOW, getter=w.never)
    # 21 Oct has a game: due every run
    assert news.poll_due(w.NOW, games, {"last_poll": (w.NOW - dt.timedelta(minutes=30)).isoformat()})
    # 25 Oct (Sunday): no game that day or the next; due only 3 hours after the last poll
    sunday = dt.datetime(2026, 10, 25, 9, 0, tzinfo=dt.timezone.utc)
    assert not news.poll_due(sunday, games, {"last_poll": (sunday - dt.timedelta(hours=2)).isoformat()})
    assert news.poll_due(sunday, games, {"last_poll": (sunday - dt.timedelta(hours=3)).isoformat()})
    assert news.poll_due(sunday, games, {})
    # 26 Oct: games tomorrow (27th)
    monday = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)
    assert news.poll_due(monday, games, {"last_poll": (monday - dt.timedelta(minutes=30)).isoformat()})


def test_poll_saves_new_items_once():
    w.world()
    _open_tip(at="2026-10-19T08:00:00+00:00")
    raw = {u: testkit.fixture(f"news/{n}.xml") for n, u in news.FEEDS.items()}
    result = news.poll(w.NOW, getter=lambda u, h: raw[u])
    stored, _ = news.store.read_jsonl(news.items_path())
    assert result["items"] == len(stored) > 30 and all(i["evidence"] for i in stored)
    again = news.poll(w.NOW + dt.timedelta(hours=1), getter=lambda u, h: raw[u])
    assert again["items"] == 0


def test_poll_reads_only_the_feeds():
    w.world()
    calls = []

    def getter(url, headers):
        calls.append(url)
        return INJURY_RSS if "eurohoops" in url else "<rss><channel></channel></rss>"
    result = news.poll(w.NOW, getter=getter)
    assert sorted(calls) == sorted(news.FEEDS.values()) and result["items"] == 1


def test_one_bad_feed_does_not_stop_the_others():
    w.world()

    def getter(url, headers):
        if "sportando" in url:
            raise news.fetch.FetchError(url, 503, "HTTP 503")
        return INJURY_RSS if "eurohoops" in url else "not xml"
    result = news.poll(w.NOW, getter=getter)
    assert result["items"] == 1 and sorted(result["failed"]) == ["gazzetta", "novasports", "sportando"]


def test_recent_lists_items_naming_either_club():
    w.world()
    news.store.append_jsonl(news.items_path(), [_item("Partizan win"), _item("Real Madrid news"),
                                                _item("Παρτιζάν: όλα έτοιμα")])
    assert [i["title"] for i in news.recent("E2026-66")] == ["Partizan win", "Παρτιζάν: όλα έτοιμα"]


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
