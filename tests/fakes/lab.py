"""Committed registrations for test-only signals, so the harness runs them (research-lab 01)."""

from oracle.services.testbed_service import research_context
from oracle.store.lab import LabTestRepo, Registration


def registered(ctx, *keys: str, bar: float = 2.0, inclusive: bool = False, horizons=(5,)) -> None:
    """Each key not yet registered, registered and committed with `bar` (default the pre-lab t > 2)."""
    with research_context(ctx) as r:
        repo = LabTestRepo(r.conn)
        for key in (k for k in keys if repo.get(k) is None):
            repo.add(Registration(key, key, "signal", "2021-01-01", tuple(horizons), "test rule", "testbed", "positive",
                                  "-", "-", "-", "-", bar, inclusive, "test bar", "confirm", None, None, "0" * 40,
                                  False, key))
