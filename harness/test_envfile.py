#!/usr/bin/env python3
"""envfile: a strict KEY=value file outside every repo, never a value in an error or on stdout.

Every rule is a refuse/allow pair. The CLI and the supervisor are driven in a subprocess against a
throwaway Bob, so the real manifest and the real secrets file are never read.

Run:  python3 harness/test_envfile.py
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
sys.path.insert(0, str(HERE))

import envfile  # noqa: E402
import oscompat  # noqa: E402

SECRET = "s3cr3t-VALUE-9f2c"
SCRATCH: list[pathlib.Path] = []


def tmpdir() -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp(prefix="envfile-")).resolve()
    SCRATCH.append(d)
    return d


def errors_of(text: str) -> list[str]:
    try:
        envfile.parse(text)
    except envfile.EnvFileError as e:
        return e.errors
    return []


def refused(text: str, needle: str) -> None:
    errs = errors_of(text)
    assert any(needle in e for e in errs), f"no error containing {needle!r}: {errs}"
    assert not any(SECRET in e for e in errs), f"an error shows the value: {errs}"


def bob_with_env_file(env_line: str | None) -> pathlib.Path:
    """A throwaway Bob folder: bob.toml from the example, an empty sessions file, a wake prompt."""
    bob = tmpdir() / "bob"
    (bob / "supervisor").mkdir(parents=True)
    (bob / "supervisor" / "sessions.json").write_text('{"sessions": []}', encoding="utf-8")
    (bob / "supervisor" / "wake-prompt.md").write_text("wake\n", encoding="utf-8")
    manifest = EXAMPLE if env_line is None else EXAMPLE.replace("[runtime]\n", f"[runtime]\n{env_line}\n", 1)
    (bob / "bob.toml").write_text(manifest, encoding="utf-8")
    return bob


def run(bob: pathlib.Path, code: str, **extra) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("ALPHA", "BETA")}
    env.update(BOB_MANIFEST=str(bob / "bob.toml"), CLAUDE_CONFIG_DIR=str(bob / ".config"),
               PYTHONPATH=str(HERE), **extra)
    return subprocess.run([sys.executable, "-c", code], cwd=bob, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


def cli(bob: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return run(bob, f"import sys, envfile; raise SystemExit(envfile.main({list(args)!r}))")


# ---- parse ------------------------------------------------------------------------------------

def test_plain_lines_comments_blanks_and_crlf_are_read():
    text = f"# comment\r\n\r\nALPHA={SECRET}\r\nBETA=a b$c=d\r\n"
    assert envfile.parse(text) == {"ALPHA": SECRET, "BETA": "a b$c=d"}


def test_a_line_without_equals_is_refused():
    refused("ALPHA\n", "line 1: not KEY=value")


def test_export_or_leading_space_is_refused():
    refused(f"export ALPHA={SECRET}\n", "is not a variable name")
    refused(f" ALPHA={SECRET}\n", "is not a variable name")


def test_an_empty_value_is_refused():
    refused("ALPHA=\n", "ALPHA has no value")


def test_a_quoted_value_is_refused():
    refused(f'ALPHA="{SECRET}"\n', "ALPHA is quoted")
    refused(f"ALPHA='{SECRET}'\n", "ALPHA is quoted")


def test_spaces_around_a_value_are_refused():
    refused(f"ALPHA= {SECRET}\n", "ALPHA has spaces around its value")


def test_a_repeated_key_is_refused():
    refused(f"ALPHA={SECRET}\nALPHA=other\n", "line 2: ALPHA is set twice")


def test_a_byte_order_mark_is_refused():
    refused(f"﻿ALPHA={SECRET}\n", "byte-order mark")


def test_every_bad_line_is_reported_at_once():
    assert len(errors_of("A\nB=\nC='x'\n")) == 3


# ---- location ---------------------------------------------------------------------------------

def test_a_file_inside_the_bob_or_core_is_refused():
    bob = tmpdir()
    assert "inside the Bob's folder" in envfile.refusal(bob / "secrets.env", bob)
    assert "inside core" in envfile.refusal(envfile.CORE / "secrets.env", bob)


def test_a_file_inside_any_git_work_tree_is_refused():
    repo = tmpdir()
    (repo / ".git").mkdir()
    (repo / "sub").mkdir()
    assert "git work tree" in envfile.refusal(repo / "sub" / "secrets.env", tmpdir())


def test_a_file_outside_every_repo_is_allowed():
    assert envfile.refusal(tmpdir() / "secrets.env", tmpdir()) is None


def test_a_missing_file_is_refused_and_no_env_file_reads_nothing():
    bob = tmpdir()
    try:
        envfile.read({"runtime": {"env_file": str(tmpdir() / "absent.env")}}, bob)
        raise AssertionError("a missing file was accepted")
    except envfile.EnvFileError as e:
        assert "no such file" in e.errors[0], e.errors
    assert envfile.read({"runtime": {}}, bob) == (None, {})
    assert envfile.read({}, bob) == (None, {})


def test_a_variable_already_set_wins_over_the_file():
    environ = {"ALPHA": "from-shell"}
    assert envfile.apply({"ALPHA": SECRET, "BETA": "b"}, environ) == ["BETA"]
    assert environ == {"ALPHA": "from-shell", "BETA": "b"}


# ---- CLI --------------------------------------------------------------------------------------

def test_cli_path_and_check_print_the_file_and_names_never_values():
    secrets = tmpdir() / "secrets.env"
    secrets.write_text(f"ALPHA={SECRET}\nBETA=b\n", encoding="utf-8")
    bob = bob_with_env_file(f'env_file = "{oscompat.posix(secrets)}"')
    path = cli(bob, "--path")
    assert path.returncode == 0 and path.stdout.strip() == oscompat.posix(secrets), path
    check = cli(bob, "--check")
    assert check.returncode == 0 and "keys=ALPHA,BETA" in check.stdout, check
    assert SECRET not in path.stdout + path.stderr + check.stdout + check.stderr


def test_cli_without_env_file_prints_no_path():
    out = cli(bob_with_env_file(None), "--path")
    assert out.returncode == 0 and out.stdout == "", out


def test_cli_refuses_a_bad_file_on_stderr_with_nothing_on_stdout():
    secrets = tmpdir() / "secrets.env"
    secrets.write_text(f'ALPHA="{SECRET}"\n', encoding="utf-8")
    out = cli(bob_with_env_file(f'env_file = "{oscompat.posix(secrets)}"'), "--path")
    assert out.returncode == 1 and out.stdout == "" and "FAIL:" in out.stderr, out
    assert SECRET not in out.stderr, out.stderr


# ---- supervisor -------------------------------------------------------------------------------

SUPERVISE = """\
import os, session_supervisor as s
seen = []
s.live_sessions = lambda: seen.append(os.environ.get("ALPHA")) or {}
s.main(["--dry-run"])
print("ALPHA=" + str(seen[0]))
"""


def test_supervisor_resumes_with_the_env_file_loaded():
    secrets = tmpdir() / "secrets.env"
    secrets.write_text(f"ALPHA={SECRET}\n", encoding="utf-8")
    out = run(bob_with_env_file(f'env_file = "{oscompat.posix(secrets)}"'), SUPERVISE)
    assert f"ALPHA={SECRET}" in out.stdout and "FAILED" not in out.stdout, out.stdout + out.stderr


def test_supervisor_with_a_missing_env_file_warns_and_still_ticks():
    missing = oscompat.posix(tmpdir() / "absent.env")
    out = run(bob_with_env_file(f'env_file = "{missing}"'), SUPERVISE)
    assert "WARN env_file:" in out.stdout and "ALPHA=None" in out.stdout, out.stdout + out.stderr


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
