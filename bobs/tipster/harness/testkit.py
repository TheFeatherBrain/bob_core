"""Shared test helpers: a throwaway Tipster, recorded fixtures, and core's plain-assert runner.

Every test runs against a temp copy of this Bob's real manifest, so no test can write into the real
data folder and every test sees the [tipster] values the Bob really runs with.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tipsterpath  # noqa: E402,F401
import bobcfg  # noqa: E402

MANIFEST = tipsterpath.BOB / "bob.toml"
FIXTURES = tipsterpath.HERE / "fixtures"


def temp_bob(replace: dict[str, str] | None = None) -> pathlib.Path:
    """A temp copy of the manifest, always starting from mode off: the person switching the real
    Bob on must not change what the tests see."""
    bob = pathlib.Path(tempfile.mkdtemp()).resolve() / "tipster"
    bob.mkdir()
    text = re.sub(r'(?m)^mode = "\w+"', 'mode = "off"', MANIFEST.read_text(encoding="utf-8"))
    for old, new in (replace or {}).items():
        assert old in text, f"{old!r} is not in the manifest"
        text = text.replace(old, new)
    (bob / "bob.toml").write_text(text, encoding="utf-8")
    os.environ["BOB_MANIFEST"] = str(bob / "bob.toml")
    os.environ["CLAUDE_CONFIG_DIR"] = str(bob / "config")
    bobcfg.config.cache_clear()
    return bob


def put(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def run(namespace: dict) -> int:
    tests = [(n, f) for n, f in sorted(namespace.items()) if n.startswith("test_") and callable(f)]
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
