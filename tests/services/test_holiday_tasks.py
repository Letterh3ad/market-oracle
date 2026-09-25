"""The real system's task calls over a scripted `_run`: nothing here starts schtasks or PowerShell."""

import json

import pytest

from oracle.services.holiday_service import REPORT_TASK, WATCHDOG_TASK, Holiday, HolidayError, RealHolidaySystem
from tests.services.test_replay import ctx, yahoo  # noqa: F401


class Scripted:
    """Answers schtasks and PowerShell by the first word and schtasks' verb; records each call."""

    def __init__(self, registered=(WATCHDOG_TASK, REPORT_TASK), powershell_works=True, delete_works=True):
        self.registered = set(registered)
        self.powershell_works, self.delete_works = powershell_works, delete_works
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "powershell":
            if not self.powershell_works:
                return 1, "Get-ScheduledTask : The term is not recognized"
            return 0, json.dumps({"state": "Ready", "last": "2026-09-25T17:02:00.1234567Z", "next": None,
                                  "result": 0})
        verb, name = args[1], args[3]
        if verb == "/Query":
            return (0, "TaskName ...") if name in self.registered else (1, "ERROR: The system cannot find the file")
        if verb == "/Delete":
            if not self.delete_works:
                return 1, "ERROR: Access is denied."
            self.registered.discard(name)
            return 0, "SUCCESS"
        raise AssertionError(args)


@pytest.fixture
def scripted():
    return Scripted()


@pytest.fixture
def real(scripted):
    system = object.__new__(RealHolidaySystem)  # the suite's guard blocks __init__; _run is scripted
    system._run = scripted
    return system


def test_existence_comes_from_schtasks_and_details_from_powershell(real, scripted):
    info = real.task_info(WATCHDOG_TASK)
    assert info.state == "Ready" and info.last_result == 0 and info.last_run.minute == 2
    assert [c[0] for c in scripted.calls] == ["schtasks", "powershell"]
    scripted.registered.clear()
    assert real.task_info(WATCHDOG_TASK) is None


def test_a_failing_details_call_still_reads_as_registered(real, scripted):
    scripted.powershell_works = False
    info = real.task_info(WATCHDOG_TASK)
    assert info is not None and "details unavailable" in info.state


def test_delete_asks_schtasks_not_powershell(real, scripted):
    scripted.powershell_works = False
    assert real.delete_task(WATCHDOG_TASK) is True
    assert WATCHDOG_TASK not in scripted.registered
    assert all(c[0] == "schtasks" for c in scripted.calls)
    assert real.delete_task(WATCHDOG_TASK) is False


def test_a_refused_delete_raises(real, scripted):
    scripted.delete_works = False
    with pytest.raises(HolidayError, match="Access is denied"):
        real.delete_task(WATCHDOG_TASK)


def test_with_powershell_broken_status_says_armed_and_disarm_deletes_both(ctx, real, scripted, tmp_path):
    holiday = Holiday(real, lambda: (_ for _ in ()).throw(ValueError("no watch settings here")))
    scripted.powershell_works = False
    assert holiday._armed()
    assert any("details unavailable" in line for line in holiday._task_lines())
    report = holiday.disarm(ctx)
    assert report.ok and scripted.registered == set()
    assert f"Deleted task {WATCHDOG_TASK}." in report.lines and f"Deleted task {REPORT_TASK}." in report.lines
    assert not holiday._armed()
