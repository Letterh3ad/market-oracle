"""What calls for a fix run, and whether one may start: pure functions over the app's API answers
and the watch state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from holiday_watch.config import Config
from holiday_watch.timeutil import iso, parse


@dataclass(frozen=True)
class Trigger:
    kind: str
    key: str
    """Unique per occurrence: a fired key never fires again."""
    detail: str


def track_replay(state: dict, now: datetime, replay: dict | None) -> None:
    """Remember when the study's done count last moved."""
    s = (replay or {}).get("study")
    if not s:
        state.pop("replay", None)
        return
    seen = state.get("replay")
    if not seen or seen.get("study") != s["id"] or seen.get("done") != s["done"]:
        state["replay"] = {"study": s["id"], "done": s["done"], "since": iso(now)}


def find_triggers(cfg: Config, now: datetime, state: dict, *, app_up: bool, status: dict | None,
                  replay: dict | None, activity: dict | None, tz: tzinfo | None = None) -> list[Trigger]:
    found: list[Trigger] = []
    if not app_up:
        if state.get("app_restarts", 0) >= cfg.app_restarts:
            found.append(Trigger("app_down", f"app_down:{state.get('down_since')}",
                                 f"{cfg.app_url}/api/status still fails, down on {state['app_restarts']} passes"))
    else:
        jobs = (activity or {}).get("jobs") or []
        found += _study(cfg, now, state, (replay or {}).get("study"), idle=not jobs)
        found += _long_jobs(cfg, now, jobs)
        missed = _tick_missed(cfg, now.astimezone(tz), status or {})
        if missed:
            found.append(Trigger("tick_missed", f"tick_missed:{missed}",
                                 f"no tick since the {missed} weekday tick was due"
                                 f" (last: {((status or {}).get('tick') or {}).get('started_at')})"))
    fired = state.get("fired", {})
    return [t for t in found if t.key not in fired]


def _study(cfg: Config, now: datetime, state: dict, s: dict | None, idle: bool) -> list[Trigger]:
    if not s:
        return []
    out = []
    failed, done = len(s.get("failed") or []), s.get("done", 0)
    if failed + done >= cfg.failure_min and failed / (failed + done) > cfg.failure_rate:
        out.append(Trigger("replay_failures", f"replay_failures:{failed}",
                           f"{failed} of {failed + done} finished replays failed"))
    for error, n in s.get("top_failures") or []:
        if n >= cfg.same_error:
            out.append(Trigger("replay_same_error", f"replay_same_error:{n}:{str(error)[:80]}",
                               f"{n} replays failed with: {error}"))
            break
    seen = state.get("replay") or {}
    if (idle and s.get("state") == "running" and not s.get("finished") and seen.get("study") == s["id"]
            and now - parse(seen["since"]) >= timedelta(hours=cfg.stall_h)):
        out.append(Trigger("replay_stalled", f"replay_stalled:{s['id']}:{s['done']}",
                           f"no replay finished since {seen['since']} while the app was idle"
                           f" (waiting: {s.get('waiting')}, pacer polled at: {s.get('polled_at')})"))
    return out


def _long_jobs(cfg: Config, now: datetime, jobs: list[dict]) -> list[Trigger]:
    return [Trigger("long_job", f"long_job:{j['id']}",
                    f"job {j['id']} ({j.get('label')}) running since {j['started_at']},"
                    f" progress: {j.get('progress')}")
            for j in jobs if j.get("state") == "running" and j.get("started_at")
            and now - parse(j["started_at"]) > timedelta(hours=cfg.job_max_h)]


def _tick_missed(cfg: Config, now_local: datetime, status: dict) -> date | None:
    """The latest weekday tick past its grace, when no tick started since it was due; only within
    a day of it, so an old miss is raised once and then left."""
    schedule = status.get("schedule") or {}
    if not schedule.get("enabled") or status.get("running_tick"):
        return None
    at = cfg.tick_time or schedule.get("time")
    if not at:
        return None
    hh, mm = (int(x) for x in at.split(":"))
    last = (status.get("tick") or {}).get("started_at")
    for back in range(7):
        day = now_local.date() - timedelta(days=back)
        due = datetime.combine(day, time(hh, mm), tzinfo=now_local.tzinfo)
        if day.weekday() >= 5 or due + timedelta(minutes=cfg.tick_grace_min) > now_local:
            continue
        if now_local - due > timedelta(days=1) or (last and parse(last) >= due):
            return None
        return day
    return None


def gate(cfg: Config, now: datetime, state: dict, trigger: Trigger, *, lock_busy: bool,
         tz: tzinfo | None = None) -> str | None:
    """Why a fix run for `trigger` may not start now, or None."""
    if lock_busy:
        return "a fix run is still running"
    runs = state.get("fix_runs") or []
    today = now.astimezone(tz).date()
    if sum(parse(r["at"]).astimezone(tz).date() == today for r in runs) >= cfg.fix_max_per_day:
        return f"{cfg.fix_max_per_day} fix runs today already"
    window = timedelta(hours=cfg.fix_same_trigger_h)
    if any(r["kind"] == trigger.kind and now - parse(r["at"]) < window for r in runs):
        return f"a {trigger.kind} fix run within {cfg.fix_same_trigger_h:g} h"
    return None
