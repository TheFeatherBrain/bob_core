# Playbook: making a Bob

From an idea ("a Bob that teaches me English", "a Bob that watches my portfolio") to a running
agent. Every Bob shares this core; it differs in its manifest, its domain rules, its own tools and
its own loops.

## 1. Answer the manifest questions

Start from `bob.example.toml`. Each answer is a manifest field.

| question | field | required |
|---|---|---|
| What is this Bob for, in one sentence? | `[bob] purpose` | yes |
| Name and short slug (`tutor`, `investor`) | `[bob] name`, `slug` | yes |
| Who directs it? | `[bob] owner`, or `[slack] authority` | owner or Slack |
| Which port does its board use? Unique per machine | `[board] port` | yes |
| Where do its tracker, release notes and evidence go? | `[paths]` | yes (`state_dir` optional) |
| Which recurring jobs does it run, and when? | `[loops]` | yes; may be empty |
| Does it talk on Slack? As which account, in which channels? | `[slack]` | no |
| Must it stay alive unattended? | `[runtime]` | no; launchd on macOS, Task Scheduler on Windows |
| Which systems does it read? Name each credential, read-only or read-write | `[credentials]` | no |

Rules for the answers:

- Credentials are named, never pasted. `new_bob.py` refuses a manifest holding a token-shaped value.
- Read-only unless the reason for write access is written beside the credential.
- Loop templates need their sections: `slack-watch` and `release-digest` need `[slack]`;
  `health-checks` needs `[runtime]`. `lessons-review` needs nothing; every Bob should run it.

## 2. Scaffold

```bash
python3 automation/new_bob.py tutor.toml --out ../tutor
```

It validates the manifest (all errors at once), writes the folder, and adds the Bob to
`fleet.toml`, which keeps slugs and board ports unique. The Bob refers to core by relative path
(`../bob_core`), so keep the two folders side by side.

## 3. First launch

```bash
cd ../tutor && ./launch.sh
```

`launch.sh` sets `CLAUDE_CONFIG_DIR=~/.claude-<slug>`, so this Bob's login, memory, plugins and
state never mix with another Bob's. A new config dir starts signed out, so once:

1. `/login` to Claude Code.
2. If the Bob uses Slack: install the Slack plugin and sign it in as the Bob's own account. Check the
   Slack send gate's matcher in `.claude/settings.json` against the plugin's tool names.
3. Ask the Bob to list its loaded rule files; `CORE.md` must be among them.
4. Ask it to register its loops from `loops/` with the schedules in `bob.toml`.

To share one login between several Bobs instead, set `[runtime] state_subdir`; the scaffold then
writes a `launch.sh` that keeps the shared config dir and separates only the state files.

Keys the Bob's tools read from the environment can live in a file instead: set `[runtime]
env_file` to an absolute path outside every repo, one `KEY=value` per line, no quotes.
`launch.sh` and the supervisor load it (a variable already set wins); `python3
<core>/harness/envfile.py --check` lists the key names it holds, never the values.

## 4. Make it good at its job

This is the part core cannot do. In the Bob's folder:

- **`CLAUDE.md`, "Domain rules":** its sources, what it may and may not do, how it cites, what it does
  when it does not know. For an investor Bob: read-only, never trades, every number with its source
  and timestamp, stale prices marked. For a tutor: the learner's level and goals, how to correct,
  how to track recurring mistakes and when to revisit them.
- **`harness/`:** its own tools. Each one fetches or computes something, saves what it read with
  `evidence.save(id, raw, query=..., window=(start, end))` from `../bob_core/harness/evidence.py`,
  returns the path it gave back so answers can cite it, and has a `--selftest`.
- **`loops/`:** its own scheduled jobs (a morning brief, a weekly review, a daily quiz). Copy the
  first two lines of any template: re-register from `CronList`, then `loop_passes.py --record`.
  Add the file to `[loops] own` and its stem to `[loops.schedules]` (`loops/morning-brief.txt` is
  scheduled as `"morning-brief"`); the manifest is refused otherwise.
- **`skills/`:** seed it with what you already know goes wrong in this domain, one lesson per file
  with a tell (`skills/README.md`), then `skill_index.py --write`. For an investor Bob: a quote read
  without its timestamp. For a tutor: a correction given without the rule behind it.
- **A board panel** (optional): a module whose `main` builds its page on `board_shell.BaseHandler`,
  named in `[board] panels`.
- **"What we are not building":** every Bob design (and every later change to it) lists what is out
  of scope, so the next session does not build it by accident.
- **Back up before you migrate:** any change that rewrites a Bob's state, evidence or ledger starts
  with step 0: copy the state folder and the ledger to a dated backup folder and write its path in
  the tracker. A duplicate is recoverable; a migration that went wrong without a copy is not.

## 5. Keep it alive (optional, macOS or Windows)

With `[runtime]` set, the scaffold writes `supervisor/`. On macOS:

1. Put the session's id and name in `supervisor/sessions.json`
   (`{"sessions": [{"id": "<full id>", "name": "<name>"}]}`; `claude agents --json` lists them).
2. Edit `supervisor/wake-prompt.md` if the resumed session needs more than "re-register loops,
   read the tracker".
3. Set `[runtime] claude_bin` to the absolute path of `claude` (launchd has a minimal PATH).
4. Install and load the job:
   `cp supervisor/<label>.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/<label>.plist`
5. `python3 ../bob_core/harness/watchdog_check.py` must print `WATCHDOG: PASS`.

On Windows the scaffold writes `supervisor/tick.cmd` instead of the plist: it sets `BOB_MANIFEST`
and `CLAUDE_CONFIG_DIR` and appends the tick to `[runtime] agent_log`, which Task Scheduler cannot
do itself. Steps 1 to 3 are the same (`claude_bin` is the full path to `claude.exe` or
`claude.cmd`; `agent_log` and `supervisor_tmp_prefix` may be Windows paths such as
`C:/Users/<you>/AppData/Local/Temp/bob-tutor-agent.log`). Then:

4. Register the task under `[runtime] launchd_label`, from a terminal signed in as the Bob's user:
   `schtasks /Create /TN <label> /SC MINUTE /MO 20 /TR "\"<bob folder>\supervisor\tick.cmd\"" /F`
5. `python3 ../bob_core/harness/watchdog_check.py` must print `WATCHDOG: PASS`.

## 6. Done when

Each of these is shown with evidence in the Bob's tracker:

1. `python3 ../bob_core/harness/bobcfg.py` prints OK from the Bob's folder.
2. One full pass of each loop is recorded: `loop_passes.py --audit` shows no misses.
3. The board answers on its port.
4. Each credential is used once, and a read-only one refuses a write.
5. If on Slack: the Bob posts as its own account, and answers a question in a thread.
6. A lesson's tell, triggered on purpose, shows the lesson (the skill gate answers) and appears in
   `skill_index.py --stats`.
7. The owner confirms the Bob answers in scope.

## 7. Changing core

A change to shared behaviour goes into `bob_core` and reaches every Bob at once. Run
`./selftest.sh` before relying on it. A Bob-specific need goes into that Bob's folder instead.
Retiring a Bob: unload its LaunchAgent (or `schtasks /Delete /TN <label>` on Windows), revoke its credentials, remove it from `fleet.toml`.
