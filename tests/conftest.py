import pytest

from oracle.store.db import connect
from oracle.store.migrate import migrate


@pytest.fixture(autouse=True)
def _in_a_temp_folder(tmp_path, monkeypatch):
    """Every test runs in its own folder: a setting saved without a config file goes to
    oracle.toml in the working folder, and that must never be the repo's real one."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()
