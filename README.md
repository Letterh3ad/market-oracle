# market-oracle

Local global-stock research and paper-trading tool. Private.

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/oracle status
.venv/Scripts/python.exe -m pytest -q
```

Laya headline scores (optional; the CUDA torch build first on an NVIDIA GPU):

```bash
.venv/Scripts/python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python.exe -m pip install -e ".[laya]"
.venv/Scripts/python.exe -m pytest -q -m laya   # real model on the sanity set
```

Design decisions: `DECISIONS.md`. Planning notes are kept locally, outside the repo.

## Holiday watch

`holiday_watch/` watches the live app from outside it while the user is away. Stdlib only, it never
imports `oracle`. Settings: copy `holiday_watch/config.example.toml` to `holiday_watch/config.toml`.
Everything it writes goes to `holiday-reports/` (gitignored).

```bash
.venv/Scripts/python.exe -m holiday_watch pass --dry-run   # one watchdog pass; --dry-run starts and kills nothing
.venv/Scripts/python.exe -m holiday_watch report           # today's holiday-reports/YYYY-MM-DD.md
.venv/Scripts/python.exe -m holiday_watch status           # watch state, fix runs today, recent log
```

- **pass** (every 30 min): restarts the app from the live worktree and starts Ollama when they
  do not answer, keeps one Remote Control session (PID file), starts a fix run on a trigger (app
  still down after 2 restarts, replay failures over 20% or one error 3 times, a job over 2 h, a
  weekday tick missed by 45 min, no replay done in 6 h while idle), and reaps headless claude
  (`-p`) that is idle or over 3 h, never a Remote Control session, a fix run in its first 30 min,
  the app's own children, the Claude desktop app or Claude Code's daemon. Interactive sessions are
  only logged unless `reaper.reap_interactive` is on. One line per action in `watchdog.log`.
- **Fix runs**: one headless `claude -p` per trigger, locked down (`fix_prompt.md`, settings with
  `dontAsk`), killed after 30 min, at most 3 a day, the same kind not within 4 h. They act only
  through `python -P -m holiday_watch status|api|restart-app|start-ollama|sql` and fix code on a
  `holiday-fix` branch; never deploy.
- **report**: status, last tick, study progress and failures, interim score, account lines, the
  day's watchdog actions and fix runs, with a short claude summary on top (the plain report is
  written anyway if claude fails).
- **Arm / disarm** (App section, Holiday watch card; or `oracle holiday arm|disarm|status`):
  arm runs every check first and refuses on any failure, naming it and the fix: watch settings
  file present and on the app's database, app and Ollama answering, automation on, the replay
  study paused with both size halves and `[arm] expect_symbols`, cutoff probed, study models
  unchanged, no model change pending in `oracle.toml`, at most one Remote Control session, free
  disk over `[arm] min_free_gb`, no Windows Update restart pending and updates paused at least
  `[arm] away_days` (6), sleep and hibernate Never on mains power, plugged in, and on a laptop the
  lid set to do nothing on mains power. Automatic sign-in after an update restart being off is a
  warning. Then it backs the database up (SQLite backup API into `[arm] backups`,
  `../oracle-backups/`), keeps or starts the one Remote Control session (PID file), starts the
  study and registers two tasks (`python[w] -m holiday_watch pass|report --config <absolute>` run
  in the live worktree: every 30 min, and daily at `[arm] report_time`; only while logged on, on
  battery too, one at a time, stopped after 20 / 30 min while what they spawn lives on), then
  runs `pass --dry-run` as the live check: a non-zero exit undoes it all. One arm or disarm at a
  time (`holiday-reports/arm.lock`). Disarm deletes both tasks, ends a running fix run and pauses
  the study. Status runs the checks with no side effects and warns of a task Running over 25 min.
  `run.ps1` stays for running by hand.
- **Live test after arming:** after a pass that started the app or a fix run, Task Scheduler shows
  the watchdog `Ready` again and its Last Run Time moves on 30 min later.
