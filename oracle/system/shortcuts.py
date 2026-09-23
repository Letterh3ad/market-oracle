from __future__ import annotations

import os
import subprocess
from pathlib import Path

from oracle.system.launch import LaunchCommand

__all__ = ["startup_folder", "write_shortcut"]

# Windows' own shortcut object, so no new dependency; the values travel as environment
# variables, never spliced into the script, so quotes and spaces in paths are safe.
_WRITE = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ORACLE_LINK); "
          "$s.TargetPath = $env:ORACLE_TARGET; $s.Arguments = $env:ORACLE_ARGS; "
          "$s.WorkingDirectory = $env:ORACLE_CWD; $s.Description = $env:ORACLE_DESC; "
          "$s.WindowStyle = 7; $s.Save()")  # 7: minimised


def startup_folder() -> Path | None:
    """The user's Startup folder (no admin rights needed); None off Windows."""
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def write_shortcut(link: Path, cmd: LaunchCommand, description: str) -> None:
    """A .lnk that starts `cmd` minimised in its folder."""
    link.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ORACLE_LINK": str(link), "ORACLE_TARGET": cmd.argv[0],
           "ORACLE_ARGS": subprocess.list2cmdline(cmd.argv[1:]), "ORACLE_CWD": str(cmd.cwd),
           "ORACLE_DESC": description}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WRITE], env=env, check=True,
                   capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
