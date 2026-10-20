#!/usr/bin/env python3
"""teams: an odds name maps to a club code only through the alias table; anything else is None.

Run:  python3 harness/test_teams.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import stats  # noqa: E402
import teams  # noqa: E402
import testkit  # noqa: E402


def test_alias_table_covers_2026_clubs():
    codes = {g["home"] for g in stats.parse_games(testkit.fixture("stats/games-E2026.json"))}
    assert len(codes) == 20
    mapped = set(teams.ALIASES.values())
    assert codes <= mapped, sorted(codes - mapped)


def test_feed_names_map():
    for name, code in (("Olympiacos Piraeus", "OLY"), ("Panathinaikos", "PAN"), ("Real Madrid", "MAD"),
                       ("Anadolu Efes", "IST"), ("Fenerbahce Beko", "ULK"), ("Crvena Zvezda", "RED")):
        assert teams.code(name) == code, name


def test_case_spacing_and_accents_do_not_matter():
    assert teams.code("  olympiacos   PIRAEUS ") == "OLY"
    assert teams.code("Fenerbahçe") == "ULK"
    assert teams.code("Žalgiris Kaunas") == "ZAL"


def test_greek_names_map():
    assert teams.code("Ολυμπιακός") == "OLY" and teams.code("Παναθηναϊκός") == "PAN"


def test_unknown_or_partial_name_is_none():
    for name in ("Olympiacos B", "Real", "Maccabi Haifa", "Hapoel Jerusalem", "", "Barcelona Dragons"):
        assert teams.code(name) is None, name


def test_aliases_never_collide():
    seen = {}
    for alias, code in teams.ALIASES.items():
        key = teams.normal(alias)
        assert seen.setdefault(key, code) == code, (alias, seen[key], code)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
