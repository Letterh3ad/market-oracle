from dataclasses import replace
from datetime import datetime, timedelta, timezone

from holiday_watch.config import load
from holiday_watch.triggers import Trigger, find_triggers, gate, track_replay

UTC = timezone.utc
CFG = replace(load(None), tick_time="")
FRI_23 = datetime(2026, 9, 25, 23, 0, tzinfo=UTC)  # a Friday


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def status(tick_started=None, time="22:00", enabled=True, running=None):
    return {"tick": None if tick_started is None else {"started_at": iso(tick_started)},
            "schedule": {"enabled": enabled, "time": time}, "running_tick": running}


def study(done=10, failed=0, top=(), state="running", finished=False, sid=1):
    return {"study": {"id": sid, "state": state, "done": done, "failed": [{"id": i} for i in range(failed)],
                      "top_failures": [list(t) for t in top], "finished": finished, "pending": 5,
                      "waiting": None, "polled_at": None}}


def kinds(triggers):
    return [t.kind for t in triggers]


def find(now=FRI_23, state=None, app_up=True, st=None, rp=None, act=None, cfg=CFG):
    return find_triggers(cfg, now, state or {}, app_up=app_up,
                         status=st if st is not None else status(tick_started=now - timedelta(hours=1)),
                         replay=rp if rp is not None else study(), activity=act or {"jobs": []}, tz=UTC)


def test_a_healthy_app_triggers_nothing():
    assert find() == []


def test_app_down_triggers_only_after_the_restart_limit():
    assert find(app_up=False, state={"app_restarts": 1, "down_since": "x"}, st={}, rp={}) == []
    got = find(app_up=False, state={"app_restarts": 2, "down_since": "x"}, st={}, rp={})
    assert kinds(got) == ["app_down"] and got[0].key == "app_down:x"


def test_replay_failure_rate_over_the_limit_once_there_are_enough_cells():
    assert find(rp=study(done=2, failed=1)) == []  # 3 cells, under failure_min
    assert find(rp=study(done=8, failed=2)) == []  # 20% is not over 20%
    got = find(rp=study(done=7, failed=2))
    assert kinds(got) == ["replay_failures"] and got[0].key == "replay_failures:2"


def test_the_same_error_three_times():
    got = find(rp=study(done=100, failed=3, top=[("ollama timeout", 3), ("other", 1)]))
    assert kinds(got) == ["replay_same_error"]
    assert "ollama timeout" in got[0].detail and got[0].key == "replay_same_error:3:ollama timeout"


def test_a_job_running_over_two_hours():
    jobs = {"jobs": [
        {"id": 7, "label": "Research X", "state": "running", "started_at": iso(FRI_23 - timedelta(hours=2, minutes=1))},
        {"id": 8, "label": "Research Y", "state": "running", "started_at": iso(FRI_23 - timedelta(hours=1))},
        {"id": 9, "label": "queued", "state": "queued", "started_at": None},
    ]}
    got = find(act=jobs)
    assert kinds(got) == ["long_job"] and got[0].key == "long_job:7" and "Research X" in got[0].detail


def test_tick_missed_45_minutes_after_a_weekday_tick_time():
    before = FRI_23 - timedelta(days=1)
    assert kinds(find(st=status(tick_started=before))) == ["tick_missed"]
    # within the grace Friday's is not due yet: the latest due is Thursday's, which ran
    thu_ran = datetime(2026, 9, 24, 22, 1, tzinfo=UTC)
    assert find(st=status(tick_started=thu_ran), now=datetime(2026, 9, 25, 22, 44, tzinfo=UTC)) == []
    assert find(st=status(tick_started=datetime(2026, 9, 25, 22, 0, 5, tzinfo=UTC))) == []
    got = find(st=status(tick_started=before))
    assert got[0].key == "tick_missed:2026-09-25"


