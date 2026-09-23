import os
import subprocess
import sys

import pytest

from oracle.system.launch import LaunchCommand
from oracle.system.shortcuts import write_shortcut

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows shortcuts")


def read_shortcut(link):
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
              "\"$($s.TargetPath)`n$($s.Arguments)`n$($s.WorkingDirectory)`n$($s.WindowStyle)\"")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                         capture_output=True, text=True, env={**os.environ, "LNK": str(link)}).stdout
    return out.splitlines()


def test_a_shortcut_starts_the_command_minimised_in_its_folder(tmp_path):
    folder = tmp_path / "with space"
    folder.mkdir()
    cmd = LaunchCommand((sys.executable, "-m", "oracle", "--config", r"C:\my files\oracle.toml", "gui"), folder)
    link = tmp_path / "Market Oracle.lnk"
    write_shortcut(link, cmd, "Market Oracle")
    assert read_shortcut(link) == [sys.executable, '-m oracle --config "C:\my files\oracle.toml" gui', str(folder), "7"]
