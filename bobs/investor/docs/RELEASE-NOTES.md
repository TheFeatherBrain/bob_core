# Investor Scout release notes

Newest first. Each change to this Bob or its tooling gets an entry saying what changed and what it
means for someone who relies on this Bob's output. "Nothing changes for you" is information too.

## The board starts with the Bob

- `[board] keep_up = true`: every supervisor tick (20 min) checks that http://localhost:8791
  answers and starts the board if not, so after a reboot it comes back on its own. A board you
  start by hand counts as up; the tick never starts a second one.
- Core's `board_shell.py` now finds this Bob's `scout_board` panel, so the board can be started the
  same way as any Bob's. `python3 harness/scout_board.py` still works.
- What it means for you: open http://localhost:8791 any time; nothing to start. The board's own
  output goes to `board.log` in `~/.claude-investor`.

## Missed loops run late; the supervisor runs on battery

- A loop firing that never ran (laptop asleep, session busy) is now run once, late, by the next
  hourly health check: nightly-screen, story-lane, weekly-digest and lessons-review
  (`[loops] catch_up` in bob.toml). Only the latest missed firing is run, and slots from before the
  Bob first recorded anything are skipped.
- The loop audit allows for Claude Code firing a job up to 30 minutes late (CronList shows the
  07:30 screen at 08:00). Before, every nightly screen would have been reported as missed.
- The supervisor task now starts on battery, keeps running when the charger is pulled, and runs
  after a tick missed while asleep; `watchdog_check.py` fails a task without those settings.
- What it means for you: a screen missed while the lid was closed arrives later the same day
  instead of not at all; a late screen still screens the last completed session.

## Short citations; the Form 4 backfill covers the last session's window

- Cards, market conditions on the board and the digest cite evidence as `[ev:<date>/<id>]`, the form
  theses use, instead of this machine's full path. Stored data is unchanged; a path outside the
  evidence folder is still printed in full, so a wrong citation stays visible.
- `edgar.py --backfill-form4` and the nightly catch-up read 7 days beyond the 90-day window. The
  screen measures that window from the last session, so a backfill run on a Monday used to leave
  Friday's window three days short and the insider factor at "could not tell" for every company.
- What it means for you: citations copied from a card into a thesis now look like the ones
  `thesis_lint.py` already accepted; the next backfill reads about 5 more trading days.

## First live sync: two read fixes

- Price reads end at the last completed session instead of today. The free Alpaca plan refused
  "today" (403), and a run during market hours could have stored a half-day bar as a close.
- An SEC 403 that is the Archives' "no such file" answer (a weekend or holiday daily index) is now
  treated as not found. Before, the first weekend in the Form 4 window stopped the whole sync. Any
  other 403 still stops it, and the message now names the refused URL.
- What it means for you: the nightly sync and screen now run end to end. First screen: session
  2026-10-14, 2,958 scored, 286 eligible, 20 on the shortlist. The insider factor stays "could not
  tell" until the 90-day Form 4 backfill is run.

## Keys from a file outside the repo

- `[runtime] env_file` names `C:/Users/georg/src/george/bob-secrets.env`; `launch.sh` and the
  supervisor load the Alpaca keys from it. A variable already set in the environment still wins.
- What it means for you: no `setx` needed. A malformed or misplaced file stops `launch.sh` with the
  line and key at fault (never the value); the supervisor logs it and keeps sessions alive.

## Scorecard v2: sectors, agreement, market conditions

- Growth and quality now rank within each company's sector (Fama-French 12 from its SEC SIC code),
  or against all stocks when the sector is small; each signal on the card says which.
- A top-ranked name needs at least 3 known factor groups above median to be eligible; otherwise
  it is "watch", with the count as the reason.
- Banks, insurers and real estate (SIC 6000 to 6799) no longer get an Altman Z distress veto.
- Every new call stores its full scorecard, the market conditions and the screen file's hash.
- New: `macro.py` (FRED, context only), `ledger_score.py --by-signal`, `data_audit.py`.
- What it means for you: scores from v2 are not comparable with v1; calls made under v1 stay in the
  ledger and are scored as before. Expect fewer eligible names than under v1.

## Scaffold

Folder created by new_bob.py. Nothing runs yet; see PLAYBOOK.md section 3 for first launch.
