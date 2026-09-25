"""Arming the holiday watch in one step: preflight checks that refuse on any failure, a database
backup, then the replay study started, the watchdog and morning-report tasks registered with Task
Scheduler and a live check of the watch. Disarm undoes it. Every side effect goes through a
HolidaySystem; tests pass a fake one, and only `real_holiday` builds the real one."""

from __future__ import annotations

import ctypes
import json
import locale
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time as clock_time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Protocol
from xml.sax.saxutils import escape

import httpx

from holiday_watch.config import EXAMPLE, Config as WatchConfig, config_file, load as load_watch_config
from holiday_watch.procs import NO_WINDOW, Proc, kill_tree, list_processes
from holiday_watch.reaper import is_claude, is_remote_control
from holiday_watch.seams import RealSystem
from holiday_watch.state import clear_lock, read_lock, read_pid, write_pid
from oracle.config import ConfigError, load_config
from oracle.services.context import AppContext
from oracle.services.cutoff_service import replay_cutoff
from oracle.services.errors import ServiceError
from oracle.services.replay_service import cannot_run, pause_study, start_study, study_status
from oracle.store.replay import PAUSED, ReplayRepo
from oracle.universe.listings import SIZES

__all__ = ["REPORT_TASK", "WATCHDOG_TASK", "Check", "Holiday", "HolidayError", "Power", "Report", "TaskInfo",
           "Updates", "backup_db", "parse_powercfg", "power_problems", "real_holiday", "task_xml"]

WATCHDOG_TASK = "Market Oracle holiday watchdog"
REPORT_TASK = "Market Oracle holiday report"
LIMITS_MIN = {WATCHDOG_TASK: 20, REPORT_TASK: 30}
"""Task Scheduler ends a run past this; the children a run spawns break away and live on."""
STUCK = timedelta(minutes=25)
"""A task Running longer is reported: with IgnoreNew it would skip every later run."""
DISARMED = "holiday disarmed"
DETAILS_UNAVAILABLE = "registered (details unavailable)"
FIRST_PASS_IN = timedelta(minutes=2)
"""The first real watchdog pass, soon enough to watch it land in watchdog.log before leaving."""
ARM_LOCK_STALE_S = 30 * 60
WATCH_TIMEOUT_S = 600
LIVE_TAIL = 12
GB = 1024 ** 3
MODEL_FIELDS = ("models", "model_slots", "allow_claude_code")
LARGE, MID_SMALL = SIZES
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
SLEEP_FIX = ("Settings > System > Power: sleep and hibernate Never when plugged in (or run "
             "`powercfg /change standby-timeout-ac 0` and `powercfg /change hibernate-timeout-ac 0`)")
LID_FIX = "Control Panel > Power Options > Choose what closing the lid does: Do nothing when plugged in"
PAUSE_FIX = "Settings > Windows Update > Pause updates, long enough to cover the whole trip; then arm"
SIGN_IN = ("Settings > Accounts > Sign-in options > 'Use my sign-in info to automatically finish setting up "
           "after an update'")
LID_ACTIONS = {0: "does nothing", 1: "sleeps", 2: "hibernates", 3: "shuts down"}


class HolidayError(ServiceError):
    pass


class Refused(HolidayError):
    """A failed check with a fix of its own."""

    def __init__(self, detail: str, fix: str) -> None:
        super().__init__(detail)
        self.fix = fix


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    """How to put it right, when it failed."""


@dataclass(frozen=True)
class Report:
    ok: bool
    armed: bool
    """Both tasks registered."""
    checks: tuple[Check, ...]
    lines: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    """Worth reading; none stops arming."""

    def to_json(self) -> dict:
        return {"ok": self.ok, "armed": self.armed, "checks": [asdict(c) for c in self.checks],
                "lines": list(self.lines), "warnings": list(self.warnings)}


@dataclass(frozen=True)
class Power:
    sleep_ac_min: int | None
    """Minutes idle on mains power before sleep; 0 is Never, None unreadable."""
    hibernate_ac_min: int | None
    on_ac: bool | None
    """None: could not tell."""
    battery: bool | None = False
    """A laptop: its lid can put it to sleep."""
    lid_ac: int | None = None
    """What closing the lid does on mains power (LID_ACTIONS); read only with a battery."""


@dataclass(frozen=True)
class Updates:
    restart_pending: bool | None
    paused_until: datetime | None
    """None: updates are not paused."""
    auto_sign_in: bool | None
    """Windows signs back in after an update restart (and locks), so logged-on tasks resume."""


@dataclass(frozen=True)
class TaskInfo:
    state: str
    """Ready, Running, Disabled..."""
    last_run: datetime | None
    next_run: datetime | None
    last_result: int | None


