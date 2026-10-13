#!/usr/bin/env python3
"""The Slack state tools from an empty start: the first cursor and the first tracked thread.

A fresh Bob has no cursors file and no roots files. The watch must be able to create both through
the tools, without hand-written state, and still refuse what it always refused: a timestamp that is
not in the saved read, and a channel nobody enumerated.

Run:  python3 harness/test_slack_state.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
EXAMPLE = (HERE.parent / "bob.example.toml").read_text()

#: A saved channel read in the shape the Slack tools print: header, messages, thread markers.
HISTORY = """Channel: #general (C00000000CH)

Message TS: 1790000000.000100
From: someone
hello
Thread: 2 replies

Message TS: 1790000100.000200
From: someone else
no thread here

Message TS: 1790000200.000300
another thread
Thread: 1 reply
"""
#: The same shape, from a channel the manifest gives no default roots file.
OTHER = HISTORY.replace("#general (C00000000CH)", "#random (C00000000ZZ)")


def bob() -> tuple[pathlib.Path, dict]:
    root = pathlib.Path(tempfile.mkdtemp()).resolve()
    (root / "bob.toml").write_text(EXAMPLE)
    config = root / "config"
    env = {k: v for k, v in os.environ.items() if k != "BOB_MANIFEST"}
    env.update(BOB_MANIFEST=str(root / "bob.toml"), CLAUDE_CONFIG_DIR=str(config))
    return root, env


def run(env: dict, cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, env=env, capture_output=True, text=True)


def test_first_cursor_needs_new_and_creates_the_file():
    root, env = bob()
    (root / "read.txt").write_text(HISTORY)
    refused = run(env, root, str(HERE / "slack_seen.py"), "cursor", "--read", "read.txt",
                  "--channel", "D00000000DM", "1790000100.000200")
    assert refused.returncode == 1 and "--new" in refused.stdout, refused.stdout + refused.stderr
    ok = run(env, root, str(HERE / "slack_seen.py"), "cursor", "--read", "read.txt",
             "--channel", "D00000000DM", "--new", "1790000100.000200")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    cursors = json.loads((root / "config" / "slack-watch-dm-cursors.json").read_text())
    assert cursors == {"D00000000DM": "1790000100.000200"}, cursors


def test_cursor_still_refuses_a_ts_not_in_the_read():
    root, env = bob()
    (root / "read.txt").write_text(HISTORY)
    out = run(env, root, str(HERE / "slack_seen.py"), "cursor", "--read", "read.txt",
              "--channel", "D00000000DM", "--new", "1799999999.000000")
    assert out.returncode == 1 and "does not contain" in out.stdout, out.stdout


def test_new_refuses_something_that_is_not_a_channel_id():
    root, env = bob()
    (root / "read.txt").write_text(HISTORY)
    out = run(env, root, str(HERE / "slack_seen.py"), "cursor", "--read", "read.txt",
              "--channel", "general", "--new", "1790000100.000200")
    assert out.returncode == 1 and "not a Slack channel id" in out.stdout, out.stdout


def test_roots_go_to_the_channels_default_file_without_naming_it():
    root, env = bob()
    (root / "read.txt").write_text(HISTORY)
    out = run(env, root, str(HERE / "slack_roots.py"), "--history", "read.txt", "--append")
    assert out.returncode == 0, out.stdout + out.stderr
    tracked = (root / "config" / "slack-watch-threads.txt").read_text().split()
    assert tracked == ["1790000000.000100", "1790000200.000300"], tracked


def test_roots_from_an_unmapped_channel_go_to_the_dm_file_with_their_channel():
    root, env = bob()
    (root / "read.txt").write_text(OTHER)
    out = run(env, root, str(HERE / "slack_roots.py"), "--history", "read.txt", "--append")
    assert out.returncode == 0, out.stdout + out.stderr
    lines = (root / "config" / "slack-watch-threads-dm.txt").read_text().splitlines()
    assert lines == ["1790000000.000100 C00000000ZZ", "1790000200.000300 C00000000ZZ"], lines


def test_appended_roots_are_due_with_their_channel():
    root, env = bob()
    (root / "read.txt").write_text(HISTORY)
    run(env, root, str(HERE / "slack_roots.py"), "--history", "read.txt", "--append")
    due = run(env, root, str(HERE / "slack_threads.py"), "--due", "--window-hours", "1000000")
    assert due.stdout.split("\n")[:2] == ["1790000000.000100 C00000000CH",
                                          "1790000200.000300 C00000000CH"], due.stdout


def test_appending_twice_adds_nothing_the_second_time():
    root, env = bob()
    (root / "read.txt").write_text(HISTORY)
    for _ in range(2):
        run(env, root, str(HERE / "slack_roots.py"), "--history", "read.txt", "--append")
    tracked = (root / "config" / "slack-watch-threads.txt").read_text().split()
    assert len(tracked) == 2, tracked


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
