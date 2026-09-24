from datetime import datetime, timedelta, timezone

import pytest

from oracle.store.db import connect
from oracle.store.job_history import JobHistoryRepo
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


@pytest.fixture
def finish_job(ctx):
    """Records a job in history as finished `n` minutes after 20:00 UTC on 2026-09-23 by a
    process long gone; returns its id."""
    repo = JobHistoryRepo(ctx.conn)
    start = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)

    def finish(n, kind="prices", label=None, state="done", error=None, subject=None):
        at = start + timedelta(minutes=n)
        job_id = repo.add(kind, label or f"Job {n}", subject, None, "queued", at, 999_999_999)
        repo.update(job_id, state, error, at, at)
        return job_id

    return finish
