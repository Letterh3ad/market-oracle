"""Settings: defaults here, overridden by a TOML file (`config.toml` beside this package, gitignored;
`config.example.toml` names every key). Strings may use {repo}, {live}, {reports}, {python},
{oracle_config} and {db}."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE / "config.example.toml"
DEFAULT_FILE = HERE / "config.toml"

DEFAULTS: dict[str, dict[str, object]] = {
    "paths": {
        "repo": str(HERE.parent),
        "live": "{repo}/../market-oracle-live",
        "reports": "{repo}/holiday-reports",
        "python": "{repo}/.venv/Scripts/python.exe",
        "oracle_config": "{repo}/oracle.toml",
        "db": "{repo}/oracle.db",
    },
    "app": {
        "url": "http://127.0.0.1:8740",
        "start": ["{python}", "-m", "oracle", "--config", "{oracle_config}", "gui"],
    },
    "ollama": {"url": "http://127.0.0.1:11434", "start": ["ollama", "serve"]},
    "remote_control": {
        "launch": ["claude", "--remote-control", "holiday"],
        "marker": "--remote-control",
        "pid_file": "{reports}/remote-control.pid",
    },
    "claude": {"command": "claude", "fix_model": "opus", "report_model": "sonnet"},
    "fix": {"timeout_min": 30, "max_per_day": 3, "same_trigger_h": 4},
    "triggers": {
        "app_restarts": 2, "failure_rate": 0.2, "failure_min": 5, "same_error": 3, "job_max_h": 2,
        "tick_time": "", "tick_grace_min": 45, "stall_h": 6,
    },
    "reaper": {
        "match": ["claude.exe", "@anthropic-ai/claude-code", "@anthropic-ai\\claude-code"],
        "idle_min": 30, "idle_cpu_s": 2.0, "max_age_h": 3,
    },
    "report": {"timeout_min": 10},
}


@dataclass(frozen=True)
class Config:
    repo: Path
    live: Path
    reports: Path
    python: Path
    oracle_config: Path
    db: Path
    app_url: str
    app_start: tuple[str, ...]
    ollama_url: str
    ollama_start: tuple[str, ...]
    rc_launch: tuple[str, ...]
    rc_marker: str
    rc_pid_file: Path
    claude: str
    fix_model: str
    report_model: str
    fix_timeout_min: float
    fix_max_per_day: int
    fix_same_trigger_h: float
    app_restarts: int
    failure_rate: float
    failure_min: int
    same_error: int
    job_max_h: float
    tick_time: str
    """"HH:MM" local; "" takes the app's own schedule time from /api/status."""
    tick_grace_min: float
    stall_h: float
    reaper_match: tuple[str, ...]
    idle_min: float
    idle_cpu_s: float
    max_age_h: float
    report_timeout_min: float

    @property
    def log_file(self) -> Path:
        return self.reports / "watchdog.log"

    @property
    def fixruns(self) -> Path:
        return self.reports / "fixruns"

    @property
    def lock_file(self) -> Path:
        return self.reports / "fixrun.lock"

    def state_file(self, dry_run: bool = False) -> Path:
        return self.reports / ("state.dry.json" if dry_run else "state.json")


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("HOLIDAY_WATCH_CONFIG", DEFAULT_FILE))
    raw = {k: dict(v) for k, v in DEFAULTS.items()}
    if path.exists():
        with path.open("rb") as f:
            for section, values in tomllib.load(f).items():
                for key, value in values.items():
                    if section not in DEFAULTS or key not in DEFAULTS[section]:
                        raise ValueError(f"{path}: unknown setting {section}.{key}")
                    raw[section][key] = value
    names: dict[str, str] = {}
    for key in ("repo", "live", "reports", "python", "oracle_config", "db"):
        names[key] = str(Path(str(raw["paths"][key]).format(**names)))

    def s(value: object) -> str:
        return str(value).format(**names)

    def cmd(value: object) -> tuple[str, ...]:
        return tuple(s(v) for v in value)  # type: ignore[union-attr]

    p, a, o, rc, c, fx, t, r = (raw[k] for k in ("paths", "app", "ollama", "remote_control", "claude", "fix",
                                                   "triggers", "reaper"))
    return Config(
        repo=Path(names["repo"]), live=Path(names["live"]), reports=Path(names["reports"]),
        python=Path(names["python"]), oracle_config=Path(names["oracle_config"]), db=Path(names["db"]),
        app_url=s(a["url"]).rstrip("/"), app_start=cmd(a["start"]),
        ollama_url=s(o["url"]).rstrip("/"), ollama_start=cmd(o["start"]),
        rc_launch=cmd(rc["launch"]), rc_marker=s(rc["marker"]), rc_pid_file=Path(s(rc["pid_file"])),
        claude=s(c["command"]), fix_model=s(c["fix_model"]), report_model=s(c["report_model"]),
        fix_timeout_min=float(fx["timeout_min"]), fix_max_per_day=int(fx["max_per_day"]),
        fix_same_trigger_h=float(fx["same_trigger_h"]),
        app_restarts=int(t["app_restarts"]), failure_rate=float(t["failure_rate"]),
        failure_min=int(t["failure_min"]), same_error=int(t["same_error"]), job_max_h=float(t["job_max_h"]),
        tick_time=s(t["tick_time"]), tick_grace_min=float(t["tick_grace_min"]), stall_h=float(t["stall_h"]),
        reaper_match=tuple(str(m) for m in r["match"]), idle_min=float(r["idle_min"]),
        idle_cpu_s=float(r["idle_cpu_s"]), max_age_h=float(r["max_age_h"]),
        report_timeout_min=float(raw["report"]["timeout_min"]),
    )
