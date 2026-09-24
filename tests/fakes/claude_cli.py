"""Stands in for the `claude` CLI: `python claude_cli.py MODE [the real flags...]`. Records
its flags and stdin to $FAKE_CLAUDE_RECORD when set; never touches a real session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def envelope(result: str, is_error: bool = False) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": result,
                       "session_id": "fake"})


def shim(folder: Path, mode: str) -> Path:
    """An executable standing in for `claude` on a path, as the app finds it: a .cmd on
    Windows, so the tests also cover a CLI that cmd.exe starts."""
    if os.name == "nt":
        path = folder / "claude.cmd"
        path.write_text(f'@"{sys.executable}" "{__file__}" {mode} %*\r\n', encoding="utf-8")
    else:
        path = folder / "claude"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{__file__}" {mode} "$@"\n', encoding="utf-8")
        path.chmod(0o755)
    return path


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    mode, flags = sys.argv[1], sys.argv[2:]
    prompt = sys.stdin.buffer.read().decode("utf-8")
    record = os.environ.get("FAKE_CLAUDE_RECORD")
    if record:
        with open(record, "w", encoding="utf-8") as f:
            json.dump({"flags": flags, "prompt": prompt, "env": sorted(os.environ)}, f)
    if mode == "good":
        print(envelope('{"ok": true}'))
    elif mode == "noisy":
        print("A newer version of Claude Code is available.")
        print(envelope('{"ok": true}'))
        print()
    elif mode == "fenced":
        print(envelope('```json\n{"ok": true}\n```'))
    elif mode == "text":
        print(envelope("I would rather not."))
    elif mode == "garbage":
        print("Welcome to Claude Code!")
    elif mode == "fail":
        print("error: unknown option '--frobnicate'", file=sys.stderr)
        return 2
    elif mode == "logged_out":
        print(envelope("Invalid API key · Please run /login", is_error=True))
        return 1
    elif mode == "hang":
        # A grandchild holding the pipes, as node does under claude.cmd: only a tree kill ends it.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        with open(os.environ["FAKE_CLAUDE_PID"], "w", encoding="utf-8") as f:
            f.write(str(child.pid))
        time.sleep(30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
