#!/usr/bin/env python3
"""new_bob.py refuses every malformed manifest and turns a good one into a working Bob.

Each rejection case breaks the example manifest in exactly one way and asserts the message for that
rule. The scaffold cases write Bobs into throwaway directories next to a throwaway fleet file, then
drive the result: the manifest resolves from the Bob's folder, every core path a rendered file names
exists, nothing is left unrendered, the board answers on its port.

Run:  python3 automation/test_new_bob.py
"""
from __future__ import annotations

import json
import os
import pathlib
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CORE = HERE.parent
EXAMPLE = CORE / "bob.example.toml"
sys.path.insert(0, str(HERE))

import new_bob  # noqa: E402

#: Without BOB_MANIFEST, so a scaffolded Bob reads its own bob.toml, never core's example.
ENV = {k: v for k, v in os.environ.items() if k != "BOB_MANIFEST"}
FLEET = {"bob": [{"slug": "other", "name": "Other Bob", "port": 8787}]}
SCRATCH: list[pathlib.Path] = []

MINIMAL = """\
[bob]
slug = "minimal"
name = "Minimal Bob"
purpose = "A Bob with no Slack, no supervisor and no loops"
owner = "George"

[board]
port = 8791
title = "Minimal Bob"
panels = []

[loops]
templates = []
own = []

[loops.schedules]

[paths]
evidence_dir = "evidence"
state_dir = "state"
tracker = "docs/TRACKER.md"
release_notes = "docs/RELEASE-NOTES.md"
"""


def tmpdir() -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp(prefix="new-bob-")).resolve()
    SCRATCH.append(d)
    return d


def example() -> dict:
    return tomllib.loads(EXAMPLE.read_text())


def rejected(mutate, needle: str) -> None:
    cfg = example()
    assert not new_bob.validate(cfg, FLEET), f"the example must be valid: {new_bob.validate(cfg, FLEET)}"
    mutate(cfg)
    errs = new_bob.validate(cfg, FLEET)
    assert any(needle in e for e in errs), f"no error containing {needle!r}: {errs}"


# ---- rejections -------------------------------------------------------------------------------

def test_missing_purpose_is_rejected():
    rejected(lambda c: c["bob"].pop("purpose"), "bob.purpose: required")


def test_bad_slug_is_rejected():
    rejected(lambda c: c["bob"].update(slug="Sample!"), "bob.slug")


def test_slug_already_in_fleet_is_rejected():
    rejected(lambda c: c["bob"].update(slug="other"), "already in the fleet")


def test_port_already_in_fleet_is_rejected():
    rejected(lambda c: c["board"].update(port=8787), "board.port: 8787 is used by 'other'")


def test_credential_value_is_rejected():
    rejected(lambda c: c["bob"].update(owner="xoxb-1234567890-abcdefgh"), "looks like a credential value")


def test_credential_table_holding_a_value_is_rejected():
    rejected(lambda c: c["credentials"].update(API={"access": "read-only", "value": "x"}), "never values")


def test_read_write_without_why_is_rejected():
    rejected(lambda c: c["credentials"].update(DB={"access": "read-write"}), "credentials.DB.why")


def test_unknown_loop_template_is_rejected():
    rejected(lambda c: c["loops"]["templates"].append("nope"), "no template named 'nope'")


def test_template_without_schedule_is_rejected():
    rejected(lambda c: c["loops"]["schedules"].pop("health-checks"), "has no schedule")


def test_slack_template_without_slack_is_rejected():
    rejected(lambda c: c.pop("slack"), "'slack-watch' needs slack")


def test_health_checks_without_runtime_is_rejected():
    rejected(lambda c: c.pop("runtime"), "'health-checks' needs runtime")


def test_partial_slack_section_is_rejected():
    rejected(lambda c: c["slack"].pop("authority"), "slack.authority: required when [slack] is present")


def test_bad_cron_is_rejected():
    rejected(lambda c: c["loops"]["schedules"].update({"health-checks": "every hour"}), "five-field cron")


