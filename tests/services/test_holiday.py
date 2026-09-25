"""Arm, disarm and status of the holiday watch, over a fake system: nothing here calls schtasks,
reads power settings or the registry, lists processes, starts claude or backs up anything but a
temp database."""

import json
import os
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from holiday_watch.config import load as load_watch_config
from holiday_watch.procs import Proc
from oracle.config import AutomationConfig, ModelProfile
from oracle.services import holiday_service
from oracle.services.holiday_service import (
    REPORT_TASK, WATCHDOG_TASK, Holiday, HolidayError, Power, TaskInfo, Updates, parse_powercfg, power_problems,
    task_xml,
)
from oracle.services.replay_service import create_study, start_study, study_status
from oracle.store.universe import UniverseRepo
from tests.services.test_replay import FIRST, IT, NOW, SPREAD, ctx, equity, priced, yahoo  # noqa: F401

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
APP = "http://127.0.0.1:8740"
OLLAMA = "http://127.0.0.1:11434"
GB = 1024 ** 3
MID = equity("MIDC", "Mid Co", "United States", IT, "NMS", "USD", cap="Mid Cap")
RC_PID = 4242
FIX_PID = 6161


def claude(pid, cmd):
    return Proc(pid, 1, "claude.exe", cmd, datetime(2026, 9, 24, tzinfo=timezone.utc), 5.0)


