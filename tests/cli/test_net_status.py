from oracle.cli.main import main
from oracle.clock import SystemClock
from oracle.config import load_config
from oracle.net.gate import SourceGate
from oracle.store.db import connect
from oracle.store.migrate import migrate


def _setup(tmp_path, toml=""):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(toml, encoding="utf-8")
    config, _ = load_config(cfg)
    conn = connect(config.db_path)
    migrate(conn)
    return cfg, SourceGate(conn, SystemClock(), config.sources)


def _rows(out):
    return {line.split()[0]: line for line in out.splitlines()[1:] if line.strip()}


def test_every_configured_source_is_listed_with_its_rate(tmp_path, capsys):
    cfg, _ = _setup(tmp_path, "[sources.eodhd]\nrate_per_second = 1.5\n")
    assert main(["--config", str(cfg), "net", "status"]) == 0
    rows = _rows(capsys.readouterr().out)
    assert {"yfinance", "stooq", "gdelt", "google_news", "ddgs", "sec_edgar", "eodhd"} <= set(rows)
    assert "1.5" in rows["eodhd"]
    assert "ok" in rows["gdelt"]


async def test_recent_requests_are_counted(tmp_path, capsys):
    cfg, gate = _setup(tmp_path, "[sources.gdelt]\nrate_per_second = 100\n")
    for _ in range(3):
        await gate.acquire("gdelt")
    main(["--config", str(cfg), "net", "status"])
    assert _rows(capsys.readouterr().out)["gdelt"].split()[2] == "3"


def test_an_open_breaker_shows_its_reason(tmp_path, capsys):
    cfg, gate = _setup(tmp_path)
    for _ in range(3):
        gate.record("yfinance", 429)
    main(["--config", str(cfg), "net", "status"])
    row = _rows(capsys.readouterr().out)["yfinance"]
    assert "OPEN" in row
    assert "429" in row
