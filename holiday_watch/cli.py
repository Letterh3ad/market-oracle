"""`python -m holiday_watch <command>`: pass, report, status, and the narrow commands a fix run
may use (api, restart-app, start-ollama, sql)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from holiday_watch import config
from holiday_watch.fixrun import supervise
from holiday_watch.report import write_report
from holiday_watch.seams import DryRunSystem, HttpError, RealSystem, UrllibHttp, Watch
from holiday_watch.state import load_state, read_lock, read_pid
from holiday_watch.timeutil import parse
from holiday_watch.watchdog import ensure_ollama, restart_app, run_pass

POST_ALLOWED = re.compile(r"^/api/(replay/(retry|pause|start)|activity/(retry-all|\d+/(cancel|retry))|app/restart)$")
SQL_ROWS = 200
LOG_TAIL = 15


def api_allowed(method: str, path: str) -> bool:
    if ".." in path:
        return False
    if method == "GET":
        return path.startswith("/api/")
    return method == "POST" and bool(POST_ALLOWED.match(path))


def make_watch(config_path: Path | None, dry_run: bool) -> Watch:
    w = Watch(config.load(config_path), UrllibHttp(), RealSystem(), dry_run=dry_run, own_pid=os.getpid(),
              config_path=config_path)
    if dry_run:
        w.system = DryRunSystem(say=w.log)
    return w


def main(argv: list[str] | None = None, make_watch: Callable[[Path | None, bool], Watch] = make_watch) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, help="settings file (default holiday_watch/config.toml)")
    common.add_argument("--dry-run", action="store_true", help="log what would be done; start or kill nothing")
    parser = argparse.ArgumentParser(prog="python -m holiday_watch")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pass", parents=[common], help="one watchdog pass (every 30 min)")
    sub.add_parser("report", parents=[common], help="write today's morning report")
    sub.add_parser("status", parents=[common], help="print the watch state")
    fix = sub.add_parser("fix-run", parents=[common], help="supervise one fix run (started by a pass)")
    fix.add_argument("trigger_file", type=Path)
    api = sub.add_parser("api", parents=[common], help="call the app's API (an allowlist)")
    api.add_argument("method", choices=("GET", "POST"))
    api.add_argument("path")
    api.add_argument("body", nargs="?", help="JSON body for a POST")
    sub.add_parser("restart-app", parents=[common], help="restart the app, or start it when down")
    sub.add_parser("start-ollama", parents=[common], help="start Ollama when it is down")
    sql = sub.add_parser("sql", parents=[common], help="query the live database, read only")
    sql.add_argument("query")
    args = parser.parse_args(argv)
    w = make_watch(args.config, args.dry_run)
    try:
        return COMMANDS[args.command](w, args)
    except HttpError as exc:
        w.echo(f"error: {exc}")
        return 1


def _pass(w: Watch, args: argparse.Namespace) -> int:
    run_pass(w)
    return 0


def _report(w: Watch, args: argparse.Namespace) -> int:
    write_report(w)
    return 0


def _fix_run(w: Watch, args: argparse.Namespace) -> int:
    return supervise(w, args.trigger_file)


def _api(w: Watch, args: argparse.Namespace) -> int:
    if not api_allowed(args.method, args.path):
        w.echo(f"{args.method} {args.path} is not allowed here")
        return 2
    if args.method == "GET":
        answer = w.http.get(w.api(args.path), timeout=120)
    else:
        answer = w.http.post(w.api(args.path), json.loads(args.body) if args.body else None)
        w.log(f"API {args.method} {args.path} {args.body or ''}".rstrip())
    w.echo(json.dumps(answer, indent=1))
    return 0


def _restart_app(w: Watch, args: argparse.Namespace) -> int:
    try:
        w.http.get(w.api("/api/status"))
    except HttpError:
        restart_app(w, load_state(w.cfg.state_file(w.dry_run)).get("server_pid"), w.system.processes())
        return 0
    answer = w.http.post(w.api("/api/app/restart"))
    w.log(f"app asked to restart: {answer}")
    return 0


def _start_ollama(w: Watch, args: argparse.Namespace) -> int:
    ensure_ollama(w)
    return 0


def _sql(w: Watch, args: argparse.Namespace) -> int:
    try:
        conn = sqlite3.connect(f"{w.cfg.db.resolve().as_uri()}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only = ON")
            cur = conn.execute(args.query)
            rows = cur.fetchmany(SQL_ROWS)
            conn.rollback()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        w.echo(f"error: {exc}")
        return 1
    w.echo("\t".join(d[0] for d in cur.description or ()))
    for row in rows:
        w.echo("\t".join("" if v is None else str(v) for v in row))
    return 0


def _status(w: Watch, args: argparse.Namespace) -> int:
    cfg, now = w.cfg, w.now()
    state = load_state(cfg.state_file(w.dry_run))
    say = w.echo
    for name, url in (("App", w.api("/api/status")), ("Ollama", f"{cfg.ollama_url}/api/tags")):
        try:
            w.http.get(url)
            say(f"{name}: up")
        except HttpError as exc:
            say(f"{name}: DOWN ({exc})")
    say(f"Reports: {cfg.reports}")
    say(f"App restarts in a row: {state.get('app_restarts', 0)}"
        + (f" (down since {state['down_since']})" if state.get("down_since") else ""))
    today = now.astimezone(w.tz).date()
    runs = [r for r in state.get("fix_runs", []) if parse(r["at"]).astimezone(w.tz).date() == today]
    say(f"Fix runs today: {len(runs)} of {cfg.fix_max_per_day}"
        + "".join(f"\n  {r['at']} {r['key']}" for r in runs))
    lock = read_lock(cfg.lock_file)
    if lock:
        age = (now - parse(lock["started_at"])) / timedelta(minutes=1)
        say(f"Fix run running: PID {lock['pid']} for {age:.0f} min ({lock.get('trigger_file')})")
    say(f"Remote Control PID file: {read_pid(cfg.rc_pid_file)}")
    say(f"Claude processes tracked by the reaper: {', '.join(state.get('cpu', {})) or 'none'}")
    try:
        tail = cfg.log_file.read_text(encoding="utf-8").splitlines()[-LOG_TAIL:]
    except FileNotFoundError:
        tail = []
    say("Recent log:" + "".join(f"\n  {line}" for line in tail) if tail else "Recent log: empty")
    return 0


COMMANDS: dict[str, Callable[[Watch, argparse.Namespace], int]] = {
    "pass": _pass, "report": _report, "status": _status, "fix-run": _fix_run, "api": _api,
    "restart-app": _restart_app, "start-ollama": _start_ollama, "sql": _sql,
}

if __name__ == "__main__":
    sys.exit(main())
