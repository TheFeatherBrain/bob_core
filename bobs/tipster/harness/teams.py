#!/usr/bin/env python3
"""Map a bookmaker's or provider's team name to a Euroleague club code, or to nothing.

Why a hand-written table and exact match: "Real" could be Real Madrid or Real Betis, "Olympiacos
B" is a different team, and a fuzzy match that attaches odds to the wrong game produces an edge
that does not exist. An unknown name is "could not tell": the game is dropped and the name logged,
so the table grows by a person's (or a reviewed) edit, never by a guess. Case, repeated spaces and
accents are ignored; nothing else is.

The table covers every 2026-27 club in the spellings of the official feed (full, short, editorial
names), common English sponsor-free names, and the Greek names Greek bookmakers use. Spellings
seen in the odds provider's responses are added when they appear (plan Task 0).

Usage:
    python3 harness/teams.py "Olympiacos Piraeus"     # OLY, or FAIL: not in the alias table
"""
from __future__ import annotations

import pathlib
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401

_TABLE = {
    "ASV": ["LDLC ASVEL Villeurbanne", "LDLC ASVEL", "ASVEL", "ASVEL Villeurbanne", "Asvel Lyon-Villeurbanne",
            "Ασβέλ"],
    "BAR": ["FC Barcelona", "Barcelona", "Barca", "FC Barcelona Basket", "Μπαρτσελόνα"],
    "BAS": ["Kosner Baskonia Vitoria-Gasteiz", "Baskonia", "Kosner Baskonia", "Saski Baskonia",
            "Baskonia Vitoria-Gasteiz", "Μπασκόνια"],
    "BES": ["Besiktas Istanbul", "Besiktas", "Besiktas Basketbol", "Μπεσίκτας"],
    "DUB": ["Dubai Basketball", "Dubai", "Ντουμπάι"],
    "HTA": ["Hapoel IBI Tel Aviv", "Hapoel TLV", "Hapoel Tel Aviv", "Hapoel Tel-Aviv", "Χάποελ Τελ Αβίβ"],
    "IST": ["Anadolu Efes Istanbul", "Anadolu Efes", "Efes", "Εφές", "Αναντολού Εφές"],
    "MAD": ["Real Madrid", "Real Madrid Baloncesto", "Ρεάλ Μαδρίτης"],
    "MIL": ["Armani Olimpia Milan", "Olimpia Milano", "EA7 Emporio Armani Milan", "Armani Milan", "Olimpia Milan",
            "Ολίμπια Μιλάνο", "Αρμάνι Μιλάνο"],
    "MUN": ["FC Bayern Munich", "Bayern Munich", "Bayern", "FC Bayern Munchen", "Bayern Munchen",
            "Μπάγερν Μονάχου", "Μπάγερν"],
    "OLY": ["Olympiacos Piraeus", "Olympiacos", "Olympiakos", "Olympiacos BC", "Ολυμπιακός"],
    "PAM": ["Valencia Basket", "Valencia", "Βαλένθια"],
    "PAN": ["Panathinaikos AKTOR Athens", "Panathinaikos", "Panathinaikos Athens", "Panathinaikos BC",
            "Παναθηναϊκός"],
    "PAR": ["Partizan Mozzart Bet Belgrade", "Partizan", "Partizan Belgrade", "KK Partizan", "Παρτιζάν"],
    "PRS": ["Paris Basketball", "Paris", "Παρί"],
    "RED": ["Crvena Zvezda Meridianbet Belgrade", "Crvena Zvezda", "Crvena", "Crvena Zvezda Belgrade",
            "KK Crvena Zvezda", "Red Star Belgrade", "Red Star", "Ερυθρός Αστέρας"],
    "TEL": ["Maccabi Rapyd Tel Aviv", "Maccabi Tel Aviv", "Maccabi", "Maccabi Playtika Tel Aviv",
            "Μακάμπι Τελ Αβίβ"],
    "ULK": ["Fenerbahce Tarfin Istanbul", "Fenerbahce", "Fenerbahce Beko", "Fenerbahce Istanbul",
            "Φενερμπαχτσέ"],
    "VIR": ["Virtus Bologna", "Virtus Segafredo Bologna", "Virtus", "Βίρτους Μπολόνια"],
    "ZAL": ["Zalgiris Kaunas", "Zalgiris", "Ζαλγκίρις"],
}


def normal(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name.casefold())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


ALIASES = {alias: code for code, names in _TABLE.items() for alias in names}
_BY_NORMAL = {normal(alias): code for alias, code in ALIASES.items()}


def code(name: str) -> str | None:
    return _BY_NORMAL.get(normal(name)) if name else None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print('usage: teams.py "NAME"')
        return 2
    found = code(argv[0])
    if found is None:
        print(f"FAIL: {argv[0]!r} not in the alias table (could not tell)")
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