def test_path_escaping_the_bob_is_rejected():
    rejected(lambda c: c["paths"].update(tracker="../elsewhere.md"), "paths.tracker")


# ---- scaffolds --------------------------------------------------------------------------------

def scaffold(manifest_text: str) -> tuple[pathlib.Path, pathlib.Path, subprocess.CompletedProcess]:
    """(bob folder, fleet file, the run). The Bob is written two levels below a scratch root."""
    root = tmpdir()
    manifest = root / "in.toml"
    manifest.write_text(manifest_text)
    fleet = root / "fleet.toml"
    fleet.write_text("# test fleet\n")
    out = root / "bobs" / "new"
    run = subprocess.run([sys.executable, str(HERE / "new_bob.py"), str(manifest), "--out", str(out),
                          "--fleet", str(fleet)], env=ENV, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    return out, fleet, run


def core_paths_named(text: str, out: pathlib.Path) -> list[pathlib.Path]:
    """Every `<core>/harness/...py` or `<core>/CORE.md` path a rendered file names."""
    rel = os.path.relpath(CORE, out).replace("\\", "/")
    pattern = re.escape(rel) + r"/(?:harness/[\w/]+\.py|CORE\.md)"
    return [out / m for m in re.findall(pattern, text)]


def test_minimal_manifest_scaffolds_without_slack_or_runtime():
    out, fleet, _run = scaffold(MINIMAL)
    settings = json.loads((out / ".claude/settings.json").read_text())
    matchers = [h["matcher"] for h in settings["hooks"]["PreToolUse"]]
    assert matchers == ["Bash", "*"], matchers
    assert not (out / "supervisor").exists()
    assert not (out / "loops").exists()
    claude = (out / "CLAUDE.md").read_text()
    assert "Directed by George" in claude and "talks in its terminal" in claude, claude
    assert 'slug = "minimal"' in fleet.read_text()
    entry = tomllib.loads(fleet.read_text())["bob"][0]
    assert entry["path"] == str(out), entry


def test_bob_without_a_supervisor_keeps_the_background_worktree_guard():
    out, _fleet, _run = scaffold(MINIMAL)
    assert "worktree" not in json.loads((out / ".claude/settings.json").read_text())


def test_supervised_bob_turns_off_the_background_worktree_guard():
    """A resumed Bob runs in the background, where Claude Code sends every edit to a new worktree:
    its tracker, theses and data would split from the live folder, so the edits are refused."""
    out, _fleet, _run = scaffold(EXAMPLE.read_text())
    settings = json.loads((out / ".claude/settings.json").read_text())
    assert settings.get("worktree") == {"bgIsolation": "none"}, settings


def test_windows_install_hint_sets_the_laptop_task_settings():
    """schtasks alone leaves the task on Task Scheduler's defaults: no runs on battery."""
    hint = new_bob.INSTALL[True].format(label="com.x.bob-y", out="C:\\bobs\\y",
                                        power=new_bob.oscompat.task_settings_command("com.x.bob-y"))
    assert "schtasks /Create /TN com.x.bob-y" in hint, hint
    assert "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable" in hint, hint
    assert "Set-ScheduledTask -TaskName 'com.x.bob-y'" in hint, hint


def test_fleet_entry_survives_quotes_and_backslashes():
    fleet = tmpdir() / "fleet.toml"
    fleet.write_text("# test fleet\n")
    cfg = tomllib.loads(MINIMAL.replace('name = "Minimal Bob"', 'name = "The \\"Minimal\\" Bob"'))
    new_bob.add_to_fleet(fleet, cfg, pathlib.Path("C:\\Users\\someone\\bobs\\minimal"))
    entry = tomllib.loads(fleet.read_text())["bob"][0]
    assert entry["name"] == 'The "Minimal" Bob' and entry["path"].endswith("minimal"), entry


def test_full_example_scaffolds_every_piece():
    out, _fleet, _run = scaffold(EXAMPLE.read_text())
    for rel in ("bob.toml", "CLAUDE.md", "launch.sh", ".claude/settings.json", "docs/TRACKER.md",
                "docs/RELEASE-NOTES.md", "skills/README.md", "skills/INDEX.md", "loops/slack-watch.txt",
                "loops/health-checks.txt", "loops/release-digest.txt", "loops/lessons-review.txt",
                "supervisor/wake-prompt.md", "supervisor/sessions.json"):
        assert (out / rel).is_file(), rel
    assert os.access(out / "launch.sh", os.X_OK)
    assert b"\r" not in (out / "launch.sh").read_bytes(), "launch.sh has CRLF line endings"
    assert json.loads((out / "supervisor/sessions.json").read_text()) == {"sessions": []}
    if os.name == "nt":
        assert_tick_cmd(out)
    else:
        assert_plist(out)


def assert_plist(out: pathlib.Path) -> None:
    plist = plistlib.loads((out / "supervisor/com.example.bob-example.plist").read_bytes())
    assert plist["Label"] == "com.example.bob-example"
    assert pathlib.Path(plist["ProgramArguments"][1]).is_file(), plist["ProgramArguments"]
    assert plist["EnvironmentVariables"]["BOB_MANIFEST"] == f"{out}/bob.toml"


def assert_tick_cmd(out: pathlib.Path) -> None:
    raw = (out / "supervisor/tick.cmd").read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n"), "tick.cmd is not CRLF throughout"
    text = raw.decode()
    assert f'set "BOB_MANIFEST={out}\\bob.toml"' in text, text
    script = re.search(r'"([^"]+session_supervisor\.py)"', text)
    assert script and pathlib.Path(script.group(1)).is_file(), text
    assert not (out / "supervisor/com.example.bob-example.plist").exists()


def test_nothing_is_left_unrendered_and_every_core_path_exists():
    out, _fleet, _run = scaffold(EXAMPLE.read_text())
    named = []
    for path in out.rglob("*"):
        if not path.is_file() or path.name in ("bob.toml", "sessions.json"):
            continue
        text = path.read_text()
        left = new_bob.PLACEHOLDER.findall(text)
        assert not left, f"{path.relative_to(out)} keeps placeholders {left}"
        named += core_paths_named(text, out)
    assert named, "no rendered file names a core path"
    missing = [str(p) for p in named if not p.resolve().is_file()]
    assert not missing, missing


def test_claude_md_imports_core_by_relative_path():
    out, _fleet, _run = scaffold(EXAMPLE.read_text())
    first = [line for line in (out / "CLAUDE.md").read_text().splitlines() if line.startswith("@")]
    assert first and (out / first[0][1:]).resolve() == (CORE / "CORE.md").resolve(), first


def test_lessons_index_is_imported_current_and_the_skill_gate_registered():
    out, _fleet, _run = scaffold(EXAMPLE.read_text())
    imports = [line for line in (out / "CLAUDE.md").read_text().splitlines() if line.startswith("@")]
    assert "@skills/INDEX.md" in imports and (out / "skills/INDEX.md").is_file(), imports
    env = {**ENV, "CLAUDE_CONFIG_DIR": str(out / ".config")}
    check = subprocess.run([sys.executable, f"{os.path.relpath(CORE, out)}/harness/skill_index.py",
                            "--check"], cwd=out, env=env, capture_output=True, text=True, timeout=60)
    assert check.returncode == 0, check.stdout + check.stderr
    pre = json.loads((out / ".claude/settings.json").read_text())["hooks"]["PreToolUse"]
    gate = [h for h in pre if h["hooks"][0]["command"].endswith("skill_gate.py")]
    assert gate and gate[0]["matcher"] == "*", pre


def test_existing_folder_is_refused():
    out, fleet, _run = scaffold(MINIMAL)
    manifest = out.parent.parent / "in.toml"
    again = subprocess.run([sys.executable, str(HERE / "new_bob.py"), str(manifest), "--out", str(out),
                            "--fleet", str(fleet)], env=ENV, capture_output=True, text=True)
    assert again.returncode == 1 and "not empty" in again.stdout, again.stdout


def test_scaffolded_bob_resolves_its_own_manifest_and_runs_core():
    out, _fleet, _run = scaffold(EXAMPLE.read_text())
    rel = os.path.relpath(CORE, out)
    env = {**ENV, "CLAUDE_CONFIG_DIR": str(out / ".config")}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, f"{rel}/harness/{args[0]}", *args[1:]], cwd=out,
                              env=env, capture_output=True, text=True, timeout=60)

    ok = run("bobcfg.py")
    assert ok.returncode == 0 and str(out / "bob.toml") in ok.stdout, ok.stdout + ok.stderr
    assert run("release_digest.py", "--count").stdout.strip() == "1"
    assert run("loop_passes.py", "--record", "health-checks").returncode == 0
    assert (out / ".config" / "loop-passes.jsonl").is_file()


