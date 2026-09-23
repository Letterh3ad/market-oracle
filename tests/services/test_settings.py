import pytest

from oracle.config import load_config
from oracle.services.context import load_settings, open_context
from oracle.services.errors import InvalidQuery
from oracle.services.settings_service import set_peer_count


def test_the_peer_count_is_saved_to_the_config_file_and_used_at_once(tmp_path):
    path = tmp_path / "oracle.toml"
    path.write_text(f'db_path = "{(tmp_path / "o.db").as_posix()}"  # keep me\n', encoding="utf-8")
    config, source = load_settings(path)
    with open_context(config, source) as ctx:
        assert set_peer_count(ctx, 35) == path
        assert ctx.config.analysts.peers == 35
    assert load_config(path)[0].analysts.peers == 35
    assert "# keep me" in path.read_text(encoding="utf-8")


def test_without_a_config_file_one_is_made_where_it_would_be_found(tmp_path, monkeypatch):
    from oracle.config import Config

    monkeypatch.chdir(tmp_path)
    with open_context(Config(db_path=tmp_path / "o.db"), None) as ctx:
        path = set_peer_count(ctx, 10)
        assert ctx.config_source == path
    assert path == tmp_path / "oracle.toml"
    assert load_config(None, cwd=tmp_path)[0].analysts.peers == 10


@pytest.mark.parametrize("count", [4, 101])
def test_a_count_outside_5_to_100_is_refused_and_nothing_is_written(tmp_path, count):
    path = tmp_path / "oracle.toml"
    path.write_text("[analysts]\npeers = 20\n", encoding="utf-8")
    config, source = load_settings(path)
    with open_context(with_db(config, tmp_path), source) as ctx:
        with pytest.raises(InvalidQuery, match="5 to 100"):
            set_peer_count(ctx, count)
    assert path.read_text(encoding="utf-8") == "[analysts]\npeers = 20\n"


def with_db(config, tmp_path):
    from dataclasses import replace

    return replace(config, db_path=tmp_path / "o.db")


@pytest.mark.parametrize("eol", [b"\r\n", b"\n"])
def test_line_endings_survive_and_no_temporary_file_is_left(tmp_path, eol):
    path = tmp_path / "oracle.toml"
    lines = [f'db_path = "{(tmp_path / "o.db").as_posix()}"'.encode(), b"[analysts]", b"peers = 20", b""]
    path.write_bytes(eol.join(lines))
    config, source = load_settings(path)
    with open_context(config, source) as ctx:
        set_peer_count(ctx, 40)
    assert path.read_bytes() == eol.join(lines[:2] + [b"peers = 40", b""])
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("oracle")] == ["oracle.toml"]


def test_an_unexpected_failure_while_checking_leaves_the_file_and_no_leftovers(tmp_path, monkeypatch):
    from oracle.services import settings_service

    path = tmp_path / "oracle.toml"
    original = f'db_path = "{(tmp_path / "o.db").as_posix()}"\n'
    path.write_text(original, encoding="utf-8")
    config, source = load_settings(path)

    def broken(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(settings_service, "load_config", broken)
    with open_context(config, source) as ctx:
        with pytest.raises(OSError):
            set_peer_count(ctx, 40)
    assert path.read_text(encoding="utf-8") == original
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("oracle")] == ["oracle.toml"]


def test_the_ai_account_switch_is_saved_keeping_its_verdict_age_and_reloaded_live(tmp_path):
    from oracle.services.settings_service import reload_live_settings, set_ai_account

    path = tmp_path / "oracle.toml"
    path.write_text("[ai_account]\nmax_verdict_age_days = 3\n", encoding="utf-8")
    config, source = load_settings(path)
    with open_context(with_db(config, tmp_path), source) as ctx:
        set_ai_account(ctx, True)
        assert (ctx.config.ai_account.enabled, ctx.config.ai_account.max_verdict_age_days) == (True, 3)
        path.write_text("[ai_account]\nenabled = false\n", encoding="utf-8")
        reload_live_settings(ctx)
        assert ctx.config.ai_account.enabled is False
    assert load_config(path)[0].ai_account.enabled is False
