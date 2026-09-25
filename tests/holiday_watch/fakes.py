from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from holiday_watch.config import load
from holiday_watch.procs import Proc
from holiday_watch.seams import HttpError, Ran, Watch

UTC = timezone.utc
NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
APP = "http://127.0.0.1:8740"
OLLAMA = "http://127.0.0.1:11434"


@dataclass
class FakeHttp:
    routes: dict[str, object] = field(default_factory=dict)
    posts: list[tuple[str, dict | None]] = field(default_factory=list)

    def get(self, url: str, timeout: float = 15.0) -> dict:
        answer = self.routes.get(url, HttpError(f"{url}: connection refused"))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]

    def post(self, url: str, body: dict | None = None, timeout: float = 15.0) -> dict:
        self.posts.append((url, body))
        answer = self.routes.get("POST " + url, {"ok": True})
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]


@dataclass
class FakeSystem:
    procs: list[Proc] = field(default_factory=list)
    spawned: list[tuple[tuple[str, ...], Path, bool]] = field(default_factory=list)
    killed: list[int] = field(default_factory=list)
    ran: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    envs: list[dict | None] = field(default_factory=list)
    result: Ran = Ran(0, "all fixed")
    next_pid: int = 5000
    port_answers: bool = False
    breakaway_refused: bool = False

    def processes(self) -> list[Proc]:
        return list(self.procs)

    def spawn(self, args, cwd, *, console=False, log=None):
        self.spawned.append((tuple(args), Path(cwd), console))
        self.next_pid += 1
        return self.next_pid

    def port_open(self, url: str) -> bool:
        return self.port_answers

    def kill_tree(self, pid: int) -> str:
        self.killed.append(pid)
        return "SUCCESS"

    def run(self, args, stdin, timeout_s, cwd, env=None):
        self.ran.append((tuple(args), stdin))
        self.envs.append(env)
        return self.result


def proc(pid, ppid=1, name="claude.exe", cmd=None, age_min=10.0, cpu=5.0) -> Proc:
    return Proc(pid, ppid, name, cmd if cmd is not None else f"{name} -p", NOW - timedelta(minutes=age_min), cpu)


def healthy_routes() -> dict[str, object]:
    return {
        f"{APP}/api/status": {"server": {"pid": 321}, "tick": {"started_at": "2026-09-25T22:00:05Z"},
                              "schedule": {"enabled": True, "time": "22:00"}, "running_tick": None},
        f"{OLLAMA}/api/tags": {"models": []},
        f"{APP}/api/replay": {"study": None},
        f"{APP}/api/activity": {"jobs": [], "elsewhere": []},
    }


def watch(tmp_path: Path, http=None, system=None, now=NOW, **cfg) -> Watch:
    base = load(tmp_path / "none.toml")
    c = replace(base, reports=tmp_path / "reports", live=tmp_path / "live", repo=tmp_path / "repo",
                rc_pid_file=tmp_path / "reports" / "rc.pid", **cfg)
    lines: list[str] = []
    w = Watch(c, http or FakeHttp(healthy_routes()), system or FakeSystem(), now=lambda: now, tz=UTC,
              own_pid=1, echo=lines.append, sleep=lambda s: None)
    w.lines = lines  # type: ignore[attr-defined]
    return w
