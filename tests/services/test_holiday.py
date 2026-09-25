"""Arm, disarm and status of the holiday watch, over a fake system: nothing here calls schtasks,
reads power settings, lists processes, starts claude or backs up anything but a temp database."""

import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from holiday_watch.config import load as load_watch_config
from holiday_watch.procs import Proc
from oracle.config import AutomationConfig, ModelProfile
from oracle.services import holiday_service
from oracle.services.holiday_service import (
    REPORT_TASK, WATCHDOG_TASK, Holiday, HolidayError, Power, parse_powercfg, power_problems, task_xml,
)
from oracle.services.replay_service import create_study, start_study, study_status
from oracle.store.universe import UniverseRepo
from tests.services.test_replay import FIRST, IT, SPREAD, ctx, equity, priced, yahoo  # noqa: F401

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
APP = "http://127.0.0.1:8740"
OLLAMA = "http://127.0.0.1:11434"
GB = 1024 ** 3
MID = equity("MIDC", "Mid Co", "United States", IT, "NMS", "USD", cap="Mid Cap")
RC_PID = 4242


def claude(pid, cmd):
    return Proc(pid, 1, "claude.exe", cmd, datetime(2026, 9, 24, tzinfo=timezone.utc), 5.0)


@dataclass
class FakeSystem:
    answers: dict = field(default_factory=lambda: {f"{APP}/api/status": {"ok": True}, f"{OLLAMA}/api/tags": {}})
    procs: list = field(default_factory=lambda: [claude(RC_PID, "claude --remote-control holiday")])
    free: int = 100 * GB
    power_state: Power = Power(sleep_ac_min=0, hibernate_ac_min=0, on_ac=True)
    tasks: dict = field(default_factory=dict)
    refuse: set = field(default_factory=set)
    watched: list = field(default_factory=list)
    launched: list = field(default_factory=list)
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
        return f"Next Run Time: soon ({name})" if name in self.tasks else None

    def watch(self, args, python, cwd):
        self.watched.append((tuple(args), python, cwd))
        return f"watch said: {' '.join(args)}"

    def launch(self, args, cwd):
        self.launched.append((tuple(args), cwd))
        return 777


@pytest.fixture
def system():
    return FakeSystem()


@pytest.fixture
def watch_file(tmp_path, ctx):  # noqa: F811
    (tmp_path / "live").mkdir()
    (tmp_path / "py").mkdir()
    (tmp_path / "py" / "python.exe").write_bytes(b"")
    path = tmp_path / "watch.toml"
    path.write_text(
        f'[paths]\nrepo = "{tmp_path.as_posix()}"\nlive = "{{repo}}/live"\nreports = "{{repo}}/reports"\n'
        f'python = "{{repo}}/py/python.exe"\ndb = "{Path(ctx.config.db_path).as_posix()}"\n'
        f'[arm]\nexpect_symbols = ["AAPL"]\n', encoding="utf-8")
    return path


@pytest.fixture
def holiday(system, watch_file, tmp_path):
    return Holiday(system, lambda: (load_watch_config(watch_file), watch_file), code_root=tmp_path / "code",
                   tz=timezone.utc)


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
    assert failed(report) == {}
    assert study_status(ctx).state == "running"
    [(db, dest)] = system.backups
    assert db == Path(ctx.config.db_path)
    assert dest.parent == (tmp_path / ".." / "oracle-backups") and dest.name.startswith("oracle-holiday-")
    assert set(system.tasks) == {WATCHDOG_TASK, REPORT_TASK}
    assert [w[0] for w in system.watched] == [("pass", "--dry-run", "--config", str(watch_file)),
                                             ("status", "--config", str(watch_file))]
    text = "\n".join(report.lines)
    assert "watch said: pass --dry-run" in text and "watch said: status" in text
    assert (tmp_path / "reports" / "remote-control.pid").read_text() == str(RC_PID)
    assert system.launched == []


