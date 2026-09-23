from __future__ import annotations

import os
import subprocess
from pathlib import Path

from oracle.system.launch import LaunchCommand

__all__ = ["shortcut_folders", "startup_folder", "write_shortcut"]

# Windows' own shortcut object, so no new dependency; the values travel as environment
# variables, never spliced into the script, so quotes and spaces in paths are safe.
_WRITE = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ORACLE_LINK); "
          "$s.TargetPath = $env:ORACLE_TARGET; $s.Arguments = $env:ORACLE_ARGS; "
          "$s.WorkingDirectory = $env:ORACLE_CWD; $s.Description = $env:ORACLE_DESC; "
          "$s.WindowStyle = 7; $s.Save()")  # 7: minimised


# Shell folder ids (CSIDL), asked of Windows: a Desktop moved into OneDrive is not under
# the user's profile.
_DESKTOP, _PROGRAMS, _STARTUP = 0x10, 0x02, 0x07
_CREATE = 0x8000  # CSIDL_FLAG_CREATE: a folder the user deleted is made again, not reported missing


def _shell_folder(csidl: int) -> Path | None:
    if os.name != "nt":
        return None
    import ctypes

    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl | _CREATE, None, 0, buf) != 0:
        return None
    return Path(buf.value)


def startup_folder() -> Path | None:
    """The user's Startup folder (no admin rights needed); None off Windows."""
    return _shell_folder(_STARTUP)


def shortcut_folders() -> list[Path]:
    """The user's desktop and Start-menu programs; empty off Windows."""
    return [f for f in (_shell_folder(_DESKTOP), _shell_folder(_PROGRAMS)) if f]


def _powershell() -> str:
    # By full path: a bare name is looked up in the working folder first on Windows.
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def write_shortcut(link: Path, cmd: LaunchCommand, description: str) -> None:
    """A .lnk that starts `cmd` minimised in its folder."""
    link.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ORACLE_LINK": str(link), "ORACLE_TARGET": cmd.argv[0],
           "ORACLE_ARGS": subprocess.list2cmdline(cmd.argv[1:]), "ORACLE_CWD": str(cmd.cwd),
           "ORACLE_DESC": description}
    subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", _WRITE], env=env, check=True,
                   capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
