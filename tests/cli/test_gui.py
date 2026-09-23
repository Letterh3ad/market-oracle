from oracle.cli import gui
from oracle.cli import main as cli_main


def test_gui_serves_the_app_on_the_configured_address(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n[gui]\nport = 9001\n', encoding="utf-8")
    served = {}
    monkeypatch.setattr(gui, "serve", lambda app, host, port: served.update(app=app, host=host, port=port))
    assert cli_main.main(["--config", str(cfg), "gui"]) == 0
    assert (served["host"], served["port"]) == ("127.0.0.1", 9001)
    assert served["app"].title == "Market Oracle"
    assert "http://127.0.0.1:9001" in capsys.readouterr().out
