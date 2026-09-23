from pathlib import Path

import pytest

from oracle.services.app_service import AppControl
from oracle.services.errors import CannotRestart
from oracle.system.launch import LaunchCommand

CMD = LaunchCommand(("python", "-m", "oracle", "gui"), Path("."))


def test_restart_starts_the_new_copy_then_asks_this_one_to_stop():
    events = []
    control = AppControl(CMD, spawn=lambda c: events.append(("spawn", c)))
    control.on_exit = lambda: events.append("exit")
    control.restart()
    assert events == [("spawn", CMD), "exit"]


def test_a_copy_that_cannot_start_leaves_this_one_running():
    def broken(cmd):
        raise OSError("no such interpreter")

    exited = []
    control = AppControl(CMD, spawn=broken)
    control.on_exit = lambda: exited.append(True)
    with pytest.raises(CannotRestart, match="no such interpreter"):
        control.restart()
    assert exited == []


def test_a_server_with_no_way_to_stop_itself_refuses_before_starting_a_copy():
    spawned = []
    control = AppControl(CMD, spawn=spawned.append)
    with pytest.raises(CannotRestart, match="cannot stop itself"):
        control.restart()
    assert spawned == []


def test_the_version_on_disk_is_read_at_most_once_a_minute():
    versions, clock = iter(["abc1234", "abc1234", "def5678"]), [0.0]
    control = AppControl(CMD, spawn=lambda c: None, read_version=lambda: next(versions), now=lambda: clock[0])
    assert control.version == "abc1234"
    assert control.version_on_disk() == "abc1234"
    clock[0] = 59
    assert control.version_on_disk() == "abc1234"
    clock[0] = 61
    assert control.version_on_disk() == "def5678" and control.version == "abc1234"
