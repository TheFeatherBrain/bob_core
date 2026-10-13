#!/usr/bin/env python3
"""Every text read, write and pipe in core says UTF-8; no file, subprocess or entry point relies on
the platform default.

Why a check and not a habit: on Windows the default is the ANSI code page (cp1252). A file read
without an encoding garbles or crashes on "Ł", a print of it to a pipe raises UnicodeEncodeError,
and a hook's UTF-8 JSON on stdin arrives garbled. Each slip looks fine on macOS and in every
ASCII-only test, so it only shows on a Windows machine with real data.

What it refuses, in every non-test module:
- `read_text()` / `write_text()` without `encoding=`;
- `open()`, `io.open()`, `os.fdopen()` or `Path.open()` in text mode without `encoding=`;
- `subprocess.run/check_output/Popen/...` (as `subprocess.x` or imported by name) with
  `text=True` (or `universal_newlines=True`) without `encoding=`;
- `os.popen`, `subprocess.getoutput` and `subprocess.getstatusoutput`, which cannot take one;
- text-mode `tempfile.NamedTemporaryFile/TemporaryFile/SpooledTemporaryFile`, `io.TextIOWrapper`
  and `logging.FileHandler` (and its rotating forms) without `encoding=`;
- an `if __name__ == "__main__":` module whose main block (or a function named main) never calls
  `utf8_stdio()` (oscompat).
A `**kwargs` call cannot show its encoding, so it needs an explicit `encoding=` beside it.

Known limits: it reads the call, not the type, so `zf.open(name)` on a ZipFile, or a mode held in
a variable, is flagged even when binary; pass `encoding=`, `"rb"`, or restructure. A file object
reached some other way (a third-party wrapper, `codecs` with the default) is not seen.

Test files are not scanned: they write their own ASCII fixtures. A module that keeps code outside
core can run `findings()` over it the same way.

Run:  python3 harness/test_text_encoding.py
"""
from __future__ import annotations

import ast
import pathlib
import textwrap

HERE = pathlib.Path(__file__).resolve().parent
CORE = HERE.parent
TEXT_IO = {"read_text", "write_text"}
RUNNERS = {"run", "check_output", "check_call", "call", "Popen"}
NOT_FILES = {"os", "webbrowser", "tarfile", "zipfile", "gzip", "bz2", "lzma"}
NO_ENCODING = {("os", "popen"), ("subprocess", "getoutput"), ("subprocess", "getstatusoutput")}
TEMP_FILES = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}
LOG_FILES = {"FileHandler", "RotatingFileHandler", "TimedRotatingFileHandler", "WatchedFileHandler"}


def _has_kw(call: ast.Call, name: str) -> bool:
    return any(k.arg == name for k in call.keywords)


def _true_kw(call: ast.Call, name: str) -> bool:
    return any(k.arg == name and not (isinstance(k.value, ast.Constant) and not k.value.value)
               for k in call.keywords)


def _text_mode(call: ast.Call, pos: int) -> bool:
    mode = call.args[pos] if len(call.args) > pos else next(
        (k.value for k in call.keywords if k.arg == "mode"), ast.Constant("r"))
    return not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value)


def _open_position(func: ast.expr) -> int | None:
    if isinstance(func, ast.Name) and func.id == "open":
        return 1
    if not isinstance(func, ast.Attribute):
        return None
    owner = func.value.id if isinstance(func.value, ast.Name) else None
    if func.attr == "fdopen" or (func.attr == "open" and owner == "io"):
        return 1
    if func.attr == "open" and owner not in NOT_FILES:
        return 0
    return None


def _owner_attr(func: ast.expr) -> tuple[str | None, str | None]:
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else (
            func.value.attr if isinstance(func.value, ast.Attribute) else None)
        return owner, func.attr
    return None, None


def _runner(func: ast.expr, imported: set[str]) -> str | None:
    owner, attr = _owner_attr(func)
    if owner == "subprocess" and attr in RUNNERS:
        return attr
    if isinstance(func, ast.Name) and func.id in RUNNERS and func.id in imported:
        return func.id
    return None


def _problem(call: ast.Call, imported: set[str]) -> str | None:
    func = call.func
    owner, attr = _owner_attr(func)
    if (owner, attr) in NO_ENCODING:
        return f"{owner}.{attr}() decodes with the platform default; use subprocess.run(..., encoding=)"
    if _has_kw(call, "encoding"):
        return None
    if attr in TEXT_IO:
        return f"{attr}() without encoding="
    pos = _open_position(func)
    if pos is not None and _text_mode(call, pos):
        return "text-mode open() without encoding="
    runner = _runner(func, imported)
    if runner and (_true_kw(call, "text") or _true_kw(call, "universal_newlines")):
        return f"subprocess.{runner}(text=True) without encoding="
    if owner == "tempfile" and attr in TEMP_FILES and (call.args or _has_kw(call, "mode")) and _text_mode(call, 0):
        return f"text-mode tempfile.{attr}() without encoding="
    if (owner, attr) == ("io", "TextIOWrapper") or attr in LOG_FILES:
        return f"{attr}() without encoding="
    return None