class HolidaySystem(Protocol):
    def get(self, url: str) -> dict: ...
    def processes(self) -> list[Proc]: ...
    def disk_free(self, path: Path) -> int: ...
    def power(self) -> Power: ...
    def windows_update(self) -> Updates: ...
    def backup(self, db: Path, dest: Path) -> None: ...
    def register_task(self, name: str, xml: str) -> None: ...
    def delete_task(self, name: str) -> bool:
        """False when no such task was registered."""
    def task_info(self, name: str) -> TaskInfo | None:
        """None when the task is not registered."""
    def watch(self, args: Sequence[str], python: Path, cwd: Path) -> tuple[int, str]:
        """`python -m holiday_watch *args`: its exit code and output."""
    def launch(self, args: Sequence[str], cwd: Path) -> int | None:
        """Start a process in a console of its own; its PID."""
    def kill(self, pid: int) -> str:
        """End the process tree; what taskkill said."""


class Holiday:
    def __init__(self, system: HolidaySystem, watch_config: Callable[[], tuple[WatchConfig, Path]],
                 tz: tzinfo | None = None) -> None:
        self.system = system
        self._watch_config = watch_config
        self.tz = tz
        """None: the PC's own zone, which Task Scheduler's times are in."""

    # The three commands

    def status(self, ctx: AppContext) -> Report:
        cfg, path, checks = self._load(ctx)
        more, warnings = self._checks(ctx, cfg, arming=False)
        checks += more
        lines = [*self._task_lines(), *self._study_lines(ctx)]
        if cfg:
            lines += self._watch(cfg, path, ("status",))[1]
        return Report(all(c.ok for c in checks), self._armed(), tuple(checks), tuple(lines),
                      tuple(warnings + self._task_warnings(ctx)))

    def arm(self, ctx: AppContext) -> Report:
        cfg, path, checks = self._load(ctx)
        if cfg is None:
            more, warnings = self._checks(ctx, None, arming=True)
            return self._refused([*checks, *more], warnings)
        with self._exclusive(cfg) as mine:
            if not mine:
                return _busy(self._armed(), cfg)
            return self._arm(ctx, cfg, path, checks)

    def disarm(self, ctx: AppContext) -> Report:
        cfg, _, _ = self._load(ctx)
        if cfg is None:
            return self._disarm(ctx, None)
        with self._exclusive(cfg) as mine:
            return self._disarm(ctx, cfg) if mine else _busy(self._armed(), cfg)

    # Arming and undoing

    def _arm(self, ctx: AppContext, cfg: WatchConfig, path: Path, checks: list[Check]) -> Report:
        more, warnings = self._checks(ctx, cfg, arming=True)
        checks += more
        if all(c.ok for c in checks):
            checks.append(self._check("DB backup", lambda: self._backup(ctx, cfg),
                                      "check the backups folder ([arm] backups in the watch settings) and free space"))
        if not all(c.ok for c in checks):
            return self._refused(checks, warnings)
        lines: list[str] = []
        launched = None
        try:
            tasks = self._task_xmls(ctx, cfg, path)
            launched = self._keep_remote_control(cfg, lines)
            start_study(ctx)
            lines.append("Started the replay study.")
            for name, xml in tasks:
                self.system.register_task(name, xml)
                lines.append(f"Registered task {name}.")
            code, shown = self._watch(cfg, path, ("pass", "--dry-run"))
            lines += shown
            live = Check("Live check", code == 0, "\n".join(shown[-LIVE_TAIL:]),
                         "" if code == 0 else "read the lines below; fix what the watch says, then arm again")
            checks.append(live)
            if not live.ok:
                raise HolidayError(f"the watch's dry-run pass failed (exit {code})")
        except Exception as exc:  # noqa: BLE001 (whatever broke, nothing is left half armed)
            lines.append(f"Arming failed: {exc}. Undone:")
            lines += self._undo(ctx, "arming failed", cfg, launched)
            return Report(False, False, tuple(checks), (f"Not armed: {exc}", *lines), tuple(warnings))
        lines += self._watch(cfg, path, ("status",))[1]
        return Report(True, self._armed(), tuple(checks), ("Armed.", *lines), tuple(warnings))

    @staticmethod
    def _refused(checks: list[Check], warnings: list[str]) -> Report:
        bad = ", ".join(c.name for c in checks if not c.ok)
        return Report(False, False, tuple(checks), (f"Not armed: failed {bad}.",), tuple(warnings))

    def _disarm(self, ctx: AppContext, cfg: WatchConfig | None) -> Report:
        lines = self._undo(ctx, DISARMED, cfg, end_fix_run=True)
        ok = not any(line.startswith("Could not") for line in lines)
        return Report(ok, self._armed(), (), ("Disarmed." if ok else "Disarm incomplete.", *lines))

    @contextmanager
    def _exclusive(self, cfg: WatchConfig) -> Iterator[bool]:
        """One arm or disarm at a time, across the GUI and the CLI: a lock file in the reports folder.
        One older than half an hour is left from a crash and taken over."""
        lock = cfg.reports / "arm.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            fd = _take_stale(lock)
        if fd is None:
            yield False
            return
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        try:
            yield True
        finally:
            lock.unlink(missing_ok=True)

    def _keep_remote_control(self, cfg: WatchConfig, lines: list[str]) -> int | None:
        """The one session the watch allows, in its PID file: the running one, else a new one, whose PID
        is returned so a rollback can close it."""
        found = self._sessions(cfg)
        launched = None
        if found:
            pid = found[0]
            lines.append(f"Remote Control session PID {pid} is the allowed one.")
        else:
            pid = launched = self.system.launch(cfg.rc_launch, cfg.repo)
            if pid is None:
                raise HolidayError(f"could not start {' '.join(cfg.rc_launch)}")
            lines.append(f"Remote Control session started (PID {pid}) in a new console: check it shows its link.")
        if read_pid(cfg.rc_pid_file) != pid:
            write_pid(cfg.rc_pid_file, pid)
        return launched

    def _task_xmls(self, ctx: AppContext, cfg: WatchConfig, path: Path) -> list[tuple[str, str]]:
        now = ctx.clock.utcnow().astimezone(self.tz).replace(tzinfo=None, microsecond=0)
        hh, mm = (int(x) for x in cfg.arm_report_time.split(":"))
        report_at = datetime.combine(now.date(), time(hh, mm))
        if report_at <= now:
            report_at += timedelta(days=1)
        python = _windowless(cfg.python)
        user = _user()

        def args(command: str) -> list[str]:
            return ["-m", "holiday_watch", command, "--config", str(path)]

        return [
            (WATCHDOG_TASK, task_xml(args("pass"), python, cfg.live, start=now + FIRST_PASS_IN,
                                     limit_min=LIMITS_MIN[WATCHDOG_TASK], repeat_min=cfg.arm_pass_every_min,
                                     user=user)),
            (REPORT_TASK, task_xml(args("report"), python, cfg.live, start=report_at,
                                   limit_min=LIMITS_MIN[REPORT_TASK], user=user)),
        ]

    def _undo(self, ctx: AppContext, reason: str, cfg: WatchConfig | None, launched: int | None = None,
              end_fix_run: bool = False) -> list[str]:
        lines = []
        for name in (WATCHDOG_TASK, REPORT_TASK):
            lines.append(self._try(f"delete task {name}", lambda name=name: f"Deleted task {name}."
                                   if self.system.delete_task(name) else f"Task {name} was not registered."))
        if launched is not None and cfg:
            lines.append(self._try("close the Remote Control session", lambda: self._close_session(cfg, launched)))
        if end_fix_run and cfg:
            lines += filter(None, [self._try("end the fix run", lambda: self._end_fix_run(cfg))])
        lines.append(self._try("pause the replay study", lambda: self._pause(ctx, reason)))
        return lines

    @staticmethod
    def _try(what: str, step: Callable[[], str]) -> str:
        try:
            return step()
        except Exception as exc:  # noqa: BLE001 (each step reports; the rest still run)
            return f"Could not {what}: {exc}"

    def _close_session(self, cfg: WatchConfig, pid: int) -> str:
        said = self.system.kill(pid)
        if read_pid(cfg.rc_pid_file) == pid:
            cfg.rc_pid_file.unlink(missing_ok=True)
        return f"Closed the Remote Control session arming started (PID {pid}): {said}"

    def _end_fix_run(self, cfg: WatchConfig) -> str:
        """A running fix run may start the study again through the app's API: it ends before the pause."""
        lock = read_lock(cfg.lock_file)
        if not lock:
            return ""
        pid = lock.get("pid")
        running = any(p.pid == pid and "holiday_watch" in p.cmdline and "fix-run" in p.cmdline
                      for p in self.system.processes())
        if not running:
            return f"A fix-run lock names PID {pid}, which is gone; left for the watch."
        said = self.system.kill(pid)
        if "SUCCESS" not in said:
            raise HolidayError(f"PID {pid} may still be running (taskkill said: {said})")
        clear_lock(cfg.lock_file)
        return f"Ended the fix run PID {pid} (started {lock.get('started_at')}): {said}"

    @staticmethod
    def _pause(ctx: AppContext, reason: str) -> str:
        study = ReplayRepo(ctx.conn).current()
        if study is None:
            return "No replay study."
        if study.state == PAUSED:
            return f"The replay study was already paused ({study.paused_reason or 'by hand'})."
        pause_study(ctx, reason)
        return "Paused the replay study."

    # Checks

    def _load(self, ctx: AppContext) -> tuple[WatchConfig | None, Path | None, list[Check]]:
        """The watch settings (their path made absolute, as the tasks need it) and their check."""
        try:
            cfg, path = self._watch_config()
            path = path.resolve()
        except Exception as exc:  # noqa: BLE001
            return None, None, [Check("Watch settings", False, f"the watch settings do not load: {exc}",
                                      "fix holiday_watch/config.toml (config.example.toml names every key)")]
        return cfg, path, [self._check("Watch settings", lambda: self._settings(ctx, cfg, path),
                                       f"copy {EXAMPLE} to {path} and set [paths] repo, live, python and db")]

    def _checks(self, ctx: AppContext, cfg: WatchConfig | None, arming: bool) -> tuple[list[Check], list[str]]:
        checks = []
        if cfg:
            checks += [
                self._check("App", lambda: self._answers(f"{cfg.app_url}/api/status"),
                            "start Oracle (the desktop shortcut), or fix [app] url in the watch settings"),
                self._check("Ollama", lambda: self._answers(f"{cfg.ollama_url}/api/tags"),
                            "start Ollama (`ollama serve`) and let it start at login"),
            ]
        checks += [
            self._check("Automation", lambda: self._automation(ctx), "turn Automation on (the switch in the header)"),
            self._check("Replay study", lambda: self._study(ctx, cfg, arming),
                        "create one on the Ledgers tab's Replay study card; it starts paused"),
            self._check("Cutoff probed", lambda: self._cutoff(ctx),
                        "Probe cutoff on the Ledgers tab's Replay study card"),
            self._check("Replay models", lambda: self._replay_models(ctx),
                        "point the model slots back to the study's, or create a new study"),
            self._check("Model settings", lambda: self._model_settings(ctx),
                        "wait a minute for the app to take up oracle.toml (or restart it), then check again"),
        ]
        if cfg:
            checks += [
                self._check("Remote Control", lambda: self._remote_control(cfg),
                            "close all but one `claude --remote-control` session: the watch keeps one"),
                self._check("Disk space", lambda: self._disk(ctx, cfg),
                            "free some space, or lower [arm] min_free_gb in the watch settings"),
            ]
        update, warnings = self._windows_update(ctx, cfg)
        return [*checks, update, *self._power()], warnings

    @staticmethod
    def _check(name: str, run: Callable[[], str], fix: str) -> Check:
        """`run` says what it found, or raises with what is wrong."""
        try:
            return Check(name, True, run())
        except Exception as exc:  # noqa: BLE001 (a broken check fails by name; it never arms)
            return Check(name, False, str(exc), getattr(exc, "fix", fix))

    def _settings(self, ctx: AppContext, cfg: WatchConfig, path: Path) -> str:
        if not path.exists():
            raise HolidayError(f"no watch settings file at {path}")
        wrong = []
        if not _same(cfg.db, ctx.config.db_path):
            wrong.append(f"the watch reads {cfg.db} but the app's database is {ctx.config.db_path}")
        if ctx.config_source and not _same(cfg.oracle_config, ctx.config_source):
            wrong.append(f"the watch starts the app with {cfg.oracle_config} but it runs with {ctx.config_source}")
        wrong += [f"{what} {p} does not exist" for what, p in (("python", cfg.python), ("live worktree", cfg.live))
                  if not p.exists()]
        if cfg.live.exists() and not (cfg.live / "holiday_watch" / "__main__.py").exists():
            wrong.append(f"the live worktree {cfg.live} has no holiday_watch package: deploy this build there")
        if wrong:
            raise HolidayError("; ".join(wrong))
        return f"{path}: the watch runs from {cfg.live}, reports in {cfg.reports}"

    def _answers(self, url: str) -> str:
        self.system.get(url)
        return f"answers at {url}"

    @staticmethod
    def _automation(ctx: AppContext) -> str:
        if not ctx.config.automation.enabled:
            raise HolidayError("automation is off: no ticks would run")
        return "on"

    @staticmethod
    def _study(ctx: AppContext, cfg: WatchConfig | None, arming: bool) -> str:
        s = study_status(ctx)
        if s is None:
            raise HolidayError("no replay study yet")
        halves = [p.half for p in s.listings]
        wrong = []
        if arming and s.state != PAUSED:
            wrong.append(f"it is {s.state}: pause it (Disarm does too), arming starts it")
        if s.finished:
            wrong.append("nothing is left to replay: create a new study")
        wrong += [f"it has no {half} listings: create a mix study" for half in (LARGE, MID_SMALL)
                  if half not in halves]
        symbols = [p.symbol for p in s.listings]
        missing = [x for x in (cfg.arm_expect_symbols if cfg else ()) if x not in symbols]
        if missing:
            wrong.append(f"it lacks {', '.join(missing)} ([arm] expect_symbols): replace a listing before the "
                         "first replay, or create it again")
        if wrong:
            raise HolidayError(f"study {s.id}: " + "; ".join(wrong))
        return (f"study {s.id}, {s.state}: {len(s.listings)} listings ({halves.count(LARGE)} large, "
                f"{halves.count(MID_SMALL)} mid/small), {s.pending} replays to run, {s.done} done; "
                f"listings: {', '.join(symbols)}")

    @staticmethod
    def _cutoff(ctx: AppContext) -> str:
        rc = replay_cutoff(ctx)
        if rc.unprobed or rc.cutoff is None:
            raise HolidayError(f"not probed: {', '.join(rc.unprobed) or 'the replay models'}")
        return f"cutoff {rc.cutoff}, replays from {rc.replay_from}"

    @staticmethod
    def _replay_models(ctx: AppContext) -> str:
        study = ReplayRepo(ctx.conn).current()
        if study is None:
            return "no study to compare"
        why, _ = cannot_run(ctx, study)
        if why:
            raise HolidayError(why)
        return "the slots still name the study's models"

    @staticmethod
    def _model_settings(ctx: AppContext) -> str:
        if ctx.config_source is None:
            return "no oracle.toml: defaults"
        try:
            on_disk = load_config(ctx.config_source)[0]
        except (ConfigError, OSError, ValueError) as exc:
            raise HolidayError(f"oracle.toml does not load ({exc}): the app keeps its old models") from exc
        changed = [f for f in MODEL_FIELDS if getattr(on_disk, f) != getattr(ctx.config, f)]
        if changed:
            raise HolidayError(f"oracle.toml's {', '.join(changed)} differ from what this app runs: a model change "
                               "is pending")
        return "oracle.toml matches the models this process runs (always so outside the app: arm from the app)"

    def _sessions(self, cfg: WatchConfig) -> list[int]:
        """Remote Control sessions: marked claude processes and the PID file's, if it is still claude."""
        procs = self.system.processes()
        found = {p.pid for p in procs if is_claude(p, cfg) and is_remote_control(p, cfg)}
        pid = read_pid(cfg.rc_pid_file)
        if any(p.pid == pid and is_claude(p, cfg) for p in procs):
            found.add(pid)
        return sorted(found)

    def _remote_control(self, cfg: WatchConfig) -> str:
        found = self._sessions(cfg)
        if len(found) > 1:
            raise HolidayError(f"{len(found)} Remote Control sessions (PIDs {', '.join(map(str, found))})")
        if found:
            return f"PID {found[0]}"
        if not cfg.rc_launch:
            raise HolidayError("none running and [remote_control] launch is empty")
        return f"none running: arming starts one ({' '.join(cfg.rc_launch)})"

    def _disk(self, ctx: AppContext, cfg: WatchConfig) -> str:
        where = Path(ctx.config.db_path).resolve().parent
        free = self.system.disk_free(where) / GB
        if free < cfg.arm_min_free_gb:
            raise HolidayError(f"{free:.1f} GB free on {where.anchor}, under {cfg.arm_min_free_gb:g} GB")
        return f"{free:.0f} GB free on {where.anchor}"

    def _windows_update(self, ctx: AppContext, cfg: WatchConfig | None) -> tuple[Check, list[str]]:
        """A restart while away ends every logged-on task until someone signs in: none may be pending, and
        updates must stay paused past the return."""
        warnings: list[str] = []

        def run() -> str:
            u = self.system.windows_update()
            if u.auto_sign_in is False:
                warnings.append(f"Automatic sign-in after an update restart is off: if Windows restarts anyway, "
                                f"nothing resumes until you sign in. Turn on {SIGN_IN}.")
            elif u.auto_sign_in is None:
                warnings.append(f"Automatic sign-in after an update restart: could not tell whether it is on; check "
                                f"{SIGN_IN} is on.")
            if u.restart_pending is None:
                raise HolidayError("could not tell whether a restart is pending")
            if u.restart_pending:
                raise Refused("a Windows Update restart is pending: Windows may restart the PC while you are away",
                              "restart the PC now, sign in and start Oracle, then arm")
            if u.paused_until is None:
                raise Refused("updates are not paused: Windows may restart the PC to install them", PAUSE_FIX)
            need = ctx.clock.utcnow() + timedelta(days=cfg.arm_away_days if cfg else 6)
            if u.paused_until < need:
                raise Refused(f"updates resume {u.paused_until:%Y-%m-%d %H:%M} UTC, before {need:%Y-%m-%d %H:%M}",
                              PAUSE_FIX)
            return f"paused until {u.paused_until:%Y-%m-%d %H:%M} UTC, no restart pending"

        return self._check("Windows Update", run, PAUSE_FIX), warnings

    def _power(self) -> list[Check]:
        try:
            p = self.system.power()
            sleep, plug = power_problems(p)
        except Exception as exc:  # noqa: BLE001
            p, sleep, plug = None, [f"could not read the power settings: {exc}"], [f"could not read: {exc}"]
        checks = [Check("Sleep", not sleep, "; ".join(sleep) or "Never on mains power", SLEEP_FIX if sleep else ""),
                  Check("Plugged in", not plug, "; ".join(plug) or "on mains power", "plug the PC in" if plug else "")]
        if p and p.battery:
            lid = ("could not read what closing the lid does" if p.lid_ac is None else
                   "" if p.lid_ac == 0 else
                   f"closing the lid on mains power {LID_ACTIONS.get(p.lid_ac, f'does action {p.lid_ac}')}")
            checks.append(Check("Lid", not lid, lid or "closing it does nothing on mains power",
                                LID_FIX if lid else ""))
        return checks

    def _backup(self, ctx: AppContext, cfg: WatchConfig) -> str:
        now = ctx.clock.utcnow().astimezone(self.tz)
        dest = cfg.arm_backups / f"oracle-holiday-{now:%Y-%m-%d-%H%M%S}.db"
        self.system.backup(Path(ctx.config.db_path), dest)
        return str(dest)

    # Status

    def _armed(self) -> bool:
        return all(self._info(name) is not None for name in (WATCHDOG_TASK, REPORT_TASK))

    def _info(self, name: str) -> TaskInfo | None:
        try:
            return self.system.task_info(name)
        except Exception:  # noqa: BLE001 (unreadable counts as not armed)
            return None

    def _task_lines(self) -> list[str]:
        lines = []
        for name in (WATCHDOG_TASK, REPORT_TASK):
            i = self._info(name)
            lines.append(f"Task {name}: not registered" if i is None else
                         f"Task {name}: {i.state}, last run {_when(i.last_run)} (result {i.last_result}), "
                         f"next {_when(i.next_run)}")
        return lines

    def _task_warnings(self, ctx: AppContext) -> list[str]:
        now = ctx.clock.utcnow()
        warnings = []
        for name in (WATCHDOG_TASK, REPORT_TASK):
            i = self._info(name)
            if i and i.state == "Running" and i.last_run and now - i.last_run > STUCK:
                warnings.append(f"Task {name} Running for {(now - i.last_run) // timedelta(minutes=1)} min: it may "
                                f"be stuck, and no later run starts until it ends (Task Scheduler stops it at "
                                f"{LIMITS_MIN[name]} min).")
        return warnings

    @staticmethod
    def _study_lines(ctx: AppContext) -> list[str]:
        s = study_status(ctx)
        if s is None:
            return ["No replay study."]
        why = f" ({s.paused_reason})" if s.state == PAUSED and s.paused_reason else ""
        return [f"Replay study {s.id}: {s.state}{why}, {s.done} of {s.total} done, {len(s.failed)} failed, "
                f"{s.pending} to run" + (f"; waiting: {s.waiting}" if s.waiting else "")]

    def _watch(self, cfg: WatchConfig, path: Path | None, args: tuple[str, ...]) -> tuple[int | None, list[str]]:
        """Run the watch from the live worktree, as the tasks do: its exit code (None when it could not
        run) and its output as lines to show."""
        full = [*args, *(("--config", str(path)) if path else ())]
        head = f"$ python -m holiday_watch {' '.join(args)}"
        try:
            code, out = self.system.watch(full, cfg.python, cfg.live)
        except Exception as exc:  # noqa: BLE001
            return None, [head, f"  could not run: {exc}"]
        return code, [head, *(f"  {line}" for line in out.splitlines())]


