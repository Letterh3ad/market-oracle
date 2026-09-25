import pytest

from oracle.cli.main import main
from oracle.services import holiday_service
from oracle.services.holiday_service import Check, Report

GOOD = Report(True, True, (Check("App", True, "answers"),), ("Armed.", "Registered task X."))
BAD = Report(False, False, (Check("App", True, "answers"), Check("Sleep", False, "sleeps after 30 min", "set Never")),
             ("Not armed: failed Sleep.",))


class FakeHoliday:
    def __init__(self, report):
        self.report, self.calls = report, []

    def arm(self, ctx):
        self.calls.append("arm")
        return self.report

    def disarm(self, ctx):
        self.calls.append("disarm")
        return self.report

    def status(self, ctx):
        self.calls.append("status")
        return self.report


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('db_path = "o.db"\n', encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("command", ["arm", "disarm", "status"])
def test_each_command_prints_the_checks_and_lines(cfg, capsys, monkeypatch, command):
    fake = FakeHoliday(GOOD)
    monkeypatch.setattr(holiday_service, "real_holiday", lambda: fake)
    assert main(["--config", cfg, "holiday", command]) == 0
    out = capsys.readouterr().out
    assert fake.calls == [command]
    assert "ok    App: answers" in out and "Armed." in out and "Registered task X." in out


def test_a_refusal_names_the_failed_check_and_its_fix_and_fails(cfg, capsys, monkeypatch):
    monkeypatch.setattr(holiday_service, "real_holiday", lambda: FakeHoliday(BAD))
    assert main(["--config", cfg, "holiday", "arm"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  Sleep: sleeps after 30 min" in out and "fix: set Never" in out and "Not armed" in out
