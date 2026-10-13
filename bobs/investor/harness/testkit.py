"""Shared test helpers: a throwaway Bob folder and core's test runner.

Every test runs against a temp copy of this Bob's real manifest. A test can then never write into
the real data folder, and a manifest change that breaks a module breaks that module's tests too.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scoutpath  # noqa: E402,F401
import bobcfg  # noqa: E402

MANIFEST = scoutpath.BOB / "bob.toml"


def temp_bob(replace: dict[str, str] | None = None) -> pathlib.Path:
    """A temp Bob folder with the real manifest (text-replaced by `replace`) and an empty theses/;
    BOB_MANIFEST and CLAUDE_CONFIG_DIR point at it until the next call."""
    bob = pathlib.Path(tempfile.mkdtemp()).resolve()
    text = MANIFEST.read_text()
    for old, new in (replace or {}).items():
        assert old in text, f"{old!r} is not in the manifest"
        text = text.replace(old, new)
    (bob / "bob.toml").write_text(text)
    (bob / "theses").mkdir()
    os.environ["BOB_MANIFEST"] = str(bob / "bob.toml")
    os.environ["CLAUDE_CONFIG_DIR"] = str(bob / "config")
    bobcfg.config.cache_clear()
    return bob


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
