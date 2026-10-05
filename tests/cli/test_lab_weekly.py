"""`oracle lab page` and `oracle lab apply-marks` (research-lab 08)."""

import json

from oracle.cli.main import main
from oracle.config import Config
from oracle.services import literature_service as lab
from oracle.services.context import open_context
from oracle.services.literature_service import open_index
from oracle.store.literature import LiteratureRepo
from tests.cli.test_testbed import config
from tests.fakes.results import AT, build


def _built(tmp_path, monkeypatch):
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None) as c:
        build(c, monkeypatch)
        with open_index(c) as r:
            LiteratureRepo(r.conn).add_method("N-01", "New idea", "new", "archive", AT, "seed", rating=6)
    return ["--config", str(config(tmp_path)), "lab"]


def _ctx(tmp_path):
    return open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None)


def test_page_writes_beside_oracle_toml_and_names_the_capabilities(tmp_path, capsys, monkeypatch):
    cmd = _built(tmp_path, monkeypatch)
    assert main([*cmd, "page", "--question", "Widen to mid caps?"]) == 0
    out = capsys.readouterr().out
    page = tmp_path / ".scratch" / "research-lab" / "weekly" / "page.html"
    assert page.exists() and f"Wrote {page.resolve()}: 11 rows, 1 candidates (N-01), 0 papers wanted, 1 open " \
                                "questions." in out
    assert ('Publish it with capabilities {"db": {"rules": [{"path": "marks", "read": "view", "write": "interact"}]}, '
            '"user": {}}') in out
    assert "Widen to mid caps?" in page.read_text(encoding="utf-8")
    assert main([*cmd, "page", "--out", str(tmp_path / "w.html")]) == 0
    assert (tmp_path / "w.html").exists()


def test_apply_marks_reports_each_and_is_idempotent(tmp_path, capsys, monkeypatch):
    cmd = _built(tmp_path, monkeypatch)
    marks = tmp_path / "marks.json"
    marks.write_text(json.dumps([{"id": "N-01", "mark": "go", "note": "yes", "by": "u_1", "at": "2026-10-05T07:30:00Z"},
                                 {"id": "NOPE", "mark": "go", "note": "", "by": "u_1", "at": "2026-10-05T07:30:00Z"}]),
                     encoding="utf-8")
    assert main([*cmd, "apply-marks", str(marks)]) == 0
    out = capsys.readouterr().out
    assert "N-01     applied     go: now go, by u_1 at 2026-10-05T07:30:00+00:00" in out
    assert "NOPE     unknown     no method 'NOPE' in the literature index" in out
    assert "2 marks: 1 applied, 1 unknown" in out
    assert main([*cmd, "apply-marks", str(marks)]) == 0
    assert "2 marks: 1 already, 1 unknown" in capsys.readouterr().out
    with _ctx(tmp_path) as c:
        assert len(lab.method(c, "N-01", tests=False).marks) == 1


def test_apply_marks_refuses_a_file_it_cannot_read(tmp_path, capsys, monkeypatch):
    cmd = _built(tmp_path, monkeypatch)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main([*cmd, "apply-marks", str(bad)]) != 0
    assert main([*cmd, "apply-marks", str(tmp_path / "missing.json")]) != 0
