#!/usr/bin/env python3
"""Gate N. Every name a function reads has to resolve to something.

A name read inside a function that resolves to nothing compiles fine and fails only when that
line runs - which may be at the end of a long job, after the work is done and before its result is
saved. `py_compile` does not see this. `symtable` does: it resolves
each name against the scopes the interpreter will use, so a name a function reads as an implicit
global that is neither a module global nor a builtin cannot resolve at runtime.

What it does NOT claim, and the second half matters. A module global created by a side effect this
scan cannot see - `globals()` assignment, a star import - reads as undefined here; those are listed
per module in `ALLOWED`, with a reason, rather than silently skipped.

And it does not see a LOCAL that is assigned on only some paths. `symtable` records such a name as
local to the scope, which is true, so a read before the branch that binds it raises
`UnboundLocalError` at runtime and this scan stays quiet. That needs dataflow, not scope resolution.
The practical rule, applied here: bind a name that a later statement reads before the `try` that
might not reach its assignment.

Run:  python3 harness/undefined_names.py            # the verdict
      python3 harness/undefined_names.py --selftest # prove the scan can still fail
"""

from __future__ import annotations

import argparse
import builtins
import pathlib
import symtable
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bobcfg  # noqa: E402

REPO = bobcfg.bob_root()

#: Names a module really does resolve at runtime by a route `symtable` cannot follow. Each needs a
#: reason: an empty allowance is indistinguishable from an oversight.
ALLOWED: dict[str, dict[str, str]] = {}

BUILTINS = frozenset(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__package__"}


def _scopes(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from _scopes(child)


def unresolved(source: str, path: str) -> list[tuple[str, str, int]]:
    """(scope, name, line) for every name read as a global that no global defines.

    The module's own top-level table supplies the globals - every import, def, class and assignment
    at module level lands there - and `builtins` supplies the rest.
    """
    top = symtable.symtable(source, path, "exec")
    module_globals = {sym.get_name() for sym in top.get_symbols()}
    allowed = set(ALLOWED.get(pathlib.Path(path).name, {}))
    out = []
    for scope in _scopes(top):
        if scope is top:
            continue
        for sym in scope.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced() or sym.is_assigned() or sym.is_parameter():
                continue
            if not sym.is_global():
                continue
            if name in module_globals or name in BUILTINS or name in allowed:
                continue
            out.append((scope.get_name(), name, scope.get_lineno()))
    return sorted(set(out))


def scan(paths) -> dict:
    findings, scanned = [], 0
    for path in sorted(paths):
        try:
            source = pathlib.Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            rows = unresolved(source, str(path))
        except SyntaxError as exc:
            findings.append((str(path), "<syntax>", f"{exc.msg} line {exc.lineno}", 0))
            continue
        scanned += 1
        for scope, name, line in rows:
            findings.append((str(path), scope, name, line))
    return {"scanned": scanned, "findings": findings}


def _targets():
    """Every `.py` under the Bob's folder, not only `harness/`: a scan whose scope is narrower
    than its claim reports a clean result it did not earn."""
    paths = list(REPO.rglob("*.py"))
    return [p for p in paths
            if ".venv" not in p.parts and "__pycache__" not in p.parts and p.is_file()]


def selftest() -> int:
    """Reintroduce the exact defect and confirm the scan reports it."""
    broken = (
        "import json\n"
        "def run(mode):\n"
        "    return json.dumps({'mode': args.mode})\n"
        "def main():\n"
        "    args = 1\n"
        "    return run('full')\n"
    )
    fixed = broken.replace("args.mode", "mode")
    # No file is written: `unresolved` reads the SOURCE it is handed, and uses the path only as
    # symtable's label and for the `ALLOWED` lookup. A scratch tree here would test nothing.
    label = "broken.py"
    got = unresolved(broken, label)
    if [(scope, name) for scope, name, _ in got] != [("run", "args")]:
        print(f"SELFTEST FAIL: expected run/args, got {got}")
        return 1
    if unresolved(fixed, label):
        print(f"SELFTEST FAIL: the fixed form still reports {unresolved(fixed, label)}")
        return 1
    print("selftest: the scan reports `args` read in a scope that does not define it, "
          "and reports nothing once the name is a parameter")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    result = scan(args.paths or _targets())
    for path, scope, name, line in result["findings"]:
        rel = pathlib.Path(path).relative_to(REPO) if str(path).startswith(str(REPO)) else path
        print(f"  {rel}:{line}  {scope}() reads `{name}`, which no scope defines")
    print(f"\n{result['scanned']} module(s) scanned, {len(result['findings'])} unresolved name(s)")
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    import oscompat
    oscompat.utf8_stdio()
    sys.exit(main())
