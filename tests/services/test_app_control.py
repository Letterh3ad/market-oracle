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
