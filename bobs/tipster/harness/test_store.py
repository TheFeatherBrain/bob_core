#!/usr/bin/env python3
"""store: JSON files replaced atomically, JSON Lines appended under a lock, torn lines counted.

Run:  python3 harness/test_store.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import store  # noqa: E402
import testkit  # noqa: E402


def test_json_round_trip_and_default():
    folder = pathlib.Path(tempfile.mkdtemp())
    store.write_json(folder / "a" / "b.json", {"x": 1})
    assert store.read_json(folder / "a" / "b.json", None) == {"x": 1}
    assert store.read_json(folder / "missing.json", {"d": 1}) == {"d": 1}
    (folder / "torn.json").write_text("{torn", encoding="utf-8")
    assert store.read_json(folder / "torn.json", []) == []


def test_jsonl_appends_and_counts_torn_lines():
    path = pathlib.Path(tempfile.mkdtemp()) / "l.jsonl"
    assert store.read_jsonl(path) == ([], 0)
    store.append_jsonl(path, [{"a": 1}, {"a": 2}])
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"a": \n')
    store.append_jsonl(path, [{"a": 3}])
    assert store.read_jsonl(path) == ([{"a": 1}, {"a": 2}, {"a": 3}], 1)


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
