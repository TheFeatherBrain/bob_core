#!/usr/bin/env python3
"""Every core module that keeps state beside the session keeps it under bobcfg.state_home().

Two Bobs that share one Claude config dir (and so its Claude and Slack logins) must not share a
Slack seen-list, DM cursors, thread ledger, loop-pass ledger, digest marker, heal log or reflect
state. Without `[runtime] state_subdir` every path must stay exactly where it was, so a Bob that
predates the key sees no change.

Claude Code's own transcripts (`<config dir>/projects`) are not Bob state and stay in the config dir.

Run:  python3 harness/test_state_home_wiring.py
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

#: module -> the module-level names that hold state paths.
STATE = {
    "slack_seen": ["SEEN", "CURSORS"],
    "slack_threads": ["STATE", "ROOTS"],
    "loop_passes": ["LEDGER"],
    "release_digest": ["STATE"],
    "watchdog_check": ["HEAL_LOG"],
    "reflect_gate": ["STATE"],
}
#: transcripts stay in the config dir whatever the manifest says.
TRANSCRIPTS = {"did_i": ["PROJECTS"]}

PROBE = """
import json, pathlib, sys
sys.path[:0] = [{harness!r}, {hooks!r}]
out = {{}}
for mod, names in json.loads({spec!r}).items():
    m = __import__(mod)
    for n in names:
        v = getattr(m, n)
        vals = list(v.values()) if isinstance(v, dict) else (list(v) if isinstance(v, (list, tuple)) else [v])
        out[mod + "." + n] = [str(x) for x in vals]
print(json.dumps(out))
"""


def _paths(manifest_text: str, spec: dict) -> tuple[pathlib.Path, dict]:
    tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
    manifest = tmp / "bob.toml"
    manifest.write_text(manifest_text)
    config = tmp / "config"
    env = {k: v for k, v in os.environ.items() if k not in ("REFLECT_STATE",)}
    env.update(BOB_MANIFEST=str(manifest), CLAUDE_CONFIG_DIR=str(config))
    code = PROBE.format(harness=str(HERE), hooks=str(HERE / "hooks"), spec=json.dumps(spec))
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=tmp)
    assert out.returncode == 0, out.stderr
    return config, json.loads(out.stdout)


def _under(paths: dict, root: pathlib.Path) -> list[str]:
    """Names whose paths are not directly under root (root/<file>, not deeper)."""
    return [name for name, values in paths.items()
            for v in values if pathlib.Path(v).parent != root]


def test_state_paths_follow_state_subdir():
    text = EXAMPLE.replace("[runtime]\n", '[runtime]\nstate_subdir = "bobs/example"\n', 1)
    config, paths = _paths(text, STATE)
    assert not _under(paths, config / "bobs" / "example"), paths


def test_state_paths_unchanged_without_state_subdir():
    config, paths = _paths(EXAMPLE, STATE)
    assert not _under(paths, config), paths


def test_transcripts_stay_in_the_config_dir():
    text = EXAMPLE.replace("[runtime]\n", '[runtime]\nstate_subdir = "bobs/example"\n', 1)
    config, paths = _paths(text, TRANSCRIPTS)
    assert paths["did_i.PROJECTS"] == [str(config / "projects")], paths


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
