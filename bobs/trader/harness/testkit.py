"""Shared test helpers: a throwaway Trader beside a minimal Scout, and core's plain-assert runner.

Every test runs against a temp copy of this Bob's real manifest, placed at <tmp>/trader with a
minimal Scout manifest at <tmp>/investor, so scout_manifest = "../investor/bob.toml" resolves the
same way it does in the repo and no test can write into either real data folder.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import traderpath  # noqa: E402,F401
import bobcfg  # noqa: E402

MANIFEST = traderpath.BOB / "bob.toml"
SCOUT = '[paths]\ndata_root = "data"\nstate_dir = "state"\n\n[scout]\nbuy_percentile = 80\n'


def temp_bob(replace: dict[str, str] | None = None) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp()).resolve()
    bob, scout = root / "trader", root / "investor"
    bob.mkdir()
    scout.mkdir()
    text = MANIFEST.read_text(encoding="utf-8")
    for old, new in (replace or {}).items():
        assert old in text, f"{old!r} is not in the manifest"
        text = text.replace(old, new)
    (bob / "bob.toml").write_text(text, encoding="utf-8")
    (scout / "bob.toml").write_text(SCOUT, encoding="utf-8")
    os.environ["BOB_MANIFEST"] = str(bob / "bob.toml")
    os.environ["CLAUDE_CONFIG_DIR"] = str(bob / "config")
    bobcfg.config.cache_clear()
    return bob


def scout_state(bob: pathlib.Path) -> pathlib.Path:
    return bob.parent / "investor" / "data" / "state"


def put(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")


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
