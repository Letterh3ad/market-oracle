import pytest

from oracle.cli.main import main
from oracle.research.cutoff_questions import EVENTS
from oracle.services import cutoff_service
from tests.fakes.llm import use_models
from tests.services.test_cutoff_probe import Knows


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text('db_path = "o.db"\n[models.big]\nprovider = "ollama"\nmodel = "big-model"\n', encoding="utf-8")
    return path


@pytest.fixture
def models(monkeypatch):
    table = {"default": Knows("2025-08"), "big": Knows("2099-12")}
    use_models(monkeypatch, lambda profile: table[profile], cutoff_service)
    return table


def test_probe_prints_the_month_scores_and_the_measured_cutoff(cfg, models, capsys):
    assert main(["--config", str(cfg), "replay", "probe"]) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "profile default" in out and "question set" in out
    assert "2025-08    2/2  known" in out and "2025-09    0/3" in out
    assert "Measured cutoff: 2025-09-30 (last month known 2025-08; claimed 2025-05-20)." in out
    assert "Replays start after it, from 2025-10-01." in out
    assert f"question 1 of {len(EVENTS)}" in captured.err


def test_cutoff_shows_the_stored_probe_with_every_answer(cfg, models, capsys):
    assert main(["--config", str(cfg), "replay", "cutoff"]) == 0
    assert "not been probed yet" in capsys.readouterr().out
    main(["--config", str(cfg), "replay", "probe"])
    capsys.readouterr()
    assert main(["--config", str(cfg), "replay", "cutoff"]) == 0
    out = capsys.readouterr().out
    assert "Measured cutoff: 2025-09-30" in out and "probed " in out
    assert f"right  {EVENTS[0].day}  {EVENTS[0].question}" in out
    assert f"wrong  {EVENTS[-1].day}  {EVENTS[-1].question}" in out and "said: unknown" in out


def test_a_picked_model_is_probed_and_shown_on_its_own_and_says_when_no_replay_can_start(cfg, models, capsys):
    assert main(["--config", str(cfg), "replay", "probe", "--model", "big"]) == 0
    out = capsys.readouterr().out
    assert "profile big" in out and "Measured cutoff: 2026-10-31" in out and "no replay day" in out
    main(["--config", str(cfg), "replay", "cutoff"])
    assert "not been probed yet" in capsys.readouterr().out
    main(["--config", str(cfg), "replay", "cutoff", "--model", "big"])
    assert "Measured cutoff: 2026-10-31" in capsys.readouterr().out


def test_an_unknown_model_is_an_error(cfg, models, capsys):
    assert main(["--config", str(cfg), "replay", "probe", "--model", "nope"]) == 1
    assert "no model profile nope" in capsys.readouterr().err