async def test_the_watchdog_task_repeats_every_30_min_logged_on_with_no_time_limit(
        ctx, study, holiday, system, watch_file, tmp_path):  # noqa: F811
    holiday.arm(ctx)
    task = ET.fromstring(system.tasks[WATCHDOG_TASK])
    assert task.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", NS).text == "PT30M"
    assert task.find("t:Principals/t:Principal/t:LogonType", NS).text == "InteractiveToken"
    settings = task.find("t:Settings", NS)
    assert settings.find("t:ExecutionTimeLimit", NS).text == "PT0S"
    assert settings.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    assert settings.find("t:StopIfGoingOnBatteries", NS).text == "false"
    assert settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    exe = task.find("t:Actions/t:Exec", NS)
    assert exe.find("t:Command", NS).text == str(tmp_path / "py" / "python.exe")
    assert exe.find("t:Arguments", NS).text == f"-m holiday_watch pass --config {watch_file}"
    assert exe.find("t:WorkingDirectory", NS).text == str(tmp_path / "code")
    report = ET.fromstring(system.tasks[REPORT_TASK])
    assert report.find("t:Triggers/t:CalendarTrigger/t:ScheduleByDay/t:DaysInterval", NS).text == "1"
    assert report.find("t:Triggers/t:CalendarTrigger/t:StartBoundary", NS).text.endswith("T07:00:00")
    assert report.find("t:Settings/t:ExecutionTimeLimit", NS).text == "PT0S"
    assert report.find("t:Actions/t:Exec/t:Arguments", NS).text.startswith("-m holiday_watch report")


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


@pytest.mark.parametrize("break_it, check", [
    (_app_down, "App"), (_ollama_down, "Ollama"), (_automation_off, "Automation"),
    (_study_running, "Replay study"), (_half_missing, "Replay study"),
    (_unprobed, "Cutoff probed"), (_models_changed, "Replay models"),
    (_two_remote_controls, "Remote Control"), (_low_disk, "Disk space"), (_sleeps, "Sleep"),
    (_battery, "Plugged in"), (_power_unreadable, "Sleep"),
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


async def test_no_study_refuses(ctx, holiday, system):  # noqa: F811
    report = holiday.arm(ctx)
    assert "Replay study" in failed(report) and "create" in failed(report)["Replay study"].fix.lower()
    assert system.tasks == {}


async def test_a_study_without_an_expected_listing_refuses(ctx, study, holiday, watch_file):  # noqa: F811
    watch_file.write_text(watch_file.read_text(encoding="utf-8").replace('"AAPL"', '"NOPE"'), encoding="utf-8")
    assert "lacks NOPE" in failed(holiday.arm(ctx))["Replay study"].detail


async def test_missing_watch_settings_refuse(ctx, study, holiday, watch_file):  # noqa: F811
    watch_file.unlink()
    report = holiday.arm(ctx)
    assert "config.example.toml" in failed(report)["Watch settings"].fix


async def test_watch_settings_on_another_database_refuse(ctx, study, holiday, watch_file):  # noqa: F811
    watch_file.write_text(watch_file.read_text(encoding="utf-8").replace("o.db", "other.db"), encoding="utf-8")
    assert "other.db" in failed(holiday.arm(ctx))["Watch settings"].detail


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


async def test_a_check_that_breaks_fails_by_name_instead_of_crashing(ctx, study, holiday, system):  # noqa: F811
    def broken(path):
        raise OSError("no drive")
    system.disk_free = broken
    assert "no drive" in failed(holiday.arm(ctx))["Disk space"].detail


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


async def test_disarm_with_nothing_armed_says_so(ctx, holiday, system):  # noqa: F811
    report = holiday.disarm(ctx)
    assert report.ok
    text = "\n".join(report.lines)
    assert f"Task {WATCHDOG_TASK} was not registered" in text and "No replay study" in text


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
    assert any(f"Next Run Time: soon ({WATCHDOG_TASK})" in line for line in report.lines)
    # Armed, the study runs: that is no failure for status.
    assert "Replay study" not in failed(report)


# Pure parts

def test_powercfg_output_gives_the_ac_minutes():
    out = ("Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)\n"
           "    Current AC Power Setting Index: 0x00000000\n"
           "    Current DC Power Setting Index: 0x00000384\n")
    assert parse_powercfg(out) == 0
    assert parse_powercfg(out.replace("0x00000000", "0x00000708")) == 30
    assert parse_powercfg("garbage") is None


def test_power_problems_name_each_setting():
    assert power_problems(Power(0, 0, True)) == ([], [])
    sleep, plug = power_problems(Power(30, None, None))
    assert any("sleeps after 30 min" in s for s in sleep) and any("hibernate" in s for s in sleep)
    assert plug and "could not tell" in plug[0]


def test_task_xml_escapes_paths():
    xml = task_xml(["-m", "holiday_watch", "pass", "--config", "C:/a & b/c.toml"], Path("C:/py/python.exe"),
                   Path("C:/code"), repeat_min=30, start=datetime(2026, 9, 25, 18, 2), user="PC\\me")
    exe = ET.fromstring(xml).find("t:Actions/t:Exec/t:Arguments", NS)
    assert exe.text == '-m holiday_watch pass --config "C:/a & b/c.toml"'


def test_the_real_seams_are_off_limits_to_tests():
    with pytest.raises(AssertionError, match="real holiday seams"):
        holiday_service.real_holiday()


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
