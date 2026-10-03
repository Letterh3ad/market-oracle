"""The research lab's test registry (research-lab 01, spec 6 and 8): every pre-registered test,
its PASS bar frozen at registration, and the commit that put it in DECISIONS. A data run of a
test is refused until that commit is recorded. Tests registered before the lab are back-filled
(legacy) with the bar they were judged by; their verdicts are never judged again."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from oracle.analysts.lab import T_FLOOR, bonferroni_bar
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, NotRegistered, UnknownLabTest
from oracle.services.testbed_service import research_context
from oracle.store.db import transaction
from oracle.store.lab import LabTestRepo, Registration
from oracle.system.version import CODE_ROOT, commit_decisions

__all__ = ["KINDS", "MAX_HORIZONS", "Template", "commit_test", "drop_test", "hypotheses",
           "register_test", "registration", "registrations", "require_committed", "check_committed"]

KINDS = ("signal", "event", "strategy")
MAX_HORIZONS = 2
KEY_RE = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,63}")
HASH_RE = re.compile(r"[0-9a-f]{7,40}")
SIGNS = ("positive", "negative")
STRATEGY_RULE = "beats the index after UK retail costs on confirm and deflated Sharpe probability >= 0.95 given the " \
                "variants tried (spec 6)"
TEXT_FIELDS = ("label", "rule", "universe", "expected_size", "source", "power", "costs", "kill_stage")


@dataclass(frozen=True)
class Template:
    """The spec-8 pre-registration template; the bar is the registry's to set."""
    key: str
    label: str
    kind: str
    horizons: tuple[int, ...]
    rule: str
    universe: str
    expected_sign: str
    expected_size: str
    source: str
    """Where the expected sign and size come from."""
    power: str
    """Could the testbed see the expected size (the power estimate)."""
    costs: str
    kill_stage: str
    parent: str | None = None
    """A FAILed method this one is near; then different_because is required."""
    different_because: str | None = None


def hypotheses(regs: Iterable[Registration]) -> int:
    """N: every registered test's horizons, each once (a strategy once)."""
    return sum(r.hypotheses for r in regs)


def registrations(ctx: AppContext) -> list[Registration]:
    with research_context(ctx) as r:
        return LabTestRepo(r.conn).all()


def registration(ctx: AppContext, key: str) -> Registration:
    with research_context(ctx) as r:
        return _get(LabTestRepo(r.conn), key)


def _get(repo: LabTestRepo, key: str) -> Registration:
    reg = repo.get(key)
    if reg is None:
        raise UnknownLabTest(f"no registered test {key!r}")
    return reg


def _check(t: Template, repo: LabTestRepo) -> None:
    texts = ("key", "kind", "expected_sign", *TEXT_FIELDS)
    if not all(isinstance(getattr(t, f), str) for f in texts) or not all(
            isinstance(getattr(t, f), (str, type(None))) for f in ("parent", "different_because")):
        raise InvalidQuery(f"{', '.join(texts)}, parent and different_because are text")
    if not KEY_RE.fullmatch(t.key):
        raise InvalidQuery(f"key {t.key!r}: lower-case letters, digits and _ . : - only, up to 64")
    if repo.get(t.key):
        raise InvalidQuery(f"{t.key!r} is registered already; a changed rule is a new test with its own key")
    if t.kind not in KINDS:
        raise InvalidQuery(f"kind {t.kind!r}: one of {', '.join(KINDS)}")
    if t.kind == "strategy":
        if t.horizons:
            raise InvalidQuery("a strategy test has no horizons: it is judged over its confirm window")
    elif not 1 <= len(t.horizons) <= MAX_HORIZONS or len(set(t.horizons)) != len(t.horizons):
        raise InvalidQuery(f"1 to {MAX_HORIZONS} different horizons, each counted toward N")
    if any(isinstance(h, bool) or not isinstance(h, int) or h < 1 for h in t.horizons):
        raise InvalidQuery("horizons are whole trading days, 1 or more")
    if t.expected_sign not in SIGNS:
        raise InvalidQuery(f"expected sign: {' or '.join(SIGNS)}")
    if t.kind == "signal" and t.expected_sign != "positive":
        raise InvalidQuery("a signal test passes only on IC above 0: negate the score so its expected sign is positive")
    if empty := [f for f in TEXT_FIELDS if not (getattr(t, f) or "").strip()]:
        raise InvalidQuery(f"the template needs {', '.join(empty)}")
    if t.parent:
        if repo.get(t.parent) is None:
            raise InvalidQuery(f"parent {t.parent!r} is not a registered test")
        if not (t.different_because or "").strip():
            raise InvalidQuery("a test near a registered parent needs a written 'different because'")


