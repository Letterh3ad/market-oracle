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


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Fake hosts resolve to a public address, so the page reader's DNS check never asks the network."""
    monkeypatch.setattr("oracle.research.web.resolve", lambda host: ["93.184.216.34"])


@pytest.fixture(autouse=True)
def _no_real_holiday_seams(monkeypatch):
    """Arming registers scheduled tasks, starts the study and reads power settings: a test that
    reaches the real seams fails instead."""
    def refuse(*args, **kwargs):
        raise AssertionError("tests never use the real holiday seams: pass a fake system to Holiday")

    monkeypatch.setattr("oracle.services.holiday_service.real_holiday", refuse)
    monkeypatch.setattr("oracle.services.holiday_service.RealHolidaySystem.__init__", refuse)


@pytest.fixture(autouse=True)
def _no_real_tilt_model(monkeypatch):
    """A regime-ai step builds its tilt model from the settings, and a local Ollama may be
    running: every test gets a stand-in that keeps every weight instead, unless it passes a fake."""
    from tests.fakes.tilt_model import STAND_IN, TiltModel

    monkeypatch.setattr("oracle.services.regime_tilt.tilt_model", lambda ctx: (TiltModel(STAND_IN), "stand-in"))


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
