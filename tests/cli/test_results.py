"""`oracle lab table` (research-lab 06): the results table as text and as the JSON the weekly page reads."""

import json

from oracle.cli.main import main
from oracle.config import Config
from oracle.services import results_service as results
from oracle.services.context import open_context
from oracle.services.literature_service import open_index
from oracle.store.literature import LiteratureRepo
from tests.cli.test_testbed import config
from tests.fakes.results import build


def _built(tmp_path, monkeypatch):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None) as c:
        build(c, monkeypatch)
        want = json.loads(json.dumps(results.table_json(results.results_table(c))))
    return ["--config", str(config(tmp_path)), "lab", "table"], want


def test_the_table_prints_every_column(tmp_path, capsys, monkeypatch):
    cmd, _ = _built(tmp_path, monkeypatch)
    assert main(cmd) == 0
    out = capsys.readouterr().out
    assert "10 rows. Deaths by stage: literature 1, power 1, build 0, train 0, confirm 2, unrecorded 1" in out
    assert "A-01     candidate   open           replicated     LLM headline sentiment" in out
    assert "  1. LLM and NLP on text; status go; sources: Jegadeesh and Titman 1993 (replicated), A blog on " \
           "headlines (practitioner)" in out
    assert "  evidence: GPT scores predict next-day returns." in out
    assert "  note: 2026-10-04 go by user: worth a look" in out
    assert "T-13     literature  fail           replicated     Residual momentum" in out
    assert "  OSAP Mom12m; triage stop: OSAP Mom12m: large caps since 2000 +0.30%/month t 1.00" in out
    assert "  power stop: could not see: expected IC 0.0050 at 20 d" in out
    assert ("  test momentum_12_1 (signal, bar t > 2.00): fail; registration: oracle lab show momentum_12_1" in out)
    assert ("    h20 IC train +0.0123 confirm +0.0071 t 0.45 power 0.0158 turnover 31.0% break-even 0.42%: FAIL "
            "(could not see an IC this small: needed 0.0158)") in out
    assert "    CAGR over buy and hold train +0.40% confirm -3.31% DSR 0.120 turnover 80.0%: FAIL" in out
    assert "-        confirm     pass           -              Results day" in out
    assert "  test gone_key: not in the registry" in out
    assert "notes: oracle lab method REF" in out


def test_a_test_shown_under_an_earlier_method_points_there(tmp_path, capsys, monkeypatch):
    cmd, _ = _built(tmp_path, monkeypatch)
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None) as c:
        with open_index(c) as r:
            LiteratureRepo(r.conn).add_method("M-002", "Momentum again", "new", "added", "2026-10-04", "user",
                                              test_keys=("momentum_12_1",))
    assert main(cmd) == 0
    assert "  test momentum_12_1: tested under X-01 (confirm, fail)" in capsys.readouterr().out


def test_the_filters_narrow_the_rows(tmp_path, capsys, monkeypatch):
    cmd, _ = _built(tmp_path, monkeypatch)
    assert main([*cmd, "--stage", "confirm", "--verdict", "fail"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("2 rows (stage confirm, verdict fail).") and "X-01" in out and "T-13 " not in out
    assert main([*cmd, "--family", "1. LLM and NLP on text"]) == 0
    assert capsys.readouterr().out.startswith("2 rows (family 1. LLM and NLP on text).")
    assert main([*cmd, "--verdict", "nope"]) != 0
    assert "verdict is one of" in capsys.readouterr().err


def test_the_json_is_the_services(tmp_path, capsys, monkeypatch):
    cmd, want = _built(tmp_path, monkeypatch)
    assert main([*cmd, "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert {**got, "as_of": None} == {**want, "as_of": None}
