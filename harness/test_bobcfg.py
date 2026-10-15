#!/usr/bin/env python3
"""bobcfg: where the manifest is found, what it must hold, and where the paths it names land.

Two layouts must both work:

* core beside the Bobs (george/bob_core, george/tutor): no manifest above core, so a process
  started in the Bob's folder finds that folder's bob.toml;
* core inside a Bob (my_bob/core/harness): the manifest above core wins, wherever the process runs.

Run:  python3 harness/test_bobcfg.py
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import tomllib

HERE = pathlib.Path(__file__).resolve().parent
EXAMPLE = (HERE.parent / "bob.example.toml").read_text()
sys.path.insert(0, str(HERE))

import bobcfg  # noqa: E402


def _layout(manifests: dict[str, str], core: str = "bob_core") -> pathlib.Path:
    """A scratch root with <core>/harness/bobcfg.py and the given manifests (relative path -> text)."""
    root = pathlib.Path(tempfile.mkdtemp()).resolve()
    (root / core / "harness").mkdir(parents=True)
    shutil.copy2(HERE / "bobcfg.py", root / core / "harness" / "bobcfg.py")
    for rel, text in manifests.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _ask(root: pathlib.Path, cwd: pathlib.Path, code: str, core: str = "bob_core", **env_extra) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ("BOB_MANIFEST", "CLAUDE_CONFIG_DIR")}
    env["PYTHONPATH"] = str(root / core / "harness")
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", "import bobcfg; " + code],
                         cwd=cwd, env=env, capture_output=True, text=True)
    return (out.stdout + out.stderr).strip()


def _errors(text: str) -> list[str]:
    return bobcfg.validate(tomllib.loads(text))


def _strip(text: str, section: str) -> str:
    """`text` with one [section] and its sub-tables removed."""
    out, skip = [], False
    for line in text.splitlines():
        if line.startswith("["):
            skip = line.strip("[]").split(".")[0] == section
        if not skip:
            out.append(line)
    return "\n".join(out) + "\n"


# ---- finding the manifest ---------------------------------------------------------------------

def test_core_beside_bobs_finds_the_bob_from_its_folder():
    root = _layout({"tutor/bob.toml": EXAMPLE, "investor/bob.toml": EXAMPLE})
    got = _ask(root, root / "tutor", "print(bobcfg.manifest_path())")
    assert got == str(root / "tutor" / "bob.toml"), got


def test_core_beside_bobs_from_a_subfolder_of_the_bob():
    root = _layout({"tutor/bob.toml": EXAMPLE})
    (root / "tutor" / "loops").mkdir()
    got = _ask(root, root / "tutor" / "loops", "print(bobcfg.manifest_path())")
    assert got == str(root / "tutor" / "bob.toml"), got


def test_core_inside_a_bob_finds_the_manifest_above_core():
    root = _layout({"bob.toml": EXAMPLE}, core="core")
    elsewhere = pathlib.Path(tempfile.mkdtemp()).resolve()
    got = _ask(root, elsewhere, "print(bobcfg.manifest_path())", core="core")
    assert got == str(root / "bob.toml"), got


def test_bob_manifest_env_wins():
    root = _layout({"tutor/bob.toml": EXAMPLE, "other.toml": EXAMPLE})
    got = _ask(root, root / "tutor", "print(bobcfg.manifest_path())", BOB_MANIFEST=str(root / "other.toml"))
    assert got == str(root / "other.toml"), got


def test_no_manifest_anywhere_says_so():
    root = _layout({})
    got = _ask(root, root, "print(bobcfg.manifest_path())")
    assert "no bob.toml" in got, got


# ---- what the manifest must hold --------------------------------------------------------------

def test_example_is_valid():
    assert _errors(EXAMPLE) == []


def test_slack_runtime_routing_credentials_are_optional():
    minimal = EXAMPLE
    for section in ("slack", "routing", "runtime", "credentials"):
        minimal = _strip(minimal, section)
    assert "[slack" not in minimal and "[runtime" not in minimal, minimal
    assert _errors(minimal) == [], _errors(minimal)


def test_a_present_section_is_validated_in_full():
    errs = _errors(EXAMPLE.replace('user_id = "U00000000EX"\n', ""))
    assert any("slack.user_id: required when [slack] is present" in e for e in errs), errs


def test_required_core_keys_are_required():
    errs = _errors(EXAMPLE.replace('release_notes = "docs/RELEASE-NOTES.md"\n', ""))
    assert any("paths.release_notes: required" in e for e in errs), errs


def test_every_error_is_reported_at_once():
    broken = EXAMPLE.replace('purpose = "', 'nopurpose = "').replace("port = 8790", 'port = "x"')
    errs = _errors(broken)
    assert any("bob.purpose" in e for e in errs) and any("board.port" in e for e in errs), errs


def test_state_dir_is_optional():
    assert "state_dir" not in tomllib.loads(EXAMPLE)["paths"] and _errors(EXAMPLE) == []


def test_own_loop_needs_a_schedule_under_its_stem():
    text = EXAMPLE.replace("own = []", 'own = ["loops/morning-brief.txt"]', 1)
    assert any('"morning-brief" to [loops.schedules]' in e for e in _errors(text)), _errors(text)
    scheduled = text.replace("[loops.schedules]\n", '[loops.schedules]\n"morning-brief" = "0 8 * * *"\n', 1)
    assert _errors(scheduled) == [], _errors(scheduled)


def test_own_loop_must_be_a_txt_inside_the_bob():
    for bad in ('"../loops/x.txt"', '"loops/x.md"', '"/tmp/x.txt"', "'..\\loops\\x.txt'",
                "'C:\\loops\\x.txt'", "'\\loops\\x.txt'", "'C:loops\\x.txt'"):
        text = EXAMPLE.replace("own = []", f"own = [{bad}]", 1)
        assert any("loops.own" in e for e in _errors(text)), (bad, _errors(text))


def test_runtime_absolute_paths_accept_windows_and_posix_forms():
    for good in ("'/tmp/bob.log'", "'C:\\Temp\\bob.log'", "'C:/Temp/bob.log'"):
        text = EXAMPLE.replace('agent_log = "/tmp/bob-example-agent.log"', f"agent_log = {good}", 1)
        assert _errors(text) == [], (good, _errors(text))
    for bad in ("'tmp/bob.log'", "'C:Temp\\bob.log'", "'bob.log'"):
        text = EXAMPLE.replace('agent_log = "/tmp/bob-example-agent.log"', f"agent_log = {bad}", 1)
        assert any("runtime.agent_log" in e for e in _errors(text)), (bad, _errors(text))


def test_runtime_env_file_is_optional_and_must_be_absolute():
    for good in ("'/home/me/bob-secrets.env'", "'C:/Users/me/bob-secrets.env'"):
        text = EXAMPLE.replace("[runtime]\n", f"[runtime]\nenv_file = {good}\n", 1)
        assert _errors(text) == [], (good, _errors(text))
    for bad in ("'bob-secrets.env'", "'../bob-secrets.env'"):
        text = EXAMPLE.replace("[runtime]\n", f"[runtime]\nenv_file = {bad}\n", 1)
        assert any("runtime.env_file: an absolute path" in e for e in _errors(text)), (bad, _errors(text))


def test_audit_reports_an_own_loop_file_that_is_missing():
    text = EXAMPLE.replace("own = []", 'own = ["loops/morning-brief.txt"]', 1).replace(
        "[loops.schedules]\n", '[loops.schedules]\n"morning-brief" = "0 8 * * *"\n', 1)
    root = _layout({"tutor/bob.toml": text})
    shutil.copy2(HERE / "loop_passes.py", root / "bob_core" / "harness" / "loop_passes.py")
    config = root / "config"
    missing = _ask(root, root / "tutor", "import loop_passes; print(loop_passes.missing_loop_files())",
                   CLAUDE_CONFIG_DIR=str(config))
    assert missing == "['loops/morning-brief.txt']", missing
    (root / "tutor" / "loops").mkdir()
    (root / "tutor" / "loops" / "morning-brief.txt").write_text("brief\n")
    present = _ask(root, root / "tutor", "import loop_passes; print(loop_passes.missing_loop_files())",
                   CLAUDE_CONFIG_DIR=str(config))
    assert present == "[]", present


# ---- where paths land -------------------------------------------------------------------------

def test_data_paths_follow_data_root_and_own_files_do_not():
    text = EXAMPLE.replace("[paths]\n", '[paths]\ndata_root = "../data/example"\n', 1)
    root = _layout({"tutor/bob.toml": text})
    got = _ask(root, root / "tutor", "c = bobcfg.config(); "
               "print(bobcfg.path(c, 'evidence_dir')); print(bobcfg.path(c, 'tracker'))").splitlines()
    assert got == [str(root / "data" / "example" / "evidence"), str(root / "tutor" / "docs" / "TRACKER.md")], got


def test_without_data_root_everything_is_under_the_bob():
    root = _layout({"tutor/bob.toml": EXAMPLE})
    got = _ask(root, root / "tutor", "print(bobcfg.path(bobcfg.config(), 'evidence_dir'))")
    assert got == str(root / "tutor" / "evidence"), got


def test_state_home_follows_config_dir_and_state_subdir():
    root = _layout({"a/bob.toml": EXAMPLE,
                    "b/bob.toml": EXAMPLE.replace("[runtime]\n", '[runtime]\nstate_subdir = "bobs/b"\n', 1)})
    config = root / "config"
    a = _ask(root, root / "a", "print(bobcfg.state_home())", CLAUDE_CONFIG_DIR=str(config))
    b = _ask(root, root / "b", "print(bobcfg.state_home())", CLAUDE_CONFIG_DIR=str(config))
    assert a == str(config) and b == str(config / "bobs" / "b"), (a, b)


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