@dataclass
class FakeSystem:
    answers: dict = field(default_factory=lambda: {f"{APP}/api/status": {"ok": True}, f"{OLLAMA}/api/tags": {}})
    procs: list = field(default_factory=lambda: [claude(RC_PID, "claude --remote-control holiday")])
    free: int = 100 * GB
    power_state: Power = Power(sleep_ac_min=0, hibernate_ac_min=0, on_ac=True)
    updates: Updates = Updates(restart_pending=False, paused_until=NOW + timedelta(days=10), auto_sign_in=True)
    tasks: dict = field(default_factory=dict)
    states: dict = field(default_factory=dict)
    refuse: set = field(default_factory=set)
    watched: list = field(default_factory=list)
    codes: dict = field(default_factory=dict)
    launched: list = field(default_factory=list)
    killed: list = field(default_factory=list)
    backups: list = field(default_factory=list)
    backup_error: str | None = None

    def get(self, url):
        answer = self.answers.get(url, HolidayError(f"{url}: connection refused"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def processes(self):
        return list(self.procs)

    def disk_free(self, path):
        return self.free

    def power(self):
        return self.power_state

    def windows_update(self):
        return self.updates

    def backup(self, db, dest):
        if self.backup_error:
            raise HolidayError(self.backup_error)
        self.backups.append((db, dest))

    def register_task(self, name, xml):
        if name in self.refuse:
            raise HolidayError(f"schtasks refused {name}")
        self.tasks[name] = xml

    def delete_task(self, name):
        return self.tasks.pop(name, None) is not None

    def task_info(self, name):
        if name not in self.tasks:
            return None
        return self.states.get(name, TaskInfo("Ready", None, NOW + timedelta(minutes=2), None))

    def watch(self, args, python, cwd):
        self.watched.append((tuple(args), python, cwd))
        return self.codes.get(args[0], 0), f"watch said: {' '.join(args)}"

    def launch(self, args, cwd):
        self.launched.append((tuple(args), cwd))
        return 777

    def kill(self, pid):
        self.killed.append(pid)
        return "SUCCESS"


@pytest.fixture
def system():
    return FakeSystem()


@pytest.fixture
def watch_file(tmp_path, ctx):  # noqa: F811
    (tmp_path / "live" / "holiday_watch").mkdir(parents=True)
    (tmp_path / "live" / "holiday_watch" / "__main__.py").write_text("", encoding="utf-8")
    (tmp_path / "py").mkdir()
    (tmp_path / "py" / "python.exe").write_bytes(b"")
    path = tmp_path / "watch.toml"
    path.write_text(
        f'[paths]\nrepo = "{tmp_path.as_posix()}"\nlive = "{{repo}}/live"\nreports = "{{repo}}/reports"\n'
        f'python = "{{repo}}/py/python.exe"\ndb = "{Path(ctx.config.db_path).as_posix()}"\n'
        f'[arm]\nexpect_symbols = ["AAPL"]\n', encoding="utf-8")
    return path


@pytest.fixture
def holiday(system, watch_file):
    return Holiday(system, lambda: (load_watch_config(watch_file), watch_file), tz=timezone.utc)


@pytest.fixture
async def study(ctx, yahoo):  # noqa: F811
    UniverseRepo(ctx.conn).replace_all([*SPREAD, MID])
    priced(yahoo, "MIDC")
    return await create_study(ctx, FIRST)


def failed(report):
    return {c.name: c for c in report.checks if not c.ok}


# Arm

async def test_arm_backs_up_starts_the_study_registers_both_tasks_and_shows_a_live_check(
        ctx, study, holiday, system, watch_file, tmp_path):  # noqa: F811
    report = holiday.arm(ctx)
    assert report.ok and report.armed, report.to_json()
    assert failed(report) == {} and report.warnings == ()
    assert study_status(ctx).state == "running"
    [(db, dest)] = system.backups
    assert db == Path(ctx.config.db_path)
    assert dest.parent == (tmp_path / ".." / "oracle-backups") and dest.name.startswith("oracle-holiday-")
    assert set(system.tasks) == {WATCHDOG_TASK, REPORT_TASK}
    assert [w[0] for w in system.watched] == [("pass", "--dry-run", "--config", str(watch_file)),
                                             ("status", "--config", str(watch_file))]
    assert {w[2] for w in system.watched} == {tmp_path / "live"}
    text = "\n".join(report.lines)
    assert "watch said: pass --dry-run" in text and "watch said: status" in text
    assert "Live check" in {c.name for c in report.checks}
    assert (tmp_path / "reports" / "remote-control.pid").read_text() == str(RC_PID)
    assert system.launched == [] and not (tmp_path / "reports" / "arm.lock").exists()


async def test_the_tasks_run_from_the_live_worktree_logged_on_with_time_limits(
        ctx, study, holiday, system, watch_file, tmp_path):  # noqa: F811
    holiday.arm(ctx)
    task = ET.fromstring(system.tasks[WATCHDOG_TASK])
    assert task.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", NS).text == "PT30M"
    assert task.find("t:Principals/t:Principal/t:LogonType", NS).text == "InteractiveToken"
    settings = task.find("t:Settings", NS)
    assert settings.find("t:ExecutionTimeLimit", NS).text == "PT20M"
    assert settings.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    assert settings.find("t:StopIfGoingOnBatteries", NS).text == "false"
    assert settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    exe = task.find("t:Actions/t:Exec", NS)
    assert exe.find("t:Command", NS).text == str(tmp_path / "py" / "python.exe")
    assert exe.find("t:Arguments", NS).text == f"-m holiday_watch pass --config {watch_file}"
    assert exe.find("t:WorkingDirectory", NS).text == str(tmp_path / "live")
    report = ET.fromstring(system.tasks[REPORT_TASK])
    assert report.find("t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval", NS).text == "1"
    assert report.find("t:Triggers/t:CalendarTrigger/t:StartBoundary", NS).text.endswith("T07:00:00")
    assert report.find("t:Settings/t:ExecutionTimeLimit", NS).text == "PT30M"
    assert report.find("t:Actions/t:Exec/t:Arguments", NS).text.startswith("-m holiday_watch report")


async def test_a_cli_arm_from_another_folder_runs_the_watch_from_the_live_worktree(
        ctx, study, system, watch_file, tmp_path, monkeypatch, capsys):  # noqa: F811
    from oracle.cli.main import main

    toml = tmp_path / "oracle.toml"
    toml.write_text(f'db_path = "{Path(ctx.config.db_path).as_posix()}"\n', encoding="utf-8")
    system.updates = replace(system.updates, paused_until=datetime.now(timezone.utc) + timedelta(days=30))
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    relative = Path("..") / "watch.toml"
    monkeypatch.setattr(holiday_service, "real_holiday",
                        lambda: Holiday(system, lambda: (load_watch_config(relative), relative)))
    assert main(["--config", str(toml), "holiday", "arm"]) == 0, capsys.readouterr().out
    exe = ET.fromstring(system.tasks[WATCHDOG_TASK]).find("t:Actions/t:Exec", NS)
    assert exe.find("t:WorkingDirectory", NS).text == str(tmp_path / "live")
    assert exe.find("t:Arguments", NS).text == f"-m holiday_watch pass --config {watch_file.resolve()}"
    assert {w[2] for w in system.watched} == {tmp_path / "live"}


async def test_a_relative_settings_path_reaches_the_task_absolute(
        ctx, study, system, watch_file, tmp_path, monkeypatch):  # noqa: F811
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    relative = Path("..") / "watch.toml"
    Holiday(system, lambda: (load_watch_config(relative), relative), tz=timezone.utc).arm(ctx)
    args = ET.fromstring(system.tasks[WATCHDOG_TASK]).find("t:Actions/t:Exec/t:Arguments", NS).text
    assert args == f"-m holiday_watch pass --config {watch_file.resolve()}"


async def test_the_windowless_python_runs_the_tasks_when_it_sits_beside_the_configured_one(
        ctx, study, holiday, system, tmp_path):  # noqa: F811
    (tmp_path / "py" / "pythonw.exe").write_bytes(b"")
    holiday.arm(ctx)
    exe = ET.fromstring(system.tasks[WATCHDOG_TASK]).find("t:Actions/t:Exec/t:Command", NS)
    assert exe.text == str(tmp_path / "py" / "pythonw.exe")


async def test_with_no_remote_control_session_arm_starts_one_and_records_it(
        ctx, study, holiday, system, tmp_path):  # noqa: F811
    system.procs = [claude(9, "claude -p hi")]
    report = holiday.arm(ctx)
    assert report.ok
    assert system.launched == [(("claude", "--remote-control", "holiday"), tmp_path)]
    assert (tmp_path / "reports" / "remote-control.pid").read_text() == "777"
    assert any("Remote Control session started (PID 777)" in line for line in report.lines)


def _app_down(ctx, system):
    system.answers.pop(f"{APP}/api/status")


def _ollama_down(ctx, system):
    system.answers.pop(f"{OLLAMA}/api/tags")


def _automation_off(ctx, system):
    ctx.config = replace(ctx.config, automation=AutomationConfig(False))


def _study_running(ctx, system):
    start_study(ctx)


def _half_missing(ctx, system):
    ctx.conn.execute("UPDATE replay_studies SET listings = REPLACE(listings, 'mid-small', 'large')")


def _unprobed(ctx, system):
    ctx.conn.execute("DELETE FROM cutoff_probes")


def _models_changed(ctx, system):
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model")},
                         model_slots={"verdict": "big"})


def _two_remote_controls(ctx, system):
    system.procs.append(claude(5151, "claude --remote-control other"))


def _low_disk(ctx, system):
    system.free = 3 * GB


def _sleeps(ctx, system):
    system.power_state = Power(sleep_ac_min=30, hibernate_ac_min=0, on_ac=True)


def _battery(ctx, system):
    system.power_state = Power(sleep_ac_min=0, hibernate_ac_min=0, on_ac=False)


def _power_unreadable(ctx, system):
    def broken():
        raise OSError("powercfg missing")
    system.power = broken


def _lid_sleeps(ctx, system):
    system.power_state = Power(0, 0, True, battery=True, lid_ac=1)


def _restart_pending(ctx, system):
    system.updates = replace(system.updates, restart_pending=True)


def _updates_not_paused(ctx, system):
    system.updates = replace(system.updates, paused_until=None)


def _updates_resume_too_soon(ctx, system):
    system.updates = replace(system.updates, paused_until=NOW + timedelta(days=3))


@pytest.mark.parametrize("break_it, check", [
    (_app_down, "App"), (_ollama_down, "Ollama"), (_automation_off, "Automation"),
    (_study_running, "Replay study"), (_half_missing, "Replay study"),
    (_unprobed, "Cutoff probed"), (_models_changed, "Replay models"),
    (_two_remote_controls, "Remote Control"), (_low_disk, "Disk space"), (_sleeps, "Sleep"),
    (_battery, "Plugged in"), (_power_unreadable, "Sleep"), (_lid_sleeps, "Lid"),
    (_restart_pending, "Windows Update"), (_updates_not_paused, "Windows Update"),
    (_updates_resume_too_soon, "Windows Update"),
])
async def test_any_failed_check_refuses_to_arm_naming_it_and_how_to_fix_it(
        ctx, study, holiday, system, break_it, check):  # noqa: F811
    break_it(ctx, system)
    was = study_status(ctx).state
    report = holiday.arm(ctx)
    assert not report.ok and not report.armed
    assert check in failed(report), report.to_json()
    assert failed(report)[check].fix
    assert system.tasks == {} and system.backups == [] and system.launched == [] and system.watched == []
    assert study_status(ctx).state == was
    assert "Not armed" in report.lines[0]


async def test_windows_update_fixes_name_what_to_do(ctx, study, holiday, system):  # noqa: F811
    _restart_pending(ctx, system)
    assert "restart" in failed(holiday.arm(ctx))["Windows Update"].fix.lower()
    system.updates = replace(system.updates, restart_pending=False, paused_until=None)
    assert "Pause updates" in failed(holiday.arm(ctx))["Windows Update"].fix


async def test_the_lid_matters_only_with_a_battery(ctx, study, holiday, system):  # noqa: F811
    system.power_state = Power(0, 0, True, battery=False, lid_ac=1)
    assert "Lid" not in {c.name for c in holiday.status(ctx).checks}
    system.power_state = Power(0, 0, True, battery=True, lid_ac=0)
    assert "Lid" not in failed(holiday.arm(ctx))


@pytest.mark.parametrize("sign_in, says", [(False, "is off"), (None, "could not tell")])
async def test_auto_sign_in_after_updates_off_or_unknown_is_a_warning_not_a_refusal(
        ctx, study, holiday, system, sign_in, says):  # noqa: F811
    system.updates = replace(system.updates, auto_sign_in=sign_in)
    report = holiday.arm(ctx)
    assert report.ok
    assert any(says in w and "Sign-in options" in w for w in report.warnings)


async def test_no_study_refuses(ctx, holiday, system):  # noqa: F811
    report = holiday.arm(ctx)
    assert "Replay study" in failed(report) and "create" in failed(report)["Replay study"].fix.lower()
    assert system.tasks == {}


async def test_a_study_without_an_expected_listing_refuses(ctx, study, holiday, watch_file):  # noqa: F811
    watch_file.write_text(watch_file.read_text(encoding="utf-8").replace('"AAPL"', '"NOPE"'), encoding="utf-8")
    assert "lacks NOPE" in failed(holiday.arm(ctx))["Replay study"].detail


async def test_the_study_check_names_its_listings(ctx, study, holiday):  # noqa: F811
    detail = next(c for c in holiday.status(ctx).checks if c.name == "Replay study").detail
    assert "AAPL" in detail and "MIDC" in detail


async def test_missing_watch_settings_refuse(ctx, study, holiday, watch_file):  # noqa: F811
    watch_file.unlink()
    report = holiday.arm(ctx)
    assert "config.example.toml" in failed(report)["Watch settings"].fix


async def test_watch_settings_on_another_database_refuse(ctx, study, holiday, watch_file):  # noqa: F811
    watch_file.write_text(watch_file.read_text(encoding="utf-8").replace("o.db", "other.db"), encoding="utf-8")
    assert "other.db" in failed(holiday.arm(ctx))["Watch settings"].detail


async def test_a_live_worktree_without_the_watch_refuses(ctx, study, holiday, tmp_path):  # noqa: F811
    (tmp_path / "live" / "holiday_watch" / "__main__.py").unlink()
    assert "holiday_watch" in failed(holiday.arm(ctx))["Watch settings"].detail


async def test_bad_watch_settings_values_refuse_before_any_backup(ctx, study, holiday, watch_file, system):  # noqa: F811
    watch_file.write_text(watch_file.read_text(encoding="utf-8") + 'report_time = "7am"\n', encoding="utf-8")
    report = holiday.arm(ctx)
    assert "report_time" in failed(report)["Watch settings"].detail
    assert system.backups == [] and study_status(ctx).state == "paused"


async def test_a_pending_model_change_in_oracle_toml_refuses(ctx, study, holiday, tmp_path):  # noqa: F811
    toml = tmp_path / "oracle.toml"
    toml.write_text(f'db_path = "{Path(ctx.config.db_path).as_posix()}"\n', encoding="utf-8")
    ctx.config_source = toml
    (tmp_path / "watch.toml").write_text((tmp_path / "watch.toml").read_text(encoding="utf-8").replace(
        "[arm]", f'oracle_config = "{toml.as_posix()}"\n[arm]'), encoding="utf-8")
    ctx.config = replace(ctx.config, models={**ctx.config.models, "spare": ModelProfile("ollama", "spare-model")})
    assert "pending" in failed(holiday.arm(ctx))["Model settings"].detail


async def test_a_failed_backup_refuses_before_anything_starts(ctx, study, holiday, system):  # noqa: F811
    system.backup_error = "disk full"
    report = holiday.arm(ctx)
    assert not report.ok and "disk full" in failed(report)["DB backup"].detail
    assert system.tasks == {} and study_status(ctx).state == "paused"


async def test_a_refused_registration_undoes_the_rest(ctx, study, holiday, system):  # noqa: F811
    system.refuse = {REPORT_TASK}
    report = holiday.arm(ctx)
    assert not report.ok and not report.armed
    assert system.tasks == {}
    assert study_status(ctx).state == "paused"
    assert any("schtasks refused" in line for line in report.lines)


async def test_a_rollback_closes_the_remote_control_session_arm_started(ctx, study, holiday, system, tmp_path):  # noqa: F811
    system.procs = []
    system.refuse = {WATCHDOG_TASK}
    report = holiday.arm(ctx)
    assert not report.ok and system.killed == [777]
    assert not (tmp_path / "reports" / "remote-control.pid").exists()
    assert any("Closed the Remote Control session arming started (PID 777)" in line for line in report.lines)


async def test_a_session_already_running_survives_a_rollback(ctx, study, holiday, system, tmp_path):  # noqa: F811
    system.refuse = {WATCHDOG_TASK}
    holiday.arm(ctx)
    assert system.killed == []


async def test_a_failing_live_check_fails_arming_and_undoes_it(ctx, study, holiday, system):  # noqa: F811
    system.codes = {"pass": 1}
    report = holiday.arm(ctx)
    assert not report.ok and not report.armed
    assert "watch said: pass --dry-run" in failed(report)["Live check"].detail
    assert failed(report)["Live check"].fix
    assert system.tasks == {} and study_status(ctx).state == "paused"


async def test_a_live_check_that_cannot_run_fails_too(ctx, study, holiday, system):  # noqa: F811
    def broken(args, python, cwd):
        raise OSError("no python")
    system.watch = broken
    report = holiday.arm(ctx)
    assert not report.ok and "no python" in failed(report)["Live check"].detail and system.tasks == {}


async def test_a_bad_report_time_refuses_before_the_study_starts(ctx, study, holiday, watch_file, system):  # noqa: F811
    watch_file.write_text(watch_file.read_text(encoding="utf-8") + 'report_time = "7am"\n', encoding="utf-8")
    report = holiday.arm(ctx)
    assert not report.ok and system.tasks == {} and system.launched == []
    assert study_status(ctx).state == "paused" and study_status(ctx).paused_reason is None


async def test_a_check_that_breaks_fails_by_name_instead_of_crashing(ctx, study, holiday, system):  # noqa: F811
    def broken(path):
        raise OSError("no drive")
    system.disk_free = broken
    assert "no drive" in failed(holiday.arm(ctx))["Disk space"].detail


async def test_a_second_arm_while_one_runs_is_refused(ctx, study, holiday, system, tmp_path):  # noqa: F811
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "arm.lock").write_text("123", encoding="utf-8")
    report = holiday.arm(ctx)
    assert not report.ok and "already running" in report.lines[0]
    assert system.backups == [] and system.tasks == {}
    assert holiday.disarm(ctx).ok is False


