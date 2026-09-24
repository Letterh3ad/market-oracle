"""A stored cutoff probe, so replay tests can create studies without asking a model."""

from __future__ import annotations

from datetime import date, datetime, timezone

from oracle.config import DEFAULT_CLAIMED_CUTOFF, Config, model_profiles
from oracle.research.cutoff_questions import VERSION
from oracle.store.cutoff import CutoffRepo, ProbeRow

EARLY = date(2025, 5, 31)
"""A cutoff before every replay day the tests use."""


def store_probe(conn, config: Config, cutoff: date = EARLY, profile: str = "default") -> None:
    spec = model_profiles(config)[profile]
    CutoffRepo(conn).replace(ProbeRow(spec.provider, spec.model, profile, VERSION, DEFAULT_CLAIMED_CUTOFF, cutoff,
                                      datetime(2026, 9, 1, tzinfo=timezone.utc), ()))
