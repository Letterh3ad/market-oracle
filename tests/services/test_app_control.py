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


def test_start_at_login_writes_the_launcher_removes_it_and_reads_its_state_from_the_file(tmp_path):
    from oracle.services.app_service import Autostart

    written = []

    def write(link, cmd, description):
        written.append((link.name, cmd))
        link.write_text("lnk")

    autostart = Autostart(tmp_path, CMD, write=write)
    assert not autostart.enabled()
    autostart.set(True)
    assert autostart.enabled() and written == [("Market Oracle.lnk", CMD)]
    (tmp_path / "Market Oracle.lnk").unlink()  # the user deletes it by hand
    assert not autostart.enabled()
    autostart.set(True)
    autostart.set(False)
    autostart.set(False)
    assert not autostart.enabled()


def test_start_at_login_without_a_startup_folder_says_so():
    from oracle.services.app_service import Autostart
    from oracle.services.errors import ServiceError

    with pytest.raises(ServiceError, match="Windows"):
        Autostart(None, CMD).set(True)


def test_the_launch_command_runs_the_code_this_server_was_loaded_from(tmp_path):
    from oracle.config import Config
    from oracle.services.app_service import server_launch
    from oracle.services.context import open_context
    from oracle.system.version import CODE_ROOT

    cfg = tmp_path / "oracle.toml"
    cfg.write_text("", encoding="utf-8")
    with open_context(Config(db_path=tmp_path / "o.db"), cfg) as ctx:
        cmd = server_launch(ctx)
    assert cmd.cwd == CODE_ROOT and str(cfg.resolve()) in cmd.argv
