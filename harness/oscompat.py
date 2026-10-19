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
    utf8_stdio  Windows pipes default to cp1252, so printing "Ł" crashes and UTF-8 on stdin
                arrives garbled. Files say encoding="utf-8" explicitly for the same reason
                (test_text_encoding.py refuses any that do not).

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


def task_settings_command(label: str) -> str:
    """The PowerShell line that lets a supervisor task run on a laptop.

    `schtasks /Create` cannot set these, and Task Scheduler's defaults never start the task on
    battery, stop it when the charger is pulled, and skip a run missed while asleep: on a laptop the
    supervisor then never brings a stopped Bob back. The time limit keeps one hung tick from
    blocking every later one (a running task makes Task Scheduler skip new starts)."""
    return (f"Set-ScheduledTask -TaskName '{label}' -Settings (New-ScheduledTaskSettingsSet "
            "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable "
            "-ExecutionTimeLimit (New-TimeSpan -Minutes 10))")


def spawn_detached(cmd: list[str], cwd, log_path) -> int:
    """Start `cmd` so it outlives the process that started it, output appended to `log_path`.

    A board started by a supervisor tick must keep serving after the tick exits. A plain child
    shares the tick's console, process group and, under Task Scheduler, its job object: closing
    any of them can end the child too. On Windows the child gets no console, its own process group
    and, where the job allows it, breaks away from the job; elsewhere it gets its own session."""
    with open(log_path, "a", encoding="utf-8") as log:
        kwargs = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
                  "close_fds": True}
        if not WINDOWS:
            return subprocess.Popen(cmd, start_new_session=True, **kwargs).pid
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            return subprocess.Popen(cmd, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs).pid
        except OSError:
            return subprocess.Popen(cmd, creationflags=flags, **kwargs).pid


def utf8_stdio() -> None:
    """Read and write the standard streams as UTF-8, whatever the platform default.

    On Windows a pipe defaults to the ANSI code page (cp1252): printing "Ł" raises
    UnicodeEncodeError, and a hook's UTF-8 JSON on stdin arrives garbled. Call it first in every
    `if __name__ == "__main__":` block, before anything is read or printed."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


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
        with path.open("a+", encoding="utf-8") as handle:
            lock(handle)
            unlock(handle)
            lock(handle)
            unlock(handle)

    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import oscompat; oscompat.utf8_stdio(); "
             "print(sys.stdin.read())")
    out = subprocess.run([sys.executable, "-c", probe, str(pathlib.Path(__file__).resolve().parent)],
                         input="Ł →".encode("utf-8"), capture_output=True, timeout=30)
    check(out.returncode == 0 and out.stdout.strip() == "Ł →".encode("utf-8"),
          f"utf8_stdio does not pass UTF-8 through a pipe: {out.stderr.decode('utf-8', 'replace')[-200:]}")

    with tempfile.TemporaryDirectory() as tmp:
        log_path = pathlib.Path(tmp) / "detached.log"
        pid = spawn_detached([sys.executable, "-c", "print('detached ok', flush=True); import time; time.sleep(2)"],
                             tmp, log_path)
        check(pid_alive(pid), "a detached child reads as dead right after it started")
        deadline = time.time() + 15
        while pid_alive(pid) and time.time() < deadline:
            time.sleep(0.2)
        check("detached ok" in log_path.read_text(encoding="utf-8"),
              "a detached child's output did not reach its log")

    print(f"oscompat selftest: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    utf8_stdio()
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(selftest())
    print(__doc__)
