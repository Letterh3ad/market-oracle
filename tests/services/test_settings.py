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
