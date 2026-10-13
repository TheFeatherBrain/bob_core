"""Read a Bob's manifest, bob.toml, and refuse one that is incomplete or malformed.

Every value that differs between Bobs lives in the manifest. Core code reads it through `get`,
which raises on a missing key rather than defaulting: a default here would be one Bob's value
leaking into another.

Some sections are optional and only needed by the modules that use them:

    [slack]        the Slack modules and the slack-watch loop
    [routing]      route.py, when several Bobs share one Slack account
    [runtime]      the session supervisor and its watchdog
    [credentials]  names and access levels of what the Bob reads; never values

A section that is present is validated in full. All errors are collected and raised together as
ManifestError, so a manifest is fixed in one pass.

The manifest is found at $BOB_MANIFEST, else the nearest bob.toml at or above this file's
directory, else the nearest at or above the working directory (see manifest_path).

Usage:
    python3 harness/bobcfg.py            # validate the manifest, print OK or the errors
    python3 harness/bobcfg.py board.port # print one value
"""
from __future__ import annotations

import functools
import os
import pathlib
import re
import sys
import tomllib

SLACK_USER = re.compile(r"^[UW][A-Z0-9]{8,}$")
SLACK_CHANNEL = re.compile(r"^[CDG][A-Z0-9]{8,}$")
LAUNCHD_LABEL = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+){2,}$")
MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
CHANNEL_MODES = {"all", "mentions"}
ACCESS = {"read-only", "read-write"}
CREDENTIAL_KEYS = {"access", "why"}

#: Required in every manifest.
REQUIRED = [
    "bob.slug", "bob.name", "bob.purpose",
    "board.port", "board.title", "board.panels",
    "loops.schedules",
    "paths.evidence_dir", "paths.tracker", "paths.release_notes",
]

#: Required inside an optional section once that section is present.
SECTION_REQUIRED = {
    "slack": ["slack.user_id", "slack.authority", "slack.channels"],
    "runtime": ["runtime.launchd_label", "runtime.agent_log", "runtime.supervisor_tmp_prefix",
                "runtime.sessions_file", "runtime.wake_prompt"],
}


class ManifestError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def manifest_path() -> pathlib.Path:
    """$BOB_MANIFEST, else the nearest bob.toml at or above this file, else at or above the cwd.

    Above this file first, so a Bob that holds core inside its own folder resolves to its own
    manifest. When core sits beside the Bobs, no manifest is above it, and the Bob is whichever
    folder the process runs in.
    """
    env = os.environ.get("BOB_MANIFEST")
    if env:
        return pathlib.Path(env)
    here = pathlib.Path(__file__).resolve().parent
    cwd = pathlib.Path.cwd().resolve()
    for start in (here, cwd):
        for directory in (start, *start.parents):
            candidate = directory / "bob.toml"
            if candidate.is_file():
                return candidate
    raise ManifestError([f"no bob.toml in {here}, {cwd} or any directory above them, "
                         "and BOB_MANIFEST unset"])


def claude_home() -> pathlib.Path:
    """This Bob's Claude Code config directory: $CLAUDE_CONFIG_DIR, else ~/.claude."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return pathlib.Path(env) if env else pathlib.Path.home() / ".claude"


def state_home() -> pathlib.Path:
    """Where this Bob's state files live: claude_home(), or a subdirectory of it named by
    `[runtime] state_subdir` when several Bobs share one config dir and its logins."""
    sub = (config().get("runtime") or {}).get("state_subdir")
    return claude_home() / sub if sub else claude_home()


@functools.cache
def config() -> dict:
    """This Bob's manifest, loaded and validated once per process."""
    return load()


def get(cfg: dict, dotted: str):
    """Return the value at a dotted path. Raises KeyError when any segment is absent."""
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"{dotted}: not in manifest")
        node = node[part]
    return node


def bob_root() -> pathlib.Path:
    """The Bob's own folder: the directory holding its manifest, not this module's."""
    return manifest_path().resolve().parent


