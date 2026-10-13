#!/usr/bin/env python3
"""The few operating-system calls core needs that differ between macOS/Linux and Windows.

Each one exists because the POSIX spelling is wrong on Windows, not merely missing:

    pid_alive   `os.kill(pid, 0)` on Windows is not a probe. Signal 0 is CTRL_C_EVENT there, so it
                sends Ctrl+C to the console process group `pid` leads, and interrupts the very
                session a liveness check was meant to leave alone.
    lock/unlock `fcntl` does not exist on Windows; `msvcrt.locking` is the equivalent.
    toml_str    a Windows path holds backslashes, which a TOML basic string reads as escapes, so
                writing `path = "C:\\Users\\..."` naively produces a file that no longer parses.
    posix       a path rendered into a shell command, an @import or a plist must use forward
                slashes: Git Bash eats unquoted backslashes, and both forms work on Windows.

Run:  python3 harness/oscompat.py --selftest
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import tomllib

WINDOWS = os.name == "nt"

if WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
else:
    import fcntl


def pid_alive(pid: int) -> bool:
    """True when a process with this id is running. Never signals it."""
    if pid <= 0:
        return False
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def lock(handle) -> None:
    """Block until this process holds an exclusive lock on the open file."""
    if not WINDOWS:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError:
            time.sleep(0.05)


def unlock(handle) -> None:
    if not WINDOWS:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def toml_str(value) -> str:
    """A TOML basic string holding `value` exactly. JSON string escapes are a subset of TOML's."""
    return json.dumps(str(value))


def posix(path) -> str:
    """A path with forward slashes, for text that a shell, an @import or a plist reads."""
    return str(path).replace("\\", "/")


def selftest() -> int:
    bad = 0

    def check(ok: bool, label: str) -> None:
        nonlocal bad
        if not ok:
            bad += 1
            print(f"FAIL {label}")

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(0.2)
        check(pid_alive(os.getpid()), "this process reads as dead")
        check(pid_alive(child.pid), "a running child reads as dead")
        check(child.poll() is None, "the liveness probe disturbed the child")
    finally:
        child.kill()
        child.wait()
    check(not pid_alive(child.pid), "a reaped child reads as alive")
    check(not pid_alive(0) and not pid_alive(-1), "a non-positive pid reads as alive")

    for raw in ("C:\\Users\\george\\bobs\\tutor", 'a "quoted" name', "/tmp/plain", "tab\there"):
        parsed = tomllib.loads(f"v = {toml_str(raw)}\n")["v"]
        check(parsed == raw, f"toml_str does not round-trip {raw!r}")
    check(posix("..\\bob_core\\harness") == "../bob_core/harness", "posix keeps backslashes")

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "x.lock"
        with path.open("a+") as handle:
            lock(handle)
            unlock(handle)
            lock(handle)
            unlock(handle)

    print(f"oscompat selftest: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(selftest())
    print(__doc__)
