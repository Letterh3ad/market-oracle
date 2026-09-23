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


def test_the_port_is_retried_until_the_old_server_lets_go_then_gives_up_with_a_message():
    import socket

    import pytest

    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    port = held.getsockname()[1]
    clock = [0.0]
    sleep = lambda s: clock.__setitem__(0, clock[0] + s)  # noqa: E731
    with pytest.raises(gui.PortBusy, match=f"port {port} .* after 30s"):
        gui.bind("127.0.0.1", port, sleep=sleep, now=lambda: clock[0])
    assert clock[0] >= 30

    def free_after_a_try(s):
        held.close()

    sock = gui.bind("127.0.0.1", port, sleep=free_after_a_try, now=lambda: 0.0)
    assert sock.getsockname()[1] == port
    sock.close()


def test_restart_asks_the_running_server_and_says_when_none_is_running(tmp_path, monkeypatch, capsys):
    import httpx

    cfg = tmp_path / "oracle.toml"
    cfg.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n[gui]\nport = 9001\n', encoding="utf-8")
    asked = []

    def answering(url, **kw):
        asked.append(url)
        return httpx.Response(202, json={"restarting": True, "cut_off": ["Research X"]})

    monkeypatch.setattr(gui.httpx, "post", answering)
    assert cli_main.main(["--config", str(cfg), "gui", "--restart"]) == 0
    assert asked == ["http://127.0.0.1:9001/api/app/restart"]
    assert "restarting" in capsys.readouterr().out.lower()

    def refused(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(gui.httpx, "post", refused)
    assert cli_main.main(["--config", str(cfg), "gui", "--restart"]) == 1
    assert "no Market Oracle GUI is running on http://127.0.0.1:9001" in capsys.readouterr().err


def test_autostart_on_and_off_from_the_command_line(tmp_path, monkeypatch, capsys):
    from oracle.services import app_service

    cfg = tmp_path / "oracle.toml"
    cfg.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n', encoding="utf-8")
    startup = tmp_path / "Startup"
    startup.mkdir()
    monkeypatch.setattr(app_service, "startup_folder", lambda: startup)
    monkeypatch.setattr(app_service, "write_shortcut", lambda link, cmd, d: link.write_text("lnk"))
    assert cli_main.main(["--config", str(cfg), "gui", "--autostart", "on"]) == 0
    assert (startup / "Market Oracle.lnk").exists() and "starts when Windows starts" in capsys.readouterr().out
    assert cli_main.main(["--config", str(cfg), "gui", "--autostart", "off"]) == 0
    assert not (startup / "Market Oracle.lnk").exists()


def test_shortcuts_from_the_command_line(tmp_path, monkeypatch, capsys):
    from oracle.services import app_service

    cfg = tmp_path / "oracle.toml"
    cfg.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n', encoding="utf-8")
    monkeypatch.setattr(app_service, "shortcut_folders", lambda: [tmp_path / "Desktop"])
    monkeypatch.setattr(app_service, "write_shortcut", lambda link, cmd, d: link.parent.mkdir() or link.write_text("x"))
    assert cli_main.main(["--config", str(cfg), "gui", "--install-shortcuts"]) == 0
    assert (tmp_path / "Desktop" / "Market Oracle.lnk").exists()
    assert "Market Oracle.lnk" in capsys.readouterr().out


def test_automation_on_and_off_from_the_command_line(tmp_path, capsys):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"\n', encoding="utf-8")
    assert cli_main.main(["--config", str(cfg), "automation", "off"]) == 0
    assert "[automation]" in cfg.read_text(encoding="utf-8") and "off" in capsys.readouterr().out
    assert cli_main.main(["--config", str(cfg), "automation"]) == 0
    assert "off" in capsys.readouterr().out
    assert cli_main.main(["--config", str(cfg), "automation", "on"]) == 0
    assert "on" in capsys.readouterr().out