#: [paths] entries that are data, not the Bob's own files. Only these follow data_root.
#: `state_dir` is optional: core keeps its own state under state_home(); a Bob's tools may use it.
DATA_PATHS = {"evidence_dir", "state_dir"}


def data_root(cfg: dict) -> pathlib.Path:
    """Where a Bob's data lives: `[paths] data_root` when set (absolute, or relative to the Bob's
    folder), else bob_root()."""
    root = (cfg.get("paths") or {}).get("data_root")
    return (bob_root() / root).resolve() if root else bob_root()


def path(cfg: dict, key: str) -> pathlib.Path:
    """A [paths] entry: data entries under data_root(), the Bob's own files under bob_root()."""
    base = data_root(cfg) if key in DATA_PATHS else bob_root()
    return base / str(get(cfg, f"paths.{key}"))


def bob_file(cfg: dict, dotted: str) -> pathlib.Path:
    """A manifest value that names a file inside the Bob's folder, as an absolute path."""
    return bob_root() / str(get(cfg, dotted))


def loop_name(own_file: str) -> str:
    """A loop's name is its file's stem: `loops/morning-brief.txt` is `morning-brief`."""
    return pathlib.PurePosixPath(str(own_file)).stem


def _repo_relative(value) -> bool:
    """Relative and inside the folder on every platform: no root, no drive, no `..` in either the
    POSIX or the Windows reading of the path."""
    if not isinstance(value, str) or not value or _absolute(value):
        return False
    win = pathlib.PureWindowsPath(value)
    return not (win.drive or win.root) and ".." not in win.parts


def _absolute(value) -> bool:
    """Absolute on macOS (`/tmp/x`) or on Windows (`C:\\tmp\\x`, `C:/tmp/x`, `\\\\host\\share`)."""
    if not isinstance(value, str):
        return False
    win = pathlib.PureWindowsPath(value)
    return value.startswith("/") or bool(win.drive and win.root)


def _has(cfg: dict, dotted: str) -> bool:
    try:
        get(cfg, dotted)
    except KeyError:
        return False
    return True


