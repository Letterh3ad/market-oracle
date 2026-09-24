import pytest

from oracle.cli.main import main
from oracle.research.cutoff_questions import EVENTS
from oracle.services import cutoff_service
from tests.fakes.llm import use_models
from tests.services.test_cutoff_probe import Knows

BIG = '[models.big]\nprovider = "ollama"\nmodel = "big-model"\n'


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text(f'db_path = "o.db"\n{BIG}', encoding="utf-8")
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
    assert "default: ollama" in out and "question set" in out
    assert "2025-08    3/3  known" in out and "2025-09    0/3" in out
    assert "Cutoff: 2025-09-30 (last month known 2025-08; claimed 2025-05-20)." in out
    assert "Stages: queries on default, research on default" in out
    assert "Measured cutoff: 2025-09-30 (set by default)." in out and "from 2025-10-01." in out
    assert f"default: question 1 of {len(EVENTS)}" in captured.err


def test_probe_with_no_model_probes_every_stage_model(tmp_path, models, capsys):
    path = tmp_path / "oracle.toml"
    path.write_text(f'db_path = "o.db"\n{BIG}[models.slots]\nverdict = "big"\n', encoding="utf-8")
    assert main(["--config", str(path), "replay", "probe"]) == 0
    out = capsys.readouterr().out
    assert "default: ollama" in out and "big: ollama big-model" in out
    assert "Measured cutoff: 2026-10-31 (set by big)." in out and "no replay day" in out


def test_cutoff_shows_the_stored_probes_with_every_answer(cfg, models, capsys):
    assert main(["--config", str(cfg), "replay", "cutoff"]) == 0
    out = capsys.readouterr().out
    assert "default: not probed yet" in out and "probe default first" in out
    main(["--config", str(cfg), "replay", "probe"])
    capsys.readouterr()
    assert main(["--config", str(cfg), "replay", "cutoff"]) == 0
    out = capsys.readouterr().out
    assert "Measured cutoff: 2025-09-30" in out and "probed " in out
    assert f"right  {EVENTS[0].day}  {EVENTS[0].question}" in out
    assert f"wrong  {EVENTS[-1].day}  {EVENTS[-1].question}" in out and "said: unknown" in out


def test_a_picked_model_is_probed_and_shown_on_its_own(cfg, models, capsys):
    assert main(["--config", str(cfg), "replay", "probe", "--model", "big"]) == 0
    out = capsys.readouterr().out
    assert "big: ollama big-model" in out and "Measured cutoff: 2026-10-31" in out and "no replay day" in out
    main(["--config", str(cfg), "replay", "cutoff"])
    assert "default: not probed yet" in capsys.readouterr().out
    main(["--config", str(cfg), "replay", "cutoff", "--model", "big"])
    assert "Measured cutoff: 2026-10-31" in capsys.readouterr().out


def test_an_unknown_model_is_an_error(cfg, models, capsys):
    assert main(["--config", str(cfg), "replay", "probe", "--model", "nope"]) == 1
    assert "no model profile nope" in capsys.readouterr().err