def git_bash() -> str:
    """Git Bash on Windows (PATH may find WSL's bash first), plain bash elsewhere."""
    if os.name != "nt":
        return "bash"
    git = pathlib.Path(shutil.which("git")).resolve()
    return str(next(d / "bin" / "bash.exe" for d in git.parents if (d / "bin" / "bash.exe").is_file()))


def launch_with_env_file(env_lines: str, runtime_extra: str = "") -> subprocess.CompletedProcess:
    """Scaffold the example with an env_file, then run its launch.sh with a fake `claude` that
    prints what it was given, and a PRESET variable already in the environment."""
    secrets = tmpdir() / "secrets.env"
    secrets.write_bytes(env_lines.encode("utf-8"))
    line = f'env_file = "{secrets.as_posix()}"\n{runtime_extra}'
    out, _fleet, _run = scaffold(EXAMPLE.read_text().replace("[runtime]\n", f"[runtime]\n{line}", 1))
    fake = tmpdir()
    (fake / "claude").write_text('#!/usr/bin/env bash\nprintf "%s|%s|%s\\n" "$ALPHA" "$BETA" "$PRESET"\n',
                                 newline="\n")
    (fake / "claude").chmod(0o755)
    env = {**ENV, "PATH": f"{fake}{os.pathsep}{ENV['PATH']}", "PRESET": "from-shell"}
    env.pop("ALPHA", None)
    env.pop("BETA", None)
    return subprocess.run([git_bash(), (out / "launch.sh").as_posix()], env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


def test_launch_loads_the_env_file_and_a_set_variable_wins():
    lines = "# keys\r\nALPHA=a b$c=d\r\n\r\nBETA=plain\r\nPRESET=from-file\r\n"
    for extra in ("", 'state_subdir = "bobs/new"\n'):
        run = launch_with_env_file(lines, extra)
        assert run.returncode == 0, run.stdout + run.stderr
        assert run.stdout.strip() == "a b$c=d|plain|from-shell", (extra, run.stdout, run.stderr)


def test_launch_refuses_a_bad_env_file_before_starting_claude():
    run = launch_with_env_file('ALPHA="quoted-secret"\n')
    assert run.returncode != 0 and run.stdout == "", run.stdout + run.stderr
    assert "ALPHA is quoted" in run.stderr and "quoted-secret" not in run.stderr, run.stderr


def test_scaffolded_board_answers_on_its_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    out, _fleet, _run = scaffold(MINIMAL.replace("port = 8791", f"port = {port}"))
    env = {**ENV, "CLAUDE_CONFIG_DIR": str(out / ".config")}
    board = subprocess.Popen([sys.executable, f"{os.path.relpath(CORE, out)}/harness/board_shell.py",
                              "--quiet"], cwd=out, env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True)
    try:
        health = state = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                    health = json.loads(r.read())
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as r:
                    state = json.loads(r.read())
                break
            except OSError:
                time.sleep(0.1)
        assert health and health["ok"], health
        assert state["title"] == "Minimal Bob" and state["release_notes"] == ["Scaffold"], state
    finally:
        board.terminate()
        board.wait(timeout=10)


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001 - a crash is a failure, reported by name
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    finally:
        for d in SCRATCH:
            shutil.rmtree(d, ignore_errors=True)
    print(f"{len(tests) - failed} of {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
