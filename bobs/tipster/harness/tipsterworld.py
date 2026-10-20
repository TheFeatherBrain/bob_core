"""Test helper: a temp Tipster with mode on, a fake odds provider and the E2026 fixture as its season.

Only games 1 and 58 count as played (their box scores are fixtures), so every club rates at the
league average and the model's margin is the home advantage: known numbers for discovery tests.
Used by test_discover.py, test_review.py and the suites after them; never imported by the Bob.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import calibrate  # noqa: E402
import odds  # noqa: E402
import stats  # noqa: E402
import store  # noqa: E402
import testkit  # noqa: E402
import tipstercfg  # noqa: E402

NOW = dt.datetime(2026, 10, 21, 8, 0, tzinfo=dt.timezone.utc)
KEY = {"ODDS_API_KEY": "fake-key-123456"}
PARAMS = {"home_adv": 3.0, "k": 8, "sigma_margin": 12.0, "sigma_total": 16.0, "double_adj": 0.0,
          "fitted_on": ["E2025"]}
ML = {"home": 2.00, "away": 1.80}
PRICES: dict[str, dict] = {}


CLOSING: dict[str, dict] = {}


class FakeProvider:
    """One budgeted read per round or game, answering from PRICES; closing from CLOSING."""
    host = "api.oddspapi.io"

    @staticmethod
    def _rows(where, only=None):
        return [{"provider_id": f"p{code}", "start_utc": start, "home_name": home, "away_name": away,
                 "books": books, "evidence": where} for code, (start, home, away, books) in sorted(PRICES.items())
                if only is None or f"p{code}" == only]

    @staticmethod
    def round(read, cfg, key, now, until):
        _, where = read(f"https://api.oddspapi.io/round?apiKey={key}")
        return {"rows": FakeProvider._rows(where), "stopped": None}

    @staticmethod
    def game(read, cfg, key, provider_id, now):
        _, where = read(f"https://api.oddspapi.io/game/{provider_id}?apiKey={key}")
        return {"rows": FakeProvider._rows(where, provider_id), "stopped": None}

    @staticmethod
    def closing(read, cfg, key, provider_id, start_utc):
        read(f"https://api.oddspapi.io/history/{provider_id}?apiKey={key}")
        return CLOSING.get(provider_id)


def book(ml=None, hc=None, tot=None) -> dict:
    return {"moneyline": ml, "handicap": hc, "total": tot}


def price(code: int, start: str, home: str, away: str, **books) -> None:
    PRICES[code] = (start, home, away, books)


def price_paris(home_name: str = "Paris Basketball", **books) -> None:
    """Game 69, Paris v Valencia on 23 Oct 18:45 UTC: neither club has a game in this world, so the
    model margin is the home advantage, 3 points."""
    price(69, "2026-10-23T18:45:00Z", home_name, "Valencia Basket", **(books or {"bet365": book(ml=ML)}))


_connect = socket.socket.connect
#: The games cache is stamped in the future so it never goes stale inside a test: a stale cache
#: would make stats.games read the live feed, and no test may touch the network.
NEVER_STALE = "2099-01-01T00:00:00+00:00"


def _no_network(self, address):
    if address[0] in ("127.0.0.1", "::1", "localhost"):
        return _connect(self, address)
    raise AssertionError(f"a test tried to reach the network: {address}")


def world(mode: str = "on", calibrated: bool = True, extra: dict | None = None) -> pathlib.Path:
    socket.socket.connect = _no_network
    bob = testkit.temp_bob({'mode = "off"': f'mode = "{mode}"', 'odds_provider = "oddspapi"': 'odds_provider = "fake"',
                            **(extra or {})})
    odds.PROVIDERS["fake"] = FakeProvider
    PRICES.clear()
    CLOSING.clear()
    games = stats.parse_games(testkit.fixture("stats/games-E2026.json"))
    games = [g if g["code"] in (1, 58) else dict(g, played=False, home_pts=None, away_pts=None) for g in games]
    store.write_json(stats.season_dir("E2026") / "games.json", {"fetched_utc": NEVER_STALE, "games": games})
    store.write_json(stats.season_dir("E2025") / "games.json", {"fetched_utc": NEVER_STALE, "games": []})
    for code in (1, 58):
        testkit.put(stats.season_dir("E2026") / f"box-{code}.json", testkit.fixture(f"stats/stats-E2026-{code}.json"))
    store.write_json(calibrate.params_path(), PARAMS)
    if calibrated:
        result = {m: {"pass": True, "why": []} for m in tipstercfg.MARKETS}
        calibrate.save(dict(result, n=990, seasons=["E2023"]), PARAMS)
    return bob


def never(url, headers):
    raise AssertionError(f"requested {url}")


def no_sync(season, **kwargs):
    return {"played": 0, "fetched": 0, "refused": []}


def evidence_file(name: str = "injury") -> str:
    path = tipstercfg.evidence_dir() / "2026-10-21" / name / "raw.txt"
    testkit.put(path, "report")
    return str(path)
