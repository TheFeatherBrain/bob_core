#!/usr/bin/env python3
"""Load a Bob's secrets from a file outside every repo, named by `[runtime] env_file`.

Keys set with `setx` or in a shell profile work, but some people keep them in one file. A file
inside a Bob's folder or core is one `git add .` from being pushed, and one `grep -r` from landing
in the model's context or a saved evidence read, so this refuses any file under the Bob, under
core, or under any git work tree.

The format is strict on purpose, so bash (launch.sh), Python (the supervisor) and a person all read
the same thing: `KEY=value` per line, `#` comments and empty lines allowed, no quotes, no `export`,
no leading spaces, no empty values, no repeated keys. A malformed file is refused whole rather than
loaded in part. Errors name the line and the key, never the value.

A variable already in the environment wins over the file, the usual .env rule: an explicit
`KEY=x ./launch.sh` must not be overridden by a stale file.

Usage:
    python3 harness/envfile.py --check   # OK <file> keys=NAME,... (names only), or FAIL: ...
    python3 harness/envfile.py --path    # the file's path for launch.sh; nothing when unset
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bobcfg  # noqa: E402
import oscompat  # noqa: E402

CORE = HERE.parent
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
QUOTES = ('"', "'")


class EnvFileError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse(text: str) -> dict[str, str]:
    """KEY -> value for every line; raises EnvFileError listing every bad line at once."""
    values: dict[str, str] = {}
    errors: list[str] = []
    if text.startswith("﻿"):
        errors.append("line 1: starts with a byte-order mark; save the file as UTF-8 without BOM")
        text = text[1:]
    for number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        where = f"line {number}"
        if not sep:
            errors.append(f"{where}: not KEY=value")
        elif not NAME.match(key):
            errors.append(f"{where}: {key[:40]!r} is not a variable name "
                          "(letters, digits and _; no spaces, no `export`)")
        elif not value:
            errors.append(f"{where}: {key} has no value")
        elif value[0] in QUOTES or value[-1] in QUOTES:
            errors.append(f"{where}: {key} is quoted; write the value without quotes")
        elif value != value.strip():
            errors.append(f"{where}: {key} has spaces around its value")
        elif key in values:
            errors.append(f"{where}: {key} is set twice")
        else:
            values[key] = value
    if errors:
        raise EnvFileError(errors)
    return values


def _inside(path: pathlib.Path, folder: pathlib.Path) -> bool:
    return path == folder or folder in path.parents


def refusal(path: pathlib.Path, bob: pathlib.Path) -> str | None:
    """Why `path` must not hold secrets, or None when it may."""
    for name, folder in (("the Bob's folder", bob), ("core", CORE)):
        if _inside(path, folder.resolve()):
            return f"{path} is inside {name}; keep secrets outside every repo"
    repo = next((d for d in path.parents if (d / ".git").exists()), None)
    return f"{path} is inside the git work tree {repo}" if repo else None


def configured(cfg: dict) -> pathlib.Path | None:
    value = (cfg.get("runtime") or {}).get("env_file")
    return pathlib.Path(value).resolve() if value else None


def read(cfg: dict, bob: pathlib.Path) -> tuple[pathlib.Path | None, dict[str, str]]:
    """(file, values) for this Bob; (None, {}) when no env_file is configured."""
    path = configured(cfg)
    if path is None:
        return None, {}
    why = refusal(path, bob)
    if why:
        raise EnvFileError([why])
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise EnvFileError([f"{path}: no such file (named by [runtime] env_file)"]) from None
    try:
        return path, parse(text)
    except EnvFileError as e:
        raise EnvFileError([f"{path}: {err}" for err in e.errors]) from None


def apply(values: dict[str, str], environ) -> list[str]:
    """Set each variable the environment does not already have; returns the names set."""
    added = [k for k in values if k not in environ]
    for k in added:
        environ[k] = values[k]
    return added


def load_into(environ) -> list[str]:
    """Read this Bob's env_file and apply it; the names set. Raises EnvFileError."""
    _path, values = read(bobcfg.config(), bobcfg.bob_root())
    return apply(values, environ)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--path", action="store_true")
    args = ap.parse_args(argv)
    try:
        path, values = read(bobcfg.config(), bobcfg.bob_root())
    except (EnvFileError, bobcfg.ManifestError) as e:
        for err in e.errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1
    if args.path:
        if path:
            print(oscompat.posix(path))
        return 0
    print(f"OK {path} keys={','.join(values)}" if path else "OK no [runtime] env_file configured")
    return 0


if __name__ == "__main__":
    oscompat.utf8_stdio()
    raise SystemExit(main(sys.argv[1:]))