def _take_stale(lock: Path) -> int | None:
    """Moves a stale lock aside, which only one caller can do to one file, then checks the moved file
    is the stale one: another arm may have replaced it between the look and the move, and then gets
    it back. The new lock's descriptor, or None when busy."""
    try:
        if clock_time.time() - lock.stat().st_mtime <= ARM_LOCK_STALE_S:
            return None
        aside = lock.with_name(f"{lock.name}.{os.getpid()}.{clock_time.monotonic_ns()}")
        os.rename(lock, aside)
    except OSError:
        return None
    try:
        if clock_time.time() - aside.stat().st_mtime <= ARM_LOCK_STALE_S:
            try:
                os.rename(aside, lock)
            except OSError:
                aside.unlink(missing_ok=True)
            return None
        aside.unlink(missing_ok=True)
        return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return None


def _busy(armed: bool, cfg: WatchConfig) -> Report:
    return Report(False, armed, (), (
        f"An arm or disarm is already running ({cfg.reports / 'arm.lock'}): wait for it, then look again. A lock "
        f"older than {ARM_LOCK_STALE_S // 60} min is taken as stale, left by a crash, and taken over.",))


def _when(at: datetime | None) -> str:
    return at.astimezone().strftime("%Y-%m-%d %H:%M") if at else "never"