def _is_main_guard(node: ast.stmt) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def _calls_utf8_stdio(nodes: list[ast.AST]) -> bool:
    return any(isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Attribute) and n.func.attr == "utf8_stdio")
        or (isinstance(n.func, ast.Name) and n.func.id == "utf8_stdio"))
        for node in nodes for n in ast.walk(node))


def _imported_runners(tree: ast.Module) -> set[str]:
    return {a.asname or a.name for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module == "subprocess" for a in n.names}


def source_findings(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    imported = _imported_runners(tree)
    out = [f"{label}:{n.lineno}: {why}" for n in ast.walk(tree)
           if isinstance(n, ast.Call) and (why := _problem(n, imported))]
    guards = [n for n in tree.body if _is_main_guard(n)]
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    if guards and not _calls_utf8_stdio(guards + mains):
        out.append(f"{label}: has a __main__ block but its main never calls oscompat.utf8_stdio()")
    return out


def findings(files: list[pathlib.Path], root: pathlib.Path) -> list[str]:
    return [f for path in files
            for f in source_findings(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix())]


def modules(*folders: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for folder in folders for p in folder.glob("*.py") if not p.name.startswith("test_"))


def _one(code: str) -> list[str]:
    return source_findings(textwrap.dedent(code), "x.py")


def test_core_has_no_bare_text_io():
    found = findings(modules(HERE, HERE / "hooks", CORE / "automation"), CORE)
    assert found == [], "\n".join(found)


def test_bare_read_and_write_text_refused():
    assert len(_one("p.read_text()\np.write_text(s)\n")) == 2


def test_read_and_write_text_with_encoding_allowed():
    assert _one("p.read_text(encoding='utf-8')\np.write_text(s, encoding='utf-8')\n") == []


def test_text_open_refused_binary_open_allowed():
    assert len(_one("open(f)\nopen(f, 'a')\np.open('w')\nos.fdopen(fd, 'w')\n")) == 4
    assert _one("open(f, 'rb')\np.open('ab')\nopen(f, encoding='utf-8')\nos.open(f, 0)\nwebbrowser.open(u)\n") == []


def test_subprocess_text_needs_encoding():
    assert len(_one("subprocess.run(a, text=True)\nsubprocess.check_output(a, universal_newlines=True)\n")) == 2
    assert _one("subprocess.run(a, text=True, encoding='utf-8')\nsubprocess.run(a, capture_output=True)\n") == []


def test_entry_point_must_call_utf8_stdio():
    assert _one("def main(): pass\nif __name__ == '__main__':\n    main()\n")
    assert _one("def main(): pass\nif __name__ == '__main__':\n    oscompat.utf8_stdio()\n    main()\n") == []
    assert _one("def main():\n    oscompat.utf8_stdio()\nif __name__ == '__main__':\n    main()\n") == []


def test_utf8_stdio_elsewhere_does_not_count():
    assert _one("def helper():\n    oscompat.utf8_stdio()\nif __name__ == '__main__':\n    pass\n")


def test_imported_runners_and_text_only_apis_refused():
    assert len(_one("from subprocess import run, check_output\nrun(a, text=True)\ncheck_output(a, text=True)\n")) == 2
    assert len(_one("os.popen('ls')\nsubprocess.getoutput('ls')\nsubprocess.getstatusoutput('ls')\n")) == 3
    assert _one("from subprocess import run\nrun(a, text=True, encoding='utf-8')\n") == []


def test_text_temp_files_wrappers_and_log_files_need_encoding():
    found = _one("tempfile.NamedTemporaryFile('w')\ntempfile.TemporaryFile(mode='w+')\n"
                 "io.TextIOWrapper(b)\nlogging.FileHandler(p)\n")
    assert len(found) == 4, found
    assert _one("tempfile.NamedTemporaryFile()\ntempfile.TemporaryFile('w', encoding='utf-8')\n"
                "io.TextIOWrapper(b, encoding='utf-8')\nlogging.FileHandler(p, encoding='utf-8')\n") == []


def test_kwargs_cannot_hide_the_encoding():
    assert _one("p.read_text(**opts)\n")
    assert _one("p.read_text(encoding='utf-8', **opts)\n") == []


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