async def test_a_stale_arm_lock_is_taken_over(ctx, study, holiday, tmp_path):  # noqa: F811
    lock = tmp_path / "reports" / "arm.lock"
    lock.parent.mkdir()
    lock.write_text("123", encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert holiday.arm(ctx).ok and not lock.exists()


# Disarm and status

async def test_disarm_pauses_the_study_and_deletes_both_tasks(ctx, study, holiday, system):  # noqa: F811
    holiday.arm(ctx)
    report = holiday.disarm(ctx)
    assert report.ok and not report.armed
    assert system.tasks == {}
    s = study_status(ctx)
    assert s.state == "paused" and s.paused_reason == "holiday disarmed"
    text = "\n".join(report.lines)
    assert f"Deleted task {WATCHDOG_TASK}" in text and f"Deleted task {REPORT_TASK}" in text
    assert "Paused the replay study" in text


async def test_disarm_ends_a_running_fix_run_before_pausing(ctx, study, holiday, system, tmp_path):  # noqa: F811
    holiday.arm(ctx)
    system.procs.append(Proc(FIX_PID, 1, "python.exe", "python -m holiday_watch fix-run t.json", None, 1.0))
    (tmp_path / "reports" / "fixrun.lock").write_text(
        json.dumps({"pid": FIX_PID, "started_at": "2026-09-24T11:50:00+00:00", "trigger_file": "t.json"}),
        encoding="utf-8")
    report = holiday.disarm(ctx)
    assert report.ok and system.killed == [FIX_PID]
    assert any(f"Ended the fix run PID {FIX_PID}" in line for line in report.lines)
    assert study_status(ctx).state == "paused"


async def test_disarm_leaves_a_gone_fix_runs_lock_alone(ctx, study, holiday, system, tmp_path):  # noqa: F811
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "fixrun.lock").write_text(
        json.dumps({"pid": FIX_PID, "started_at": "2026-09-24T11:50:00+00:00"}), encoding="utf-8")
    assert holiday.disarm(ctx).ok and system.killed == []


