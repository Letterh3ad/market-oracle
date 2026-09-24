"""The watch's small JSON files: state between passes and the fix-run lock."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from holiday_watch.timeutil import parse


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None  # a torn write is treated as no file: the next pass rewrites it


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def load_state(path: Path) -> dict:
    return _read(path) or {}


def save_state(path: Path, state: dict, now: datetime) -> None:
    """Old fix runs and fired keys are dropped: only the last day's count and a week's keys matter."""
    state["fix_runs"] = [r for r in state.get("fix_runs", []) if now - parse(r["at"]) < timedelta(days=2)]
    state["fired"] = {k: at for k, at in state.get("fired", {}).items() if now - parse(at) < timedelta(days=7)}
    _write(path, state)


def read_lock(path: Path) -> dict | None:
    return _read(path)


def write_lock(path: Path, lock: dict) -> None:
    _write(path, lock)


def clear_lock(path: Path) -> None:
    path.unlink(missing_ok=True)


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")
