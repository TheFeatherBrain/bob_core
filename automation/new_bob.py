#!/usr/bin/env python3
"""Turn a manifest into a working Bob folder.

    python3 automation/new_bob.py <manifest.toml> --out DIR [--fleet FLEET.toml]

Validates the manifest (every rule in harness/bobcfg.py, plus the fleet, credential-value and
loop-template rules below), then writes DIR:

    bob.toml                  the manifest, copied
    CLAUDE.md                 imports this core's CORE.md, then the Bob's own rules
    launch.sh                 starts the Bob from its folder
    .claude/settings.json     the hook gates, run straight out of this core
    docs/TRACKER.md, docs/RELEASE-NOTES.md (paths from [paths])
    loops/<name>.txt          one per [loops] templates entry
    skills/README.md, INDEX.md  where the Bob writes what it learns; the index CLAUDE.md imports
    supervisor/               wake prompt, sessions list and launchd plist, when [runtime] is set

Core is referenced by its path relative to DIR, so DIR and this core must keep their relative
position (for example george/bob_core and george/tutor). Nothing is copied out of core, and no git
repository, account or credential is created. The Bob is appended to the fleet file (default:
fleet.toml in this core), which keeps slugs and board ports unique.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tomllib

CORE = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = CORE / "templates"
DEFAULT_FLEET = CORE / "fleet.toml"
sys.path.insert(0, str(CORE / "harness"))
import bobcfg  # noqa: E402  - core's own manifest rules
import skill_index  # noqa: E402

#: Shapes of real credential values. A manifest names credentials; a string like these means a
#: value was pasted in.
TOKEN_SHAPES = re.compile(r"xox[abposr]-[A-Za-z0-9-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|"
                          r"github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}")
#: `{bob.name}`, `{loops.schedules.slack-watch}`: dotted manifest keys, hyphens allowed because
#: TOML keys carry them.
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_-]*(?:\.[a-z_][a-z0-9_-]*)*)\}")

#: What each loop template needs from the manifest, beyond its schedule.
TEMPLATE_NEEDS = {
    "slack-watch": ["slack"],
    "release-digest": ["slack", "slack.release_channel"],
    "health-checks": ["runtime"],
}


class RenderError(Exception):
    pass


def _strings(value, where: str = ""):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{where}.{k}" if where else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, f"{where}[{i}]")


def validate(cfg: dict, fleet: dict) -> list[str]:
    """Every reason this manifest cannot become a Bob; empty when it can."""
    errors = bobcfg.validate(cfg)
    slug = cfg.get("bob", {}).get("slug")
    port = cfg.get("board", {}).get("port")
    for other in fleet.get("bob", []):
        if other.get("slug") == slug:
            errors.append(f"bob.slug: {slug!r} is already in the fleet")
        if other.get("port") == port:
            errors.append(f"board.port: {port} is used by {other.get('slug')!r}")
    for where, text in _strings(cfg):
        if TOKEN_SHAPES.search(text):
            errors.append(f"{where}: looks like a credential value; name the credential instead")
    schedules = cfg.get("loops", {}).get("schedules") or {}
    for name in cfg.get("loops", {}).get("templates", []):
        if not (TEMPLATES / "loops" / f"{name}.txt").is_file():
            errors.append(f"loops.templates: no template named {name!r}")
            continue
        if name not in schedules:
            errors.append(f"loops.schedules: {name!r} is used as a template and has no schedule")
        for need in TEMPLATE_NEEDS.get(name, []):
            try:
                bobcfg.get(cfg, need)
            except KeyError:
                errors.append(f"loops.templates: {name!r} needs {need} in the manifest")
    return errors


def context(cfg: dict, out: pathlib.Path) -> dict[str, str]:
    """Placeholder values: every scalar in the manifest by dotted key, plus a few in prose."""
    ctx = {k: str(v) for k, v in _strings(cfg) if "[" not in k}
    for section, table in cfg.items():
        for k, v in (table.items() if isinstance(table, dict) else []):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                ctx[f"{section}.{k}"] = str(v)
    slack = cfg.get("slack") or {}
    ctx["core"] = os.path.relpath(CORE, out)
    ctx["core_abs"] = str(CORE)
    ctx["bob_abs"] = str(out)
    ctx["python"] = sys.executable
    ctx["authority"] = ", ".join(slack.get("authority", [])) or "the owner"
    ctx["channels"] = ", ".join(f"{c['id']} ({c['mode']})" for c in slack.get("channels", [])) or "none"
    if slack:
        ctx["directed_by"] = f"Slack ids with authority: {ctx['authority']}"
        ctx["channels_line"] = (f"posts as {slack.get('user_id')}; watches {ctx['channels']}; "
                                f"release notes to {slack.get('release_channel', 'nowhere')}.")
        ctx["report_to"] = slack.get("release_channel") or "the terminal"
    else:
        ctx["directed_by"] = f"Directed by {cfg['bob'].get('owner') or 'the person who runs it'}"
        ctx["channels_line"] = "not configured; this Bob talks in its terminal."
        ctx["report_to"] = "the terminal"
    runtime = cfg.get("runtime") or {}
    ctx["config_dir"] = str(bobcfg.claude_home() if runtime.get("state_subdir")
                            else pathlib.Path.home() / f".claude-{cfg['bob']['slug']}")
    return ctx


def render(template: str, ctx: dict[str, str]) -> str:
    unknown = sorted({m for m in PLACEHOLDER.findall(template) if m not in ctx})
    if unknown:
        raise RenderError(f"no manifest value for {', '.join(unknown)}")
    return PLACEHOLDER.sub(lambda m: ctx[m.group(1)], template)


def settings(cfg: dict, core: str) -> dict:
    """The hook gates, run from the Bob's folder straight out of core. The skill gate on every
    tool call, the Slack gate only when the Bob talks on Slack; the reflect gate is opt-in (see
    harness/hooks/README.md)."""
    def hook(script: str) -> dict:
        return {"type": "command",
                "command": f'cd "$CLAUDE_PROJECT_DIR" && python3 {core}/harness/hooks/{script}'}
    pre = [{"matcher": "Bash", "hooks": [hook("commit_gate.py")]}]
    if cfg.get("slack"):
        pre.insert(0, {"matcher": "mcp__.*[Ss]lack.*__slack_(send_message|send_message_draft|schedule_message)",
                       "hooks": [hook("slack_send_gate.py")]})
    pre.append({"matcher": "*", "hooks": [hook("skill_gate.py")]})
    return {"hooks": {"PreToolUse": pre}}


def files_for(manifest: pathlib.Path, cfg: dict, out: pathlib.Path) -> dict[str, str]:
    ctx = context(cfg, out)
    t = lambda name: render((TEMPLATES / name).read_text(), ctx)  # noqa: E731
    shared = bool((cfg.get("runtime") or {}).get("state_subdir"))
    paths = cfg["paths"]
    files = {
        "bob.toml": manifest.read_text(),
        "CLAUDE.md": t("CLAUDE.md"),
        "launch.sh": t("launch-shared.sh" if shared else "launch.sh"),
        ".claude/settings.json": json.dumps(settings(cfg, ctx["core"]), indent=2) + "\n",
        paths["tracker"]: t("TRACKER.md"),
        paths["release_notes"]: t("RELEASE-NOTES.md"),
        "skills/README.md": t("skills-README.md"),
        "skills/INDEX.md": skill_index.render([]),
    }
    for name in cfg["loops"].get("templates", []):
        files[f"loops/{name}.txt"] = t(f"loops/{name}.txt")
    runtime = cfg.get("runtime")
    if runtime:
        files[runtime["wake_prompt"]] = t("supervisor/wake-prompt.md")
        files[runtime["sessions_file"]] = (TEMPLATES / "supervisor" / "sessions.json").read_text()
        files[f"supervisor/{runtime['launchd_label']}.plist"] = t("supervisor/agent.plist")
    return files


def scaffold(manifest: pathlib.Path, cfg: dict, out: pathlib.Path) -> list[str]:
    files = files_for(manifest, cfg, out)      # render everything before writing anything
    for rel, text in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (out / "launch.sh").chmod(0o755)
    return sorted(files)


def add_to_fleet(fleet_path: pathlib.Path, cfg: dict, out: pathlib.Path) -> None:
    entry = (f'\n[[bob]]\nslug = "{cfg["bob"]["slug"]}"\nname = "{cfg["bob"]["name"]}"\n'
             f'port = {cfg["board"]["port"]}\npath = "{out}"\n')
    with fleet_path.open("a") as fh:
        fh.write(entry)


NEXT_STEPS = """Next, by hand (PLAYBOOK.md section 3):
  1. cd {out} && ./launch.sh, then /login once (a new config dir starts signed out).
  2. If the Bob uses Slack: install the Slack plugin and sign it in as the Bob's own account.
  3. Ask the Bob to list its loaded rule files, then to register its loops from loops/.
  4. If [runtime] is set: put the session id in {sessions}, then
     cp supervisor/{label}.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/{label}.plist"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scaffold a Bob from its manifest.")
    ap.add_argument("manifest", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True, help="a new or empty directory")
    ap.add_argument("--fleet", type=pathlib.Path, default=DEFAULT_FLEET)
    a = ap.parse_args(argv)

    out = a.out.resolve()
    if out.exists() and any(out.iterdir()):
        print(f"FAIL: {out} is not empty; new_bob.py writes only into a new directory")
        return 1
    try:
        cfg = tomllib.loads(a.manifest.read_text())
        fleet = tomllib.loads(a.fleet.read_text()) if a.fleet.exists() else {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"FAIL: {e}")
        return 1
    errors = validate(cfg, fleet)
    if errors:
        print("FAIL: the manifest cannot become a Bob:")
        print("\n".join(f"  {e}" for e in errors))
        return 1
    try:
        written = scaffold(a.manifest, cfg, out)
    except RenderError as e:
        print(f"FAIL: {e}")
        return 1
    add_to_fleet(a.fleet, cfg, out)
    print(f"ok: {cfg['bob']['name']} scaffolded at {out} ({len(written)} files), "
          f"core at {os.path.relpath(CORE, out)}")
    print(f"ok: added to {a.fleet}")
    print()
    runtime = cfg.get("runtime") or {}
    print(NEXT_STEPS.format(out=out, sessions=runtime.get("sessions_file", "-"),
                            label=runtime.get("launchd_label", "-")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