def register_test(ctx: AppContext, t: Template, today: str | None = None) -> Registration:
    """Record a pre-registration with its bar frozen now: max(3, Bonferroni over every
    registered test's horizons, this one's included). Uncommitted, so no data run yet. A rule near
    a FAILed literature method (spec 8 and 10) needs a written 'different because'."""
    with research_context(ctx) as r:
        _check(t, LabTestRepo(r.conn))
    # Imported here: the literature index reads the scoreboard, whose harnesses import this module.
    from oracle.services.meaning_service import near_fail

    # Outside the write transaction: the meaning check may call Ollama and store method vectors.
    near = near_fail(ctx, f"{t.label}. {t.rule}")
    if near is not None and not (t.different_because or "").strip():
        raise InvalidQuery(f"the rule is near {near.label}, which FAILed: a FAIL is never re-tested with tweaks; "
                           "say how this one differs (different because), or drop it")
    with research_context(ctx) as r, transaction(r.conn):
        repo = LabTestRepo(r.conn)
        _check(t, repo)
        n = hypotheses(repo.all()) + max(1, len(t.horizons))
        strategy = t.kind == "strategy"
        bar = None if strategy else round(bonferroni_bar(n), 4)
        if strategy:
            rule = STRATEGY_RULE
        else:
            what, rest = (("IC above 0", ", positive in more than half the counted segments") if t.kind == "signal"
                          else ("mean CAR in the train mean's sign", ""))
            rule = f"confirm {what}, t >= {bar:.2f} (max({T_FLOOR:g}, Bonferroni 5% two-sided over N = {n})){rest}"
        reg = Registration(
            t.key, t.label.strip(), t.kind, today or ctx.clock.utcnow().date().isoformat(), tuple(t.horizons),
            t.rule.strip(), t.universe.strip(), t.expected_sign, t.expected_size.strip(), t.source.strip(),
            t.power.strip(), t.costs.strip(), bar, True, rule, t.kill_stage.strip(), t.parent or None,
            (t.different_because or "").strip() or None, None, False, t.key, near_fail=near.label if near else None)
        repo.add(reg)
        return reg


def commit_test(ctx: AppContext, key: str, commit_hash: str, root: Path = CODE_ROOT) -> Registration:
    """Record the commit that put `key`'s registration in DECISIONS. Refused unless the checkout
    shows the commit on HEAD's history, made on or after the registration day, adding the key (in
    backticks) to DECISIONS.md; any git failure refuses it too."""
    commit_hash = commit_hash.strip().lower()
    if not HASH_RE.fullmatch(commit_hash):
        raise InvalidQuery(f"{commit_hash!r} is not a commit hash (7 to 40 hex digits)")
    with research_context(ctx) as r:
        repo = LabTestRepo(r.conn)
        reg = _get(repo, key)
        if reg.commit_hash:
            raise InvalidQuery(f"{key} is committed already at {reg.commit_hash[:7]}")
        try:
            found = commit_decisions(commit_hash, root)
        except LookupError as exc:
            raise InvalidQuery(f"{exc}: the commit is not recorded") from exc
        short = found.full[:7]
        if not found.on_head:
            raise InvalidQuery(f"{short} is not in this checkout's history: merge it first")
        if found.committed.date().isoformat() < reg.registered_on:
            raise InvalidQuery(f"{short} was committed before `{key}` was registered ({reg.registered_on})")
        if f"`{key}`" not in found.decisions or f"`{key}`" in found.parent_decisions:
            raise InvalidQuery(f"{short} does not add `{key}` to DECISIONS.md: commit the registration entry and "
                               "record that commit")
        commit_hash = found.full
        repo.set_commit(key, commit_hash)
        return _get(repo, key)


def drop_test(ctx: AppContext, key: str) -> None:
    """Remove a registration the gate turned down; once committed it stays (it counts toward N)."""
    with research_context(ctx) as r:
        repo = LabTestRepo(r.conn)
        if _get(repo, key).commit_hash:
            raise InvalidQuery(f"{key} is committed: it stays registered and counts toward N")
        repo.delete(key)


def require_committed(r: AppContext, keys: Iterable[str]) -> dict[str, Registration]:
    """`keys`' registrations, on the research database's context `r`; refuses a data run of any
    test not registered or not yet committed."""
    repo, out = LabTestRepo(r.conn), {}
    for key in keys:
        reg = repo.get(key)
        if reg is None:
            raise NotRegistered(f"{key} is not pre-registered: register it and commit the registration before any "
                                "data run")
        if not reg.commit_hash:
            raise NotRegistered(f"{key}'s registration is not committed: commit it to DECISIONS and record the "
                                "commit (oracle lab commit) before any data run")
        out[key] = reg
    return out


def check_committed(ctx: AppContext, keys: Iterable[str]) -> None:
    """require_committed from the live context: a job is refused before it is queued."""
    with research_context(ctx) as r:
        require_committed(r, keys)
