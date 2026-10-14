#!/usr/bin/env python3
"""Small file helpers every Trader module shares: JSON replaced atomically, JSON Lines appended.

Why atomic JSON: a crash mid-write must leave the old copy, not half a file the next run cannot
read. Why a lock on appends: the daily run and a person running approve.py can write the ledger at
the same moment. Why torn lines are counted, not fatal: a ledger must stay readable after a crash,
and the count makes the damage visible (reconcile then refuses to trade).

Usage (library):
    rows, torn = store.read_jsonl(path)
    store.append_jsonl(path, [row])
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import oscompat  # noqa: E402


def read_json(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, default=str, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path: pathlib.Path) -> tuple[list[dict], int]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], 0
    rows, torn = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            torn += 1
    return rows, torn


def append_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        oscompat.lock(fh)
        try:
            fh.write("".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows))
            fh.flush()
        finally:
            oscompat.unlock(fh)
