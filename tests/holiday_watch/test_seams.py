"""RealSystem.spawn with Popen replaced: nothing is started."""

import subprocess

import pytest

from holiday_watch import seams
from holiday_watch.seams import RealSystem


class FakePopen:
    calls: list[dict] = []
    refuse_breakaway = False

    def __init__(self, args, **kw):
        if FakePopen.refuse_breakaway and kw["creationflags"] & seams.BREAKAWAY:
            raise OSError("access denied")
        FakePopen.calls.append({"args": args, **kw})
        self.pid = 4321


@pytest.fixture
def popen(monkeypatch):
    FakePopen.calls, FakePopen.refuse_breakaway = [], False
    monkeypatch.setattr(seams.subprocess, "Popen", FakePopen)
    return FakePopen


def test_a_console_child_keeps_its_own_console_handles(popen, tmp_path):
    assert RealSystem().spawn(["claude", "--remote-control", "holiday"], tmp_path, console=True) == 4321
    (call,) = popen.calls
    assert call["creationflags"] & seams.NEW_CONSOLE
    assert not {"stdin", "stdout", "stderr"} & call.keys()


def test_a_hidden_child_is_detached_with_null_input_and_its_log(popen, tmp_path):
    RealSystem().spawn(["ollama", "serve"], tmp_path, log=tmp_path / "o.log")
    (call,) = popen.calls
    assert call["creationflags"] & seams.DETACHED and call["stdin"] is subprocess.DEVNULL
    assert (tmp_path / "o.log").exists()


def test_a_refused_breakaway_falls_back_and_is_remembered(popen, tmp_path):
    popen.refuse_breakaway = True
    system = RealSystem()
    system.spawn(["ollama", "serve"], tmp_path)
    assert system.breakaway_refused and not popen.calls[0]["creationflags"] & seams.BREAKAWAY
