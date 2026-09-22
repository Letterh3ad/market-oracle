import subprocess
import sys

from oracle.cli.main import main
from oracle.store.db import connect


def _run(capsys, *argv):
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def _field(out, name):
    line = next(line for line in out.splitlines() if line.strip().startswith(name))
    return line.split(None, 1)[1].strip()


def test_status_reports_schema_db_and_config_source(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('db_path = "o.db"\n', encoding="utf-8")
    code, out, _ = _run(capsys, "--config", str(cfg), "status")
    assert code == 0
    assert _field(out, "schema") == "1"
    assert _field(out, "database") == str(tmp_path / "o.db")
    assert _field(out, "config") == str(cfg)
    assert _field(out, "currency") == "GBP"
    assert (tmp_path / "o.db").exists()


def test_status_without_a_config_file_says_defaults(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, _ = _run(capsys, "status")
    assert code == 0
    assert _field(out, "config").startswith("defaults")
    assert _field(out, "database") == str(tmp_path / "oracle.db")


def test_status_is_idempotent(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("", encoding="utf-8")
    _, first, _ = _run(capsys, "--config", str(cfg), "status")
    _, second, _ = _run(capsys, "--config", str(cfg), "status")
    assert first == second


def test_a_newer_database_is_refused_without_a_traceback(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("", encoding="utf-8")
    conn = connect(tmp_path / "oracle.db")
    conn.execute("PRAGMA user_version=99")
    conn.close()
    code, _, err = _run(capsys, "--config", str(cfg), "status")
    assert code == 1
    assert "newer than this build" in err
    assert "Traceback" not in err


def test_a_bad_config_is_reported_by_key(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('nope = 1\n', encoding="utf-8")
    code, _, err = _run(capsys, "--config", str(cfg), "status")
    assert code == 2
    assert "nope" in err


def test_no_command_prints_usage_and_fails(capsys):
    code, out, err = _run(capsys)
    assert code == 2
    assert "usage" in (out + err).lower()


def test_runs_as_a_module(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "oracle", "status"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "schema" in result.stdout


def test_an_explicit_config_that_does_not_exist_is_an_error(tmp_path, capsys):
    code, _, err = _run(capsys, "--config", str(tmp_path / "typo.toml"), "status")
    assert code == 2
    assert "typo.toml" in err
    assert not (tmp_path / "oracle.db").exists()
