import pytest

from oracle.store.db import connect
from oracle.store.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()
