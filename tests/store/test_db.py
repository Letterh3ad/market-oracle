from datetime import datetime, timedelta, timezone

import pytest

from oracle.store.db import to_utc_iso, transaction
from oracle.store.meta import MetaRepo


def test_aware_non_utc_datetime_converts_to_utc_and_round_trips():
    ts = datetime(2026, 8, 27, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    iso = to_utc_iso(ts)
    assert iso == "2026-08-27T12:00:00+00:00"
    assert datetime.fromisoformat(iso) == ts


def test_naive_datetime_raises():
    with pytest.raises(ValueError):
        to_utc_iso(datetime(2026, 8, 27, 12, 0))


def test_meta_round_trips_and_overwrites(conn):
    meta = MetaRepo(conn)
    assert meta.get("k") is None
    meta.set("k", "a")
    meta.set("k", "b")
    assert meta.get("k") == "b"


def test_nested_transaction_commits_both_writes(conn):
    meta = MetaRepo(conn)
    with transaction(conn):
        meta.set("a", "1")
        meta.set("b", "2")
    assert (meta.get("a"), meta.get("b")) == ("1", "2")


def test_nested_transaction_rolls_back_both_writes(conn):
    meta = MetaRepo(conn)
    with pytest.raises(RuntimeError):
        with transaction(conn):
            meta.set("a", "1")
            meta.set("b", "2")
            raise RuntimeError("boom")
    assert (meta.get("a"), meta.get("b")) == (None, None)


def test_inner_failure_rolls_back_only_the_inner_block(conn):
    meta = MetaRepo(conn)
    with transaction(conn):
        meta.set("outer", "1")
        with pytest.raises(RuntimeError):
            with transaction(conn):
                meta.set("inner", "1")
                raise RuntimeError("boom")
    assert meta.get("outer") == "1"
    assert meta.get("inner") is None