async def test_disarm_with_nothing_armed_says_so(ctx, holiday, system):  # noqa: F811
    report = holiday.disarm(ctx)
    assert report.ok
    text = "\n".join(report.lines)
    assert f"Task {WATCHDOG_TASK} was not registered" in text and "No replay study" in text


async def test_a_disarm_step_that_fails_is_named_and_the_rest_still_run(ctx, study, holiday, system):  # noqa: F811
    holiday.arm(ctx)

    def stuck(name):
        raise HolidayError("access denied")
    system.delete_task = stuck
    report = holiday.disarm(ctx)
    assert not report.ok and report.lines[0] == "Disarm incomplete."
    assert f"Could not delete task {WATCHDOG_TASK}: access denied" in report.lines
    assert study_status(ctx).state == "paused"


async def test_status_runs_the_checks_without_side_effects(ctx, study, holiday, system, watch_file):  # noqa: F811
    report = holiday.status(ctx)
    assert not report.armed and report.ok
    assert "DB backup" not in {c.name for c in report.checks}
    assert system.backups == [] and system.tasks == {} and system.launched == []
    assert study_status(ctx).state == "paused"
    assert [w[0] for w in system.watched] == [("status", "--config", str(watch_file))]
    holiday.arm(ctx)
    report = holiday.status(ctx)
    assert report.armed
    assert any(line.startswith(f"Task {WATCHDOG_TASK}: Ready") for line in report.lines)
    assert "Replay study" not in failed(report)