def test_no_tick_missed_on_weekends_while_running_or_when_scheduling_is_off():
    sat = datetime(2026, 9, 26, 23, 0, tzinfo=UTC)
    # Saturday: Friday's tick ran, so nothing is missed
    assert find(now=sat, st=status(tick_started=datetime(2026, 9, 25, 22, 1, tzinfo=UTC))) == []
    old = FRI_23 - timedelta(days=1)
    assert find(st=status(tick_started=old, running={"id": 1})) == []
    assert find(st=status(tick_started=old, enabled=False)) == []
    thu_late = datetime(2026, 9, 24, 23, 31, tzinfo=UTC)  # Friday's 23:30 is not due yet
    assert find(st=status(tick_started=thu_late, time="23:30")) == []


def test_a_configured_tick_time_wins_over_the_apps():
    old = FRI_23 - timedelta(days=1)
    thu_late = datetime(2026, 9, 24, 23, 31, tzinfo=UTC)
    assert find(st=status(tick_started=thu_late, time="23:30"), cfg=replace(CFG, tick_time="22:00")) != []


def test_a_miss_older_than_a_day_is_not_raised_again():
    mon = datetime(2026, 9, 28, 20, 0, tzinfo=UTC)  # Friday's miss is over a day old
    assert find(now=mon, st=status(tick_started=datetime(2026, 9, 20, tzinfo=UTC))) == []


def test_stalled_study_while_idle():
    state: dict = {}
    track_replay(state, FRI_23 - timedelta(hours=7), study(done=10))
    track_replay(state, FRI_23, study(done=10))
    got = find(state=state)
    assert kinds(got) == ["replay_stalled"] and got[0].key == "replay_stalled:1:10"
    busy = {"jobs": [{"id": 1, "label": "tick", "state": "running", "started_at": iso(FRI_23)}]}
    assert find(state=state, act=busy) == []
    assert find(state=state, rp=study(done=10, state="paused")) == []
    assert find(state=state, rp=study(done=10, finished=True)) == []


def test_progress_resets_the_stall_clock():
    state: dict = {}
    track_replay(state, FRI_23 - timedelta(hours=7), study(done=10))
    track_replay(state, FRI_23 - timedelta(hours=1), study(done=11))
    assert find(state=state, rp=study(done=11)) == []


def test_fired_keys_are_not_raised_again():
    old = FRI_23 - timedelta(days=1)
    assert find(st=status(tick_started=old), state={"fired": {"tick_missed:2026-09-25": iso(FRI_23)}}) == []


T = Trigger("replay_failures", "replay_failures:3", "3 of 10 failed")


def test_gate_holds_while_a_fix_runs():
    assert gate(CFG, FRI_23, {}, T, lock_busy=True, tz=UTC) == "a fix run is still running"
    assert gate(CFG, FRI_23, {}, T, lock_busy=False, tz=UTC) is None


def test_gate_caps_fix_runs_per_local_day():
    runs = [{"kind": k, "key": k, "at": iso(FRI_23 - timedelta(hours=h))} for k, h in
            (("a", 1), ("b", 2), ("c", 3))]
    assert gate(CFG, FRI_23, {"fix_runs": runs}, T, lock_busy=False, tz=UTC) == "3 fix runs today already"
    yesterday = [{**r, "at": iso(FRI_23 - timedelta(days=1))} for r in runs]
    assert gate(CFG, FRI_23, {"fix_runs": yesterday}, T, lock_busy=False, tz=UTC) is None


def test_gate_spaces_the_same_kind_by_four_hours():
    recent = [{"kind": "replay_failures", "key": "replay_failures:2", "at": iso(FRI_23 - timedelta(hours=3))}]
    assert "within 4" in gate(CFG, FRI_23, {"fix_runs": recent}, T, lock_busy=False, tz=UTC)
    older = [{**recent[0], "at": iso(FRI_23 - timedelta(hours=4, minutes=1))}]
    assert gate(CFG, FRI_23, {"fix_runs": older}, T, lock_busy=False, tz=UTC) is None
