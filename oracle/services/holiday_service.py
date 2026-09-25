"""Arming the holiday watch in one step: preflight checks that refuse on any failure, a database
backup, then the replay study started and the watchdog and morning-report tasks registered with
Task Scheduler. Disarm undoes it. Every side effect goes through a HolidaySystem; tests pass a fake
one, and only `real_holiday` builds the real one."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Protocol
from xml.sax.saxutils import escape

import httpx

import holiday_watch
from holiday_watch.config import EXAMPLE, Config as WatchConfig, config_file, load as load_watch_config
from holiday_watch.procs import NO_WINDOW, Proc, list_processes
from holiday_watch.reaper import is_claude, is_remote_control
from holiday_watch.seams import RealSystem
from holiday_watch.state import read_pid, write_pid
from oracle.config import ConfigError, load_config
from oracle.services.context import AppContext
from oracle.services.cutoff_service import replay_cutoff
from oracle.services.errors import ServiceError
from oracle.services.replay_service import cannot_run, pause_study, start_study, study_status
from oracle.store.replay import PAUSED, ReplayRepo
from oracle.universe.listings import SIZES

__all__ = ["REPORT_TASK", "WATCHDOG_TASK", "Check", "Holiday", "HolidayError", "Power", "Report", "backup_db",
           "parse_powercfg", "power_problems", "real_holiday", "task_xml"]

WATCHDOG_TASK = "Market Oracle holiday watchdog"
REPORT_TASK = "Market Oracle holiday report"
DISARMED = "holiday disarmed"
FIRST_PASS_IN = timedelta(minutes=2)
"""The first real watchdog pass, soon enough to watch it land in watchdog.log before leaving."""
WATCH_TIMEOUT_S = 600
GB = 1024 ** 3
MODEL_FIELDS = ("models", "model_slots", "allow_claude_code")
LARGE, MID_SMALL = SIZES
CODE_ROOT = Path(holiday_watch.__file__).resolve().parent.parent
"""The folder holding the watch package: the tasks run from it."""
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
SLEEP_FIX = ("Settings > System > Power: sleep and hibernate Never when plugged in (or run "
             "`powercfg /change standby-timeout-ac 0` and `powercfg /change hibernate-timeout-ac 0`)")


class HolidayError(ServiceError):
    pass


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

    def to_json(self) -> dict:
        return {"ok": self.ok, "armed": self.armed, "checks": [asdict(c) for c in self.checks],
                "lines": list(self.lines)}


@dataclass(frozen=True)
class Power:
    sleep_ac_min: int | None
    """Minutes idle on mains power before sleep; 0 is Never, None unreadable."""
    hibernate_ac_min: int | None
    on_ac: bool | None
    """None: could not tell."""


class HolidaySystem(Protocol):
    def get(self, url: str) -> dict: ...
    def processes(self) -> list[Proc]: ...
    def disk_free(self, path: Path) -> int: ...
    def power(self) -> Power: ...
    def backup(self, db: Path, dest: Path) -> None: ...
    def register_task(self, name: str, xml: str) -> None: ...
    def delete_task(self, name: str) -> bool:
        """False when no such task was registered."""
    def task_info(self, name: str) -> str | None:
        """What Task Scheduler says of the task, or None when it is not registered."""
    def watch(self, args: Sequence[str], python: Path, cwd: Path) -> str:
        """`python -m holiday_watch *args`'s output."""
    def launch(self, args: Sequence[str], cwd: Path) -> int | None:
        """Start a process in a console of its own; its PID."""


