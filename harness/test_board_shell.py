#!/usr/bin/env python3
"""board_shell.py hands the board to the panel module the manifest names.

A panel lives in the Bob's own harness/, not in core. If the shell looks only beside itself, a Bob
with a panel cannot be started through core at all: `board_shell.py` dies with ModuleNotFoundError
and only the panel's own script works, so nothing generic (the supervisor) can start the board.

Run:  python3 harness/test_board_shell.py
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
EXAMPLE = (HERE.parent / "bob.example.toml").read_text(encoding="utf-8")
PANEL = "import sys\n\ndef main(argv):\n    print('panel main', sys.argv[1:] if argv is None else argv)\n    return 7\n"


def _bob(panels: str) -> pathlib.Path:
    """A Bob folder beside a copy of core, with the example manifest naming `panels`."""
    root = pathlib.Path(tempfile.mkdtemp()).resolve()
    shutil.copytree(HERE, root / "bob_core" / "harness",
                    ignore=shutil.ignore_patterns("__pycache__", "test_*"))
    bob = root / "tutor"
    (bob / "harness").mkdir(parents=True)
    (bob / "bob.toml").write_text(EXAMPLE.replace("panels = []", f"panels = {panels}", 1), encoding="utf-8")
    return bob


def _run(bob: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "BOB_MANIFEST"}
    env.update(BOB_MANIFEST=str(bob / "bob.toml"), CLAUDE_CONFIG_DIR=str(bob / "config"))
    return subprocess.run([sys.executable, str(bob.parent / "bob_core" / "harness" / "board_shell.py"), *args],
                          cwd=bob, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_a_panel_in_the_bobs_own_harness_is_found():
    bob = _bob('["tutor_panel"]')
    (bob / "harness" / "tutor_panel.py").write_text(PANEL, encoding="utf-8")
    out = _run(bob, "--quiet")
    assert out.returncode == 7 and "panel main ['--quiet']" in out.stdout, (out.returncode, out.stdout, out.stderr)


def test_a_panel_that_exists_nowhere_still_fails():
    out = _run(_bob('["no_such_panel"]'))
    assert out.returncode != 0 and "no_such_panel" in out.stderr, (out.returncode, out.stderr)


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
