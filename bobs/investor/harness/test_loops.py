#!/usr/bin/env python3
"""Loops are code too: each must register and record first, and call only CLIs and flags that exist.

A loop that names a renamed script fails at 07:30 with nobody watching; this test fails now instead.

Run:  python3 harness/test_loops.py
"""
from __future__ import annotations

import pathlib
import re
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import testkit  # noqa: E402

BOB, CORE = scoutpath.BOB, scoutpath.CORE
OWN = tomllib.loads(testkit.MANIFEST.read_text())["loops"]["own"]
CALL = re.compile(r"python3 ((?:\.\./\.\./)?harness/[\w/]+\.py)((?: --[\w-]+| [\w<>.-]+)*)")


def _loops() -> dict[str, str]:
    return {pathlib.PurePosixPath(rel).stem: (BOB / rel).read_text() for rel in OWN}


def test_every_own_loop_exists():
    assert all((BOB / rel).is_file() for rel in OWN), OWN


def test_each_loop_registers_then_records_first():
    for name, text in _loops().items():
        lines = [l for l in text.splitlines() if l.strip()]
        assert lines[1].startswith("FIRST: CronList"), name
        assert lines[2].endswith(f"loop_passes.py --record {name}"), name


def test_loops_call_only_existing_clis_and_flags():
    for name, text in _loops().items():
        for script, args in CALL.findall(text):
            path = (CORE / script[len("../../"):]) if script.startswith("../../") else BOB / script
            assert path.is_file(), f"{name}: {script} does not exist"
            source = path.read_text(encoding="utf-8")
            for flag in re.findall(r"--[\w-]+", args):
                assert f'"{flag}"' in source, f"{name}: {script} has no {flag}"


if __name__ == "__main__":
    raise SystemExit(testkit.run(globals()))
