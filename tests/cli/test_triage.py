from oracle.cli.main import main
from oracle.services import triage_service as triage
from tests.cli.test_testbed import config
from tests.fakes.osap import write_release
from tests.lab.test_triage_service import KEPT


def test_import_map_triage_power_and_the_table(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(triage, "observed_windows", lambda c: KEPT)
    cfg = ["--config", str(config(tmp_path))]
    assert main([*cfg, "lab", "osap-import", str(write_release(tmp_path / "osap"))]) == 0
    assert "Imported PredictorPortsFull.csv (op)" in capsys.readouterr().out
    assert main([*cfg, "lab", "osap"]) == 0
    out = capsys.readouterr().out
    assert "4 Predictor" in out and "large: 3 signals" in out and "compute window" in out
    assert main([*cfg, "lab", "map", "T-13", "Mom12m"]) == 0
    assert main([*cfg, "lab", "mark", "T-13", "go"]) == 0
    capsys.readouterr()
    assert main([*cfg, "lab", "triage", "T-13"]) == 0
    out = capsys.readouterr().out
    assert "T-13 triage: STOP (go -> fail)" in out and "paper (Jegadeesh and Titman 1993): +1.31%/month t 3.74" in out
    assert "original portfolio: in sample +1.31 t 3.74 (312 m)" in out
    assert main([*cfg, "lab", "mark", "T-07", "go"]) == 0
    assert main([*cfg, "lab", "map", "T-07", "GP"]) == 0
    assert main([*cfg, "lab", "triage", "T-07"]) == 0
    assert main([*cfg, "lab", "power", "T-07"]) == 0
    out = capsys.readouterr().out
    assert "T-07 power: STOP (power -> not-testable): could not see" in out and "names-and-days formula" in out
    assert main([*cfg, "lab", "table"]) == 0
    out = capsys.readouterr().out
    assert "T-13     literature  fail" in out and "  OSAP Mom12m; triage stop: OSAP Mom12m" in out
    assert "T-07     power       could-not-see" in out and "  power stop: could not see" in out


def test_triage_by_hand_needs_a_note(tmp_path, capsys):
    cfg = ["--config", str(config(tmp_path))]
    assert main([*cfg, "lab", "mark", "T-29", "go"]) == 0
    assert main([*cfg, "lab", "triage", "T-29", "--by-hand", "pass"]) != 0
    assert "needs a note" in capsys.readouterr().err
