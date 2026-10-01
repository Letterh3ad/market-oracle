"""The research database's own schema: the live one's meta, source pacing, job locks and bars
tables (same DDL, so the same repos and gate work on it), then the testbed's. Append only."""

from __future__ import annotations

from oracle.store.migrations import m0001_meta, m0002_sources, m0003_job_locks, m0009_bars
from oracle.store.testbed_migrations import t0001_testbed, t0002_aliases

MIGRATIONS = (m0001_meta, m0002_sources, m0003_job_locks, m0009_bars, t0001_testbed, t0002_aliases)