async def test_status_warns_of_a_task_running_too_long(ctx, study, holiday, system):  # noqa: F811
    holiday.arm(ctx)
    system.states[WATCHDOG_TASK] = TaskInfo("Running", NOW - timedelta(minutes=26), None, None)
    assert any(WATCHDOG_TASK in w and "Running for 26 min" in w for w in holiday.status(ctx).warnings)
    system.states[WATCHDOG_TASK] = TaskInfo("Running", NOW - timedelta(minutes=5), None, None)
    assert holiday.status(ctx).warnings == ()


# Pure parts

def test_powercfg_output_gives_the_ac_index():
    out = ("Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)\n"
           "    Current AC Power Setting Index: 0x00000000\n"
           "    Current DC Power Setting Index: 0x00000384\n")
    assert parse_powercfg(out) == 0
    assert parse_powercfg(out.replace("0x00000000", "0x00000708")) == 1800
    assert parse_powercfg("garbage") is None


def test_power_problems_name_each_setting():
    assert power_problems(Power(0, 0, True)) == ([], [])
    sleep, plug = power_problems(Power(30, None, None))
    assert any("sleeps after 30 min" in s for s in sleep) and any("hibernate" in s for s in sleep)
    assert plug and "could not tell" in plug[0]


def test_task_xml_escapes_and_quotes_paths():
    xml = task_xml(["-m", "holiday_watch", "pass", "--config", "C:/a & b/c.toml"], Path("C:/my py/python.exe"),
                   Path("C:/code"), start=datetime(2026, 9, 25, 18, 2), limit_min=20, repeat_min=30, user="PC\\me")
    exe = ET.fromstring(xml).find("t:Actions/t:Exec", NS)
    assert exe.find("t:Arguments", NS).text == '-m holiday_watch pass --config "C:/a & b/c.toml"'
    assert exe.find("t:Command", NS).text == f'"{Path("C:/my py/python.exe")}"'


def test_the_real_seams_are_off_limits_to_tests():
    with pytest.raises(AssertionError, match="real holiday seams"):
        holiday_service.real_holiday()
    with pytest.raises(AssertionError, match="real holiday seams"):
        holiday_service.RealHolidaySystem()


def test_a_real_backup_copies_a_database_and_checks_it(tmp_path):
    src = tmp_path / "src.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    dest = tmp_path / "bk" / "copy.db"
    holiday_service.backup_db(src, dest)
    assert sqlite3.connect(dest).execute("SELECT x FROM t").fetchall() == [(1,)]
