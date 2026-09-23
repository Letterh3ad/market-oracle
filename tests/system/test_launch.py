import sys
import time

from oracle.system.launch import LaunchCommand, server_command, spawn_detached


def test_the_server_command_names_the_interpreter_config_and_folder(tmp_path):
    cmd = server_command(tmp_path / "oracle.toml", tmp_path)
    config = str((tmp_path / "oracle.toml").resolve())
    assert cmd == LaunchCommand((sys.executable, "-m", "oracle", "--config", config, "gui"), tmp_path)


def test_without_a_config_file_the_folder_supplies_the_defaults(tmp_path):
    assert server_command(None, tmp_path).argv == (sys.executable, "-m", "oracle", "gui")


def test_a_detached_process_runs_in_its_folder(tmp_path):
    marker = tmp_path / "ran"
    spawn_detached(LaunchCommand((sys.executable, "-c", "open('ran', 'w').write('yes')"), tmp_path))
    deadline = time.monotonic() + 15
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert marker.read_text() == "yes"