class Holiday:
    def __init__(self, system: HolidaySystem, watch_config: Callable[[], tuple[WatchConfig, Path]],
                 code_root: Path = CODE_ROOT, tz: tzinfo | None = None) -> None:
        self.system = system
        self._watch_config = watch_config
        self.code_root = code_root
        self.tz = tz
        """None: the PC's own zone, which Task Scheduler's times are in."""

    # The three commands

    def status(self, ctx: AppContext) -> Report:
        cfg, path, checks = self._preflight(ctx, arming=False)
        lines = [*self._task_lines(), *self._study_lines(ctx)]
        if cfg:
            lines += self._watch(cfg, path, ("status",))
        return Report(all(c.ok for c in checks), self._armed(), tuple(checks), tuple(lines))

    def arm(self, ctx: AppContext) -> Report:
        cfg, path, checks = self._preflight(ctx, arming=True)
        if cfg and all(c.ok for c in checks):
            checks.append(self._check("DB backup", lambda: self._backup(ctx, cfg),
                                      "check the backups folder ([arm] backups in the watch settings) and free space"))
        bad = [c.name for c in checks if not c.ok]
        if bad:
            return Report(False, False, tuple(checks), (f"Not armed: failed {', '.join(bad)}.",))
        assert cfg and path
        lines: list[str] = []
        try:
            self._keep_remote_control(cfg, lines)
            start_study(ctx)
            lines.append("Started the replay study.")
            for name, xml in self._task_xmls(ctx, cfg, path):
                self.system.register_task(name, xml)
                lines.append(f"Registered task {name}.")
        except Exception as exc:  # noqa: BLE001 (whatever broke, nothing is left half armed)
            lines.append(f"Arming failed: {exc}. Undone:")
            lines += self._undo(ctx, "arming failed")
            return Report(False, False, tuple(checks), (f"Not armed: {exc}", *lines))
        lines += self._watch(cfg, path, ("pass", "--dry-run"))
        lines += self._watch(cfg, path, ("status",))
        return Report(True, self._armed(), tuple(checks), ("Armed.", *lines))

    def disarm(self, ctx: AppContext) -> Report:
        lines = self._undo(ctx, DISARMED)
        ok = not any(line.startswith("Could not") for line in lines)
        return Report(ok, self._armed(), (), ("Disarmed." if ok else "Disarm incomplete.", *lines))

    # Preflight

    def _preflight(self, ctx: AppContext, arming: bool) -> tuple[WatchConfig | None, Path | None, list[Check]]:
        try:
            cfg, path = self._watch_config()
        except Exception as exc:  # noqa: BLE001
            cfg, path = None, None
            checks = [Check("Watch settings", False, f"the watch settings do not load: {exc}",
                            "fix holiday_watch/config.toml")]
        else:
            checks = [self._check("Watch settings", lambda: self._settings(ctx, cfg, path),
                                  f"copy {EXAMPLE} to {path} and set [paths] repo, live, python and db")]
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
                        "create one on the Ledgers tab's Replay study card (`oracle replay create`); it starts paused"),
            self._check("Cutoff probed", lambda: self._cutoff(ctx),
                        "Probe cutoff on the Ledgers tab's Replay study card (`oracle replay probe`)"),
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
        checks += self._power()
        return cfg, path, checks

    @staticmethod
    def _check(name: str, run: Callable[[], str], fix: str) -> Check:
        """`run` says what it found, or raises with what is wrong."""
        try:
            return Check(name, True, run())
        except Exception as exc:  # noqa: BLE001 (a broken check fails by name; it never arms)
            return Check(name, False, str(exc), fix)

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
        if wrong:
            raise HolidayError("; ".join(wrong))
        return f"{path}: app started from {cfg.live}, reports in {cfg.reports}"

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
        symbols = {p.symbol for p in s.listings}
        missing = [x for x in (cfg.arm_expect_symbols if cfg else ()) if x not in symbols]
        if missing:
            wrong.append(f"it lacks {', '.join(missing)} ([arm] expect_symbols): replace a listing before the "
                         "first replay, or create it again")
        if wrong:
            raise HolidayError(f"study {s.id}: " + "; ".join(wrong))
        return (f"study {s.id}, {s.state}: {len(s.listings)} listings ({halves.count(LARGE)} large, "
                f"{halves.count(MID_SMALL)} mid/small), {s.pending} replays to run, {s.done} done")

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
        return "oracle.toml matches what the app runs"

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

    def _power(self) -> list[Check]:
        try:
            sleep, plug = power_problems(self.system.power())
        except Exception as exc:  # noqa: BLE001
            sleep = plug = [f"could not read the power settings: {exc}"]
        return [Check("Sleep", not sleep, "; ".join(sleep) or "Never on mains power", SLEEP_FIX if sleep else ""),
                Check("Plugged in", not plug, "; ".join(plug) or "on mains power", "plug the PC in" if plug else "")]

    def _backup(self, ctx: AppContext, cfg: WatchConfig) -> str:
        now = ctx.clock.utcnow().astimezone(self.tz)
        dest = cfg.arm_backups / f"oracle-holiday-{now:%Y-%m-%d-%H%M%S}.db"
        self.system.backup(Path(ctx.config.db_path), dest)
        return str(dest)

    # Arming and undoing

    def _keep_remote_control(self, cfg: WatchConfig, lines: list[str]) -> None:
        """The one session the watch allows, in its PID file: the running one, else a new one."""
        found = self._sessions(cfg)
        if found:
            pid = found[0]
            lines.append(f"Remote Control session PID {pid} is the allowed one.")
        else:
            pid = self.system.launch(cfg.rc_launch, cfg.repo)
            if pid is None:
                raise HolidayError(f"could not start {' '.join(cfg.rc_launch)}")
            lines.append(f"Remote Control session started (PID {pid}) in a new console: check it shows its link.")
        if read_pid(cfg.rc_pid_file) != pid:
            write_pid(cfg.rc_pid_file, pid)

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
            (WATCHDOG_TASK, task_xml(args("pass"), python, self.code_root, start=now + FIRST_PASS_IN,
                                     repeat_min=cfg.arm_pass_every_min, user=user)),
            (REPORT_TASK, task_xml(args("report"), python, self.code_root, start=report_at, user=user)),
        ]

    def _undo(self, ctx: AppContext, reason: str) -> list[str]:
        lines = []
        for name in (WATCHDOG_TASK, REPORT_TASK):
            try:
                lines.append(f"Deleted task {name}." if self.system.delete_task(name)
                             else f"Task {name} was not registered.")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"Could not delete task {name}: {exc}")
        study = ReplayRepo(ctx.conn).current()
        if study is None:
            lines.append("No replay study.")
        elif study.state == PAUSED:
            lines.append(f"The replay study was already paused ({study.paused_reason or 'by hand'}).")
        else:
            pause_study(ctx, reason)
            lines.append("Paused the replay study.")
        return lines

    # Status

    def _armed(self) -> bool:
        return all(self._info(name) for name in (WATCHDOG_TASK, REPORT_TASK))

    def _info(self, name: str) -> str | None:
        try:
            return self.system.task_info(name)
        except Exception:  # noqa: BLE001 (unreadable counts as not armed)
            return None

    def _task_lines(self) -> list[str]:
        lines = []
        for name in (WATCHDOG_TASK, REPORT_TASK):
            info = self._info(name)
            lines.append(f"Task {name}: " + ("registered" if info else "not registered"))
            lines += [f"  {line}" for line in (info or "").splitlines() if line.strip()]
        return lines

    @staticmethod
    def _study_lines(ctx: AppContext) -> list[str]:
        s = study_status(ctx)
        if s is None:
            return ["No replay study."]
        why = f" ({s.paused_reason})" if s.state == PAUSED and s.paused_reason else ""
        return [f"Replay study {s.id}: {s.state}{why}, {s.done} of {s.total} done, {len(s.failed)} failed, "
                f"{s.pending} to run" + (f"; waiting: {s.waiting}" if s.waiting else "")]

    def _watch(self, cfg: WatchConfig, path: Path | None, args: tuple[str, ...]) -> list[str]:
        full = [*args, *(("--config", str(path)) if path else ())]
        head = f"$ python -m holiday_watch {' '.join(args)}"
        try:
            out = self.system.watch(full, cfg.python, self.code_root)
        except Exception as exc:  # noqa: BLE001 (the live check is for reading; it never undoes arming)
            return [head, f"  failed: {exc}"]
        return [head, *(f"  {line}" for line in out.splitlines())]


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
    """Minutes from `powercfg /query`'s AC line for one setting (it counts seconds)."""
    m = re.search(r"AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    return int(m.group(1), 16) // 60 if m else None


def task_xml(args: Sequence[str], command: Path, cwd: Path, *, start: datetime, repeat_min: int | None = None,
             user: str | None = None) -> str:
    """A Task Scheduler definition: every `repeat_min` from `start`, else daily at `start`'s time. Run only
    while the user is logged on (the Remote Control relaunch needs a visible console), on battery too,
    never stopped for running long (spawned children would end with it), one instance at a time."""
    when = f"<StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary><Enabled>true</Enabled>"
    trigger = (f"<TimeTrigger><Repetition><Interval>PT{repeat_min}M</Interval><StopAtDurationEnd>false"
               f"</StopAtDurationEnd></Repetition>{when}</TimeTrigger>" if repeat_min else
               f"<CalendarTrigger>{when}<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
               "</CalendarTrigger>")
    who = f"<UserId>{escape(user)}</UserId>" if user else ""
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
        "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>7</Priority></Settings>"
        f'<Actions Context="Author"><Exec><Command>{escape(str(command))}</Command>'
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


class RealHolidaySystem:
    """The PC itself: HTTP, the process list, powercfg, schtasks and the watch as a subprocess."""

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
        def ac_minutes(setting: str) -> int | None:
            return parse_powercfg(self._run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", setting])[1])

        return Power(ac_minutes("STANDBYIDLE"), ac_minutes("HIBERNATEIDLE"), _on_mains())

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
        if self.task_info(name) is None:
            return False
        code, out = self._run(["schtasks", "/Delete", "/TN", name, "/F"])
        if code != 0:
            raise HolidayError(f"schtasks could not delete {name}: {out.strip()}")
        return True

    def task_info(self, name: str) -> str | None:
        code, out = self._run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
        if code != 0:
            return None
        keep = ("Status:", "Next Run Time:", "Last Run Time:", "Last Result:")
        return "\n".join(line.strip() for line in out.splitlines() if line.strip().startswith(keep))

    def watch(self, args: Sequence[str], python: Path, cwd: Path) -> str:
        out = subprocess.run([str(python), "-m", "holiday_watch", *args], cwd=cwd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=WATCH_TIMEOUT_S, creationflags=NO_WINDOW)
        return (out.stdout + out.stderr).rstrip()

    def launch(self, args: Sequence[str], cwd: Path) -> int | None:
        return RealSystem().spawn(args, cwd, console=True)

    @staticmethod
    def _run(args: list[str]) -> tuple[int, str]:
        out = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=120,
                             creationflags=NO_WINDOW)
        return out.returncode, out.stdout + out.stderr


def _on_mains() -> bool | None:
    class Status(ctypes.Structure):
        _fields_ = [("ac", ctypes.c_ubyte), ("battery", ctypes.c_ubyte), ("percent", ctypes.c_ubyte),
                    ("saver", ctypes.c_ubyte), ("life", ctypes.c_ulong), ("full", ctypes.c_ulong)]

    status = Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    if status.ac in (0, 1):
        return bool(status.ac)
    return True if status.battery & 128 else None  # 128: no system battery, a desktop on mains


def _load_watch_config() -> tuple[WatchConfig, Path]:
    path = config_file()
    return load_watch_config(path), path


def real_holiday() -> Holiday:
    """The only way to the real seams; the test suite replaces it with one that refuses."""
    return Holiday(RealHolidaySystem(), _load_watch_config)
