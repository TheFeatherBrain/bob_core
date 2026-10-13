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
- `subprocess.run/check_output/Popen/...` with `text=True` (or `universal_newlines=True`)
  without `encoding=`;
- an `if __name__ == "__main__":` module that never calls `utf8_stdio()` (oscompat).

Test files are not scanned: they write their own ASCII fixtures. A Bob reuses `findings()` on its
own harness (core never looks into bobs/).

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


def _problem(call: ast.Call) -> str | None:
    func = call.func
    if _has_kw(call, "encoding"):
        return None
    if isinstance(func, ast.Attribute) and func.attr in TEXT_IO:
        return f"{func.attr}() without encoding="
    pos = _open_position(func)
    if pos is not None and _text_mode(call, pos):
        return "text-mode open() without encoding="
    if (isinstance(func, ast.Attribute) and func.attr in RUNNERS and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and (_true_kw(call, "text") or _true_kw(call, "universal_newlines"))):
        return f"subprocess.{func.attr}(text=True) without encoding="
    return None


def _is_main_guard(node: ast.stmt) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


def _calls_utf8_stdio(tree: ast.Module) -> bool:
    return any(isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Attribute) and n.func.attr == "utf8_stdio")
        or (isinstance(n.func, ast.Name) and n.func.id == "utf8_stdio")) for n in ast.walk(tree))


def source_findings(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    out = [f"{label}:{n.lineno}: {why}" for n in ast.walk(tree)
           if isinstance(n, ast.Call) and (why := _problem(n))]
    if any(_is_main_guard(n) for n in tree.body) and not _calls_utf8_stdio(tree):
        out.append(f"{label}: has a __main__ block but never calls oscompat.utf8_stdio()")
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
