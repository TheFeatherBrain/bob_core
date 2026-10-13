#!/usr/bin/env python3
"""route.py decides which Bob answers a Slack message when several Bobs share one Slack account.

The Bob that watches Slack (the front door) answers unless the message clearly belongs to another
Bob. "Clearly" is deterministic, so two runs never disagree:

* a message that starts with a Bob's prefix (after the @-mention) goes to that Bob;
* otherwise, if exactly one Bob's keywords appear as whole words, it goes to that Bob;
* keywords from two Bobs, or none, mean the front door answers.

Run:  python3 harness/test_route.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import route  # noqa: E402

TUTOR = {"slug": "tutor", "prefixes": ["tutor:"], "keywords": ["grammar", "vocabulary", "lesson"]}
INVESTOR = {"slug": "investor", "prefixes": ["investor:"], "keywords": ["lesson", "portfolio"]}
MENTION = "<@U00000000EX|Bob> "


def test_prefix_after_the_mention_routes():
    assert route.route(MENTION + "tutor: explain the past perfect", [TUTOR, INVESTOR]) == "tutor"


def test_prefix_is_case_insensitive_and_ignores_leading_space():
    assert route.route(MENTION + "  TUTOR: anything?", [TUTOR]) == "tutor"


def test_prefix_wins_over_another_bobs_keywords():
    assert route.route(MENTION + "investor: is this grammar right?", [TUTOR, INVESTOR]) == "investor"


def test_one_bobs_keyword_routes():
    assert route.route(MENTION + "can you check the GRAMMAR of this?", [TUTOR, INVESTOR]) == "tutor"


def test_keyword_must_be_a_whole_word():
    assert route.route(MENTION + "the grammars of two languages", [TUTOR]) is None


def test_keywords_from_two_bobs_stay_with_the_front_door():
    assert route.route(MENTION + "grammar of the portfolio report", [TUTOR, INVESTOR]) is None


def test_a_shared_keyword_alone_stays_with_the_front_door():
    assert route.route(MENTION + "next lesson please", [TUTOR, INVESTOR]) is None


def test_nothing_matching_stays_with_the_front_door():
    assert route.route(MENTION + "good morning", [TUTOR, INVESTOR]) is None


def test_prefix_text_elsewhere_in_the_message_is_not_a_prefix():
    assert route.route(MENTION + "tell me about tutor: the team", [TUTOR]) is None


def test_cli_reads_manifests_and_prints_the_slug_or_front():
    tmp = pathlib.Path(__import__("tempfile").mkdtemp())
    (tmp / "tutor.toml").write_text('[bob]\nslug = "tutor"\n[routing]\nprefixes = ["tutor:"]\nkeywords = ["grammar"]\n')
    (tmp / "front.toml").write_text('[bob]\nslug = "front"\n')
    run = lambda text: subprocess.run(
        [sys.executable, str(HERE / "route.py"), "--text", text, str(tmp / "tutor.toml"), str(tmp / "front.toml")],
        capture_output=True, text=True).stdout.strip()
    assert run(MENTION + "tutor: hi") == "tutor"
    assert run(MENTION + "hi") == "front"


def test_cli_reads_every_bob_from_a_fleet_file():
    tmp = pathlib.Path(__import__("tempfile").mkdtemp())
    for slug, prefix in (("tutor", "tutor:"), ("investor", "investor:")):
        (tmp / slug).mkdir()
        (tmp / slug / "bob.toml").write_text(f'[bob]\nslug = "{slug}"\n[routing]\nprefixes = ["{prefix}"]\n')
    (tmp / "fleet.toml").write_text(
        f'[[bob]]\nslug = "tutor"\nport = 1\npath = {json.dumps(str(tmp / "tutor"))}\n\n'
        f'[[bob]]\nslug = "investor"\nport = 2\npath = {json.dumps(str(tmp / "investor"))}\n')
    run = lambda text: subprocess.run(
        [sys.executable, str(HERE / "route.py"), "--text", text, "--fleet", str(tmp / "fleet.toml")],
        capture_output=True, text=True).stdout.strip()
    assert run(MENTION + "investor: how is NVDA?") == "investor"
    assert run(MENTION + "tutor: next lesson") == "tutor"
    assert run(MENTION + "hello") == "front"


def test_cli_without_any_bob_is_an_error():
    out = subprocess.run([sys.executable, str(HERE / "route.py"), "--text", "hi"],
                         capture_output=True, text=True)
    assert out.returncode != 0 and "name the Bobs" in out.stderr, out.stderr


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 - a crash is a failure, reported by name
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