def validate(cfg: dict) -> list[str]:
    errors = [f"{k}: required" for k in REQUIRED if not _has(cfg, k)]
    for section, keys in SECTION_REQUIRED.items():
        if section in cfg:
            errors += [f"{k}: required when [{section}] is present" for k in keys if not _has(cfg, k)]

    def check(dotted: str, ok, why: str) -> None:
        if _has(cfg, dotted) and not ok(get(cfg, dotted)):
            errors.append(f"{dotted}: {why}")

    def is_str(v) -> bool:
        return isinstance(v, str) and v.strip() != ""

    check("bob.slug", lambda v: isinstance(v, str) and re.match(r"^[a-z][a-z0-9_]{1,30}$", v),
          "lowercase letters, digits and _, starting with a letter")
    check("bob.name", is_str, "a non-empty string")
    check("bob.purpose", is_str, "a non-empty string")
    check("board.port", lambda v: isinstance(v, int) and not isinstance(v, bool) and 1024 <= v <= 65535,
          "an integer from 1024 to 65535")
    check("board.title", is_str, "a non-empty string")
    check("board.panels",
          lambda v: isinstance(v, list) and all(isinstance(m, str) and MODULE.match(m) for m in v),
          "a list of importable module names, possibly empty")

    check("slack.user_id", lambda v: isinstance(v, str) and SLACK_USER.match(v), "not a Slack user id")
    check("slack.release_channel", lambda v: isinstance(v, str) and SLACK_CHANNEL.match(v),
          "not a Slack channel id")
    check("slack.authority",
          lambda v: isinstance(v, list) and v and all(isinstance(x, str) and SLACK_USER.match(x) for x in v),
          "a non-empty list of Slack user ids")
    for i, ch in enumerate((cfg.get("slack") or {}).get("channels", []) or []):
        where = f"slack.channels[{i}]"
        if not isinstance(ch, dict):
            errors.append(f"{where}: a table with id and mode")
            continue
        if not (isinstance(ch.get("id"), str) and SLACK_CHANNEL.match(ch["id"])):
            errors.append(f"{where}.id: not a Slack channel id")
        if ch.get("mode") not in CHANNEL_MODES:
            errors.append(f"{where}.mode: one of {sorted(CHANNEL_MODES)}")
    for name, ch in ((cfg.get("slack") or {}).get("roots") or {}).items():
        if not (isinstance(ch, str) and SLACK_CHANNEL.match(ch)):
            errors.append(f"slack.roots.{name}: not a Slack channel id")

    for key in ("routing.prefixes", "routing.keywords"):
        check(key, lambda v: isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v),
              "a list of non-empty strings")

    check("runtime.launchd_label", lambda v: isinstance(v, str) and LAUNCHD_LABEL.match(v),
          "a reverse-DNS launchd label, for example com.example.bob-tutor")
    for key in ("runtime.agent_log", "runtime.supervisor_tmp_prefix"):
        check(key, _absolute, "an absolute path")
    for key in ("runtime.sessions_file", "runtime.wake_prompt"):
        check(key, _repo_relative, "a relative path inside the Bob's folder")
    check("runtime.state_subdir", _repo_relative, "a relative path inside the config dir")
    check("runtime.claude_bin", is_str, "a command name or an absolute path")

    check("paths.data_root", is_str, "a non-empty path")
    for name, value in (cfg.get("paths") or {}).items():
        if name != "data_root" and not _repo_relative(value):
            errors.append(f"paths.{name}: a relative path inside the Bob's folder")

    loops = cfg.get("loops") or {}
    schedules = loops.get("schedules") or {}
    for name, cron in schedules.items():
        if not (isinstance(cron, str) and len(cron.split()) == 5):
            errors.append(f"loops.schedules.{name}: a five-field cron expression")
    own = loops.get("own", [])
    if not isinstance(own, list):
        errors.append("loops.own: a list of loop files, e.g. [\"loops/morning-brief.txt\"]")
        own = []
    for item in own:
        if not (_repo_relative(item) and str(item).endswith(".txt")):
            errors.append(f"loops.own: {item!r} is not a relative .txt path inside the Bob's folder")
        elif loop_name(item) not in schedules:
            errors.append(f"loops.own: {item!r} has no schedule; add "
                          f"\"{loop_name(item)}\" to [loops.schedules]")

    for name, cred in (cfg.get("credentials") or {}).items():
        where = f"credentials.{name}"
        if not isinstance(cred, dict):
            errors.append(f"{where}: a table with access, never a value")
            continue
        extra = sorted(set(cred) - CREDENTIAL_KEYS)
        if extra:
            errors.append(f"{where}: holds {extra}; a manifest names credentials, never values")
        if cred.get("access") not in ACCESS:
            errors.append(f"{where}.access: one of {sorted(ACCESS)}")
        elif cred["access"] == "read-write" and not cred.get("why"):
            errors.append(f"{where}.why: required for read-write access")
    return errors


def load(path: str | os.PathLike | None = None) -> dict:
    p = pathlib.Path(path) if path is not None else manifest_path()
    try:
        cfg = tomllib.loads(p.read_text())
    except FileNotFoundError:
        raise ManifestError([f"{p}: no manifest"]) from None
    except tomllib.TOMLDecodeError as e:
        raise ManifestError([f"{p}: not valid TOML: {e}"]) from None
    errors = validate(cfg)
    if errors:
        raise ManifestError(errors)
    return cfg


def main(argv: list[str]) -> int:
    try:
        cfg = load()
    except ManifestError as e:
        for err in e.errors:
            print(err)
        return 1
    if argv:
        print(get(cfg, argv[0]))
    else:
        print(f"OK {manifest_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
