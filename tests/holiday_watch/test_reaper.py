import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from holiday_watch.config import load
from holiday_watch.procs import Proc, ancestors, descendants, parse_processes
from holiday_watch.reaper import choose_kills, is_claude, protected_pids, track_cpu

UTC = timezone.utc
NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
CFG = load(None)


def proc(pid, ppid=1, name="claude.exe", cmd=None, age_min=10.0, cpu=5.0):
    return Proc(pid, ppid, name, cmd if cmd is not None else f"{name} -p", NOW - timedelta(minutes=age_min), cpu)


def test_parse_processes_reads_the_powershell_rows():
    rows = [{"pid": 10, "ppid": 4, "name": "claude.exe", "cmd": "claude --remote-control holiday",
             "created": "2026-09-26T01:02:03.1234567Z", "cpu": 12.5},
            {"pid": 4, "ppid": 0, "name": "System", "cmd": None, "created": None, "cpu": None}]
    got = parse_processes(json.dumps(rows))
    assert got[0] == Proc(10, 4, "claude.exe", "claude --remote-control holiday",
                          datetime(2026, 9, 26, 1, 2, 3, 123456, tzinfo=UTC), 12.5)
    assert got[1] == Proc(4, 0, "System", "", None, 0.0)
    assert parse_processes(json.dumps(rows[0]))[0].pid == 10  # one row comes back as an object


def test_is_claude_matches_name_or_command_line_in_any_case():
    pats = CFG.reaper_match
    assert is_claude(proc(1, name="Claude.EXE"), pats)
    assert is_claude(proc(1, name="node.exe", cmd=r"node C:\npm\node_modules\@anthropic-ai\claude-code\cli.js"), pats)
    assert not is_claude(proc(1, name="python.exe", cmd="python -m oracle gui"), pats)


def test_the_desktop_app_and_claude_codes_daemon_are_never_reaped():
    """Seen on the user's PC: all of these are claude.exe."""
    spared = [
        proc(1, cmd=r'"C:\Program Files\WindowsApps\Claude_2.9939.2.0_x64__pzs8sxrjxfjjc\app\claude.exe" --type=gpu',
             age_min=5000),
        proc(2, cmd=r"C:\Users\lette\.local\bin\claude.exe daemon run --json-path C:\x", age_min=5000),
        proc(3, cmd=r"C:\Users\lette\.local\bin\claude.exe --bg-pty-host \\.\pipe\cc-daemon-1", age_min=5000),
    ]
    session = proc(4, 3, cmd=r"C:\Users\lette\.local\bin\claude.exe --session-id 55f9c7b7", age_min=5000)
    assert not any(is_claude(p, CFG.reaper_match, CFG.reaper_spare) for p in spared)
    assert [k.proc.pid for k in choose_kills([*spared, session], NOW, {}, set(), CFG)] == [4]


def test_tree_walks():
    procs = [proc(1, 0, "a"), proc(2, 1, "b"), proc(3, 2, "c"), proc(4, 1, "d"), proc(5, 9, "e")]
    assert descendants(procs, {2}) == {2, 3}
    assert ancestors(procs, 3) == {3, 2, 1}


def test_a_process_past_its_time_limit_is_killed():
    procs = [proc(20, age_min=3 * 60 + 1), proc(21, age_min=60)]
    kills = choose_kills(procs, NOW, {}, set(), CFG)
    assert [(k.proc.pid, k.reason) for k in kills] == [(20, "past its 3 h time limit")]
    assert kills[0].age_s == (3 * 60 + 1) * 60


def test_idle_needs_a_baseline_older_than_idle_min_with_little_cpu_since():
    p = proc(30, cpu=5.0)
    track = track_cpu({}, [p], NOW - timedelta(minutes=31), CFG)
    assert choose_kills([replace(p, cpu_s=6.0)], NOW, track, set(), CFG)[0].reason == "idle for 31 min"
    assert choose_kills([replace(p, cpu_s=8.0)], NOW, track, set(), CFG) == []
    fresh = track_cpu({}, [p], NOW - timedelta(minutes=29), CFG)
    assert choose_kills([p], NOW, fresh, set(), CFG) == []


def test_track_cpu_keeps_an_idle_baseline_and_resets_on_work_or_a_new_process():
    p = proc(30, cpu=5.0)
    first = track_cpu({}, [p], NOW - timedelta(minutes=60), CFG)
    kept = track_cpu(first, [replace(p, cpu_s=5.5)], NOW - timedelta(minutes=30), CFG)
    assert kept["30"] == first["30"]
    worked = track_cpu(first, [replace(p, cpu_s=9.0)], NOW, CFG)
    assert worked["30"]["cpu"] == 9.0
    reused = track_cpu(first, [replace(p, created=NOW)], NOW, CFG)
    assert reused["30"]["since"] != first["30"]["since"]
    assert track_cpu(first, [proc(31, name="python.exe", cmd="python")], NOW, CFG) == {}


def test_protected_processes_are_spared():
    procs = [proc(40, age_min=500), proc(41, 40, age_min=500), proc(50, age_min=500)]
    assert [k.proc.pid for k in choose_kills(procs, NOW, {}, {40, 41}, CFG)] == [50]


def test_a_killed_parent_covers_its_claude_children():
    procs = [proc(60, age_min=500), proc(61, 60, age_min=500)]
    assert [k.proc.pid for k in choose_kills(procs, NOW, {}, set(), CFG)] == [60]


def test_protected_pids_cover_remote_control_a_young_fix_run_and_our_own_ancestors():
    procs = [proc(1, 0, "explorer.exe"), proc(70, 1), proc(71, 70), proc(80, 1, "python.exe"), proc(81, 80),
             proc(90, 1, "cmd.exe"), proc(91, 90, "python.exe")]
    lock = {"pid": 80, "started_at": (NOW - timedelta(minutes=10)).isoformat()}
    assert protected_pids(procs, NOW, CFG, rc_pid=70, lock=lock, own_pid=91) == {70, 71, 80, 81, 90, 91, 1}
    old = {**lock, "started_at": (NOW - timedelta(minutes=31)).isoformat()}
    assert protected_pids(procs, NOW, CFG, rc_pid=None, lock=old, own_pid=91) == {90, 91, 1}