def _same(a: Path, b: Path) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def _windowless(python: Path) -> Path:
    """pythonw beside python, so a task every 30 min flashes no console window."""
    w = python.with_name("pythonw" + python.suffix)
    return w if python.name.lower().startswith("python.") and w.exists() else python


def _user() -> str | None:
    name, domain = os.environ.get("USERNAME"), os.environ.get("USERDOMAIN")
    return f"{domain}\\{name}" if name and domain else name


def power_problems(p: Power) -> tuple[list[str], list[str]]:
    """What stops the PC staying up: sleep problems, then power-source problems."""
    sleep = []
    for setting, minutes in (("sleep", p.sleep_ac_min), ("hibernate", p.hibernate_ac_min)):
        if minutes is None:
            sleep.append(f"could not read the {setting} setting")
        elif minutes:
            sleep.append(f"the PC {setting}s after {minutes} min on mains power")
    plug = ([] if p.on_ac else ["on battery"] if p.on_ac is False
            else ["could not tell whether the PC is on mains power"])
    return sleep, plug


def parse_powercfg(out: str) -> int | None:
    """The AC value of one setting from `powercfg /query` (seconds for timeouts, an index for actions)."""
    m = re.search(r"AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    return int(m.group(1), 16) if m else None


def task_xml(args: Sequence[str], command: Path, cwd: Path, *, start: datetime, limit_min: int,
             repeat_min: int | None = None, user: str | None = None) -> str:
    """A Task Scheduler definition: every `repeat_min` from `start`, else daily at `start`'s time. Run only
    while the user is logged on (the Remote Control relaunch needs a visible console), on battery too,
    one instance at a time, stopped after `limit_min` so a stuck run never blocks the next for good."""
    when = f"<StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary><Enabled>true</Enabled>"
    trigger = (f"<TimeTrigger><Repetition><Interval>PT{repeat_min}M</Interval><StopAtDurationEnd>false"
               f"</StopAtDurationEnd></Repetition>{when}</TimeTrigger>" if repeat_min else
               f"<CalendarTrigger>{when}<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
               "</CalendarTrigger>")
    who = f"<UserId>{escape(user)}</UserId>" if user else ""
    exe = f'"{command}"' if " " in str(command) else str(command)
    return (
        f'<Task version="1.2" xmlns="{TASK_NS}">'
        "<RegistrationInfo><Description>Market Oracle holiday watch (armed from the app; Disarm deletes it)"
        "</Description></RegistrationInfo>"
        f"<Triggers>{trigger}</Triggers>"
        f'<Principals><Principal id="Author">{who}<LogonType>InteractiveToken</LogonType>'
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>"
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate>"
        "<StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>"
        "<IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>"
        "<AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden>"
        "<RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>true</WakeToRun>"
        f"<ExecutionTimeLimit>PT{limit_min}M</ExecutionTimeLimit><Priority>7</Priority></Settings>"
        f'<Actions Context="Author"><Exec><Command>{escape(exe)}</Command>'
        f"<Arguments>{escape(subprocess.list2cmdline(list(args)))}</Arguments>"
        f"<WorkingDirectory>{escape(str(cwd))}</WorkingDirectory></Exec></Actions>"
        "</Task>"
    )


def backup_db(src: Path, dest: Path) -> None:
    """SQLite's online backup, safe while the app writes; the copy must pass a quick check."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise HolidayError(f"{dest} already exists")
    source = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(dest)
        try:
            source.backup(copy)
            verdict = copy.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            copy.close()
    finally:
        source.close()
    if verdict != "ok":
        raise HolidayError(f"the backup {dest} fails its check: {verdict}")


TASK_SCRIPT = (
    "$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction Stop; $i = $t | Get-ScheduledTaskInfo; "
    "function iso($d) {{ if ($d -and $d.Year -gt 2000) {{ $d.ToUniversalTime().ToString('o') }} }}; "
    "[pscustomobject]@{{state=\"$($t.State)\"; last=(iso $i.LastRunTime); next=(iso $i.NextRunTime); "
    "result=$i.LastTaskResult}} | ConvertTo-Json -Compress"
)
UPDATE_KEYS = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
               r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending")
PAUSE_KEY = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
SIGN_IN_POLICY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
ARSO_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\UserARSO"


class RealHolidaySystem:
    """The PC itself: HTTP, the process list, powercfg, the registry (read only), schtasks and the watch
    as a subprocess."""

    def get(self, url: str) -> dict:
        try:
            answer = httpx.get(url, timeout=20)
            answer.raise_for_status()
            return answer.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HolidayError(f"{url}: {exc}") from exc

    def processes(self) -> list[Proc]:
        return list_processes()

    def disk_free(self, path: Path) -> int:
        return shutil.disk_usage(path).free

    def power(self) -> Power:
        def ac(group: str, setting: str) -> int | None:
            return parse_powercfg(self._run(["powercfg", "/query", "SCHEME_CURRENT", group, setting])[1])

        def minutes(value: int | None) -> int | None:
            return None if value is None else value // 60

        on_ac, battery = _power_status()
        return Power(minutes(ac("SUB_SLEEP", "STANDBYIDLE")), minutes(ac("SUB_SLEEP", "HIBERNATEIDLE")), on_ac,
                     battery, ac("SUB_BUTTONS", "LIDACTION") if battery else None)

    def windows_update(self) -> Updates:
        import winreg

        def value(key: str, name: str) -> object | None:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                    return winreg.QueryValueEx(k, name)[0]
            except OSError:
                return None

        def exists(key: str) -> bool:
            try:
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key).Close()
                return True
            except OSError:
                return False

        paused = value(PAUSE_KEY, "PauseUpdatesExpiryTime")
        until = datetime.fromisoformat(str(paused).replace("Z", "+00:00")) if paused else None
        if until and until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if value(SIGN_IN_POLICY, "DisableAutomaticRestartSignOn") == 1:
            sign_in: bool | None = False
        else:
            opt_out = value(f"{ARSO_KEY}\\{_sid()}", "OptOut")
            sign_in = None if opt_out is None else opt_out == 0
        return Updates(any(exists(k) for k in UPDATE_KEYS), until, sign_in)

    def backup(self, db: Path, dest: Path) -> None:
        backup_db(db, dest)

    def register_task(self, name: str, xml: str) -> None:
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "task.xml"
            file.write_text('<?xml version="1.0" encoding="UTF-16"?>\n' + xml, encoding="utf-16")
            code, out = self._run(["schtasks", "/Create", "/TN", name, "/XML", str(file), "/F"])
        if code != 0:
            raise HolidayError(f"schtasks could not register {name}: {out.strip()}")

    def delete_task(self, name: str) -> bool:
        if not self._exists(name):
            return False
        code, out = self._run(["schtasks", "/Delete", "/TN", name, "/F"])
        if code != 0:
            raise HolidayError(f"schtasks could not delete {name}: {out.strip()}")
        return True

    def task_info(self, name: str) -> TaskInfo | None:
        """Existence from schtasks' exit code; the details from PowerShell, which may fail on its own."""
        if not self._exists(name):
            return None
        code, out = self._run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                               TASK_SCRIPT.format(name=name.replace("'", "''"))])
        try:
            if code != 0:
                raise ValueError(out)
            raw = json.loads(out)

            def at(text: str | None) -> datetime | None:
                return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", text)) if text else None

            return TaskInfo(raw["state"], at(raw.get("last")), at(raw.get("next")), raw.get("result"))
        except (ValueError, KeyError, TypeError):
            return TaskInfo(DETAILS_UNAVAILABLE, None, None, None)

    def _exists(self, name: str) -> bool:
        return self._run(["schtasks", "/Query", "/TN", name])[0] == 0

    def watch(self, args: Sequence[str], python: Path, cwd: Path) -> tuple[int, str]:
        # A pipe gets the ANSI code page from the child, not UTF-8.
        out = subprocess.run([str(python), "-m", "holiday_watch", *args], cwd=cwd, capture_output=True, text=True,
                             encoding=locale.getpreferredencoding(False), errors="replace", timeout=WATCH_TIMEOUT_S,
                             creationflags=NO_WINDOW)
        return out.returncode, (out.stdout + out.stderr).rstrip()

    def launch(self, args: Sequence[str], cwd: Path) -> int | None:
        return RealSystem().spawn(args, cwd, console=True)

    def kill(self, pid: int) -> str:
        return kill_tree(pid)

    @staticmethod
    def _run(args: list[str]) -> tuple[int, str]:
        out = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=120,
                             creationflags=NO_WINDOW)
        return out.returncode, out.stdout + out.stderr


def _power_status() -> tuple[bool | None, bool | None]:
    """On mains power, and whether a system battery exists (None: could not tell)."""
    class Status(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("battery", ctypes.c_ubyte), ("percent", ctypes.c_ubyte),
                    ("saver", ctypes.c_ubyte), ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]

    status = Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None, None
    battery = None if status.battery == 255 else not status.battery & 128  # 128: no system battery
    if status.ac in (0, 1):
        return bool(status.ac), battery
    return (True if battery is False else None), battery


def _sid() -> str:
    out = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=30,
                         creationflags=NO_WINDOW)
    return out.stdout.strip().split(",")[-1].strip('"')


def _load_watch_config() -> tuple[WatchConfig, Path]:
    path = config_file()
    return load_watch_config(path), path


def real_holiday() -> Holiday:
    """The only way to the real seams; the test suite replaces it (and RealHolidaySystem) with a refusal."""
    return Holiday(RealHolidaySystem(), _load_watch_config)
