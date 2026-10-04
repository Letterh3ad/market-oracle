"""The research lab's scoreboard (research-lab 01): every registered test, one row per horizon,
with its newest kept confirm result read where its harness keeps it, the bar its registration
froze and its verdict as judged then. Nothing is judged again here."""

from __future__ import annotations

from dataclasses import dataclass

from oracle.analysts.lab import bonferroni_bar
from oracle.services import event_study_service, laya_test_service, signal_test_service, strategy_service, trend_service
from oracle.services.context import AppContext
from oracle.services.lab_registry import hypotheses, registrations
from oracle.store.lab import Registration

__all__ = ["ScoreRow", "Scoreboard", "bar_text", "scoreboard"]

NOT_RUN = "not run"
UNCOMMITTED = "not committed: no data run yet"


@dataclass(frozen=True)
class ScoreRow:
    key: str
    label: str
    kind: str
    registered_on: str
    horizon: int | None
    """Trading days; None for a strategy."""
    measure: str
    """What `value` is: confirm IC, mean CAR or CAGR over buy and hold."""
    value: float | None
    t: float | None
    bar: str
    verdict: str
    power: float | None
    """The confirm window's observed power: the smallest IC it could see at the bar."""
    legacy: bool
    commit_hash: str | None
    run_at: str | None
    note: str
    dsr: float | None = None
    """A strategy's deflated Sharpe probability of its daily excess over the index on confirm."""


@dataclass(frozen=True)
class Scoreboard:
    rows: tuple[ScoreRow, ...]
    n: int
    """Registered hypotheses so far (each horizon once)."""
    next_bar: float
    """The t a one-horizon test registered now would need."""


def near_check_text(reg: Registration) -> str | None:
    """How the near-FAIL check ran; None for a registration from before the check existed."""
    if reg.near_check is None:
        return None
    how = "keywords only" if reg.near_check == "keyword" else "meaning"
    return f"ran on {how}: {reg.near_check_note}" if reg.near_check_note else f"ran on {how}"


def bar_text(reg: Registration) -> str:
    if reg.bar is None:
        return reg.bar_rule
    return f"t {'>=' if reg.bar_inclusive else '>'} {reg.bar:.2f}"


def _signal_rows(reg: Registration, kept: dict) -> list[dict]:
    t = kept.get(reg.result_key)
    if t is None:
        return []
    by_h = {w.horizon: w for w in t.confirm}
    return [dict(horizon=h, measure="IC", value=w.ic, t=w.t, verdict=w.verdict_text, power=w.observed_power,
                 run_at=t.run_at) for h in reg.horizons if (w := by_h.get(h))]


def _event_rows(reg: Registration, study) -> list[dict]:
    name = reg.result_key.removeprefix(f"{event_study_service.KEY}:")
    kind = next((x for x in study.types if x.name == name), None) if study else None
    if kind is None:
        return []
    by_h = {r.horizon: r for r in kind.confirm}
    return [dict(horizon=h, measure="mean CAR", value=r.mean, t=r.t, verdict=kind.verdict, power=None,
                 run_at=study.run_at) for h in reg.horizons if (r := by_h.get(h))]


def _laya_rows(ctx: AppContext, reg: Registration) -> list[dict]:
    t = laya_test_service.latest_verdict(ctx, run=reg.result_key.split(":", 1)[1])
    if t is None:
        return []
    by_h = {x.horizon: x for x in t.horizons}
    return [dict(horizon=h, measure="IC", value=x.ic, t=x.t, verdict=x.verdict, power=None, run_at=t.run_at)
            for h in reg.horizons if (x := by_h.get(h))]


def _trend_rows(reg: Registration, t) -> list[dict]:
    if t is None:
        return []
    c = t.confirm
    return [dict(horizon=None, measure="CAGR over buy and hold", value=c.trend.cagr - c.held.cagr, t=None,
                 verdict=t.verdict, power=None, run_at=t.run_at)]


def _strategy_rows(reg: Registration, t) -> list[dict]:
    if t is None:
        return []
    c = t.confirm
    return [dict(horizon=None, measure="CAGR over the index after costs", value=c.excess, t=None, verdict=t.verdict,
                 power=None, run_at=t.run_at, dsr=c.dsr)]


def scoreboard(ctx: AppContext) -> Scoreboard:
    regs = registrations(ctx)
    kept = signal_test_service.signal_tests(ctx)
    study = event_study_service.event_studies(ctx).get("full")
    trend = trend_service.trend_tests(ctx)
    strategies = strategy_service.strategy_tests(ctx)
    rows = []
    for reg in regs:
        if reg.result_key.startswith(f"{event_study_service.KEY}:"):
            found = _event_rows(reg, study)
        elif reg.result_key.startswith("laya:"):
            found = _laya_rows(ctx, reg)
        elif reg.result_key == trend_service.KEY:
            found = _trend_rows(reg, trend)
        elif reg.kind == "strategy":
            found = _strategy_rows(reg, strategies.get(reg.result_key))
        else:
            found = _signal_rows(reg, kept)
        if not found:
            empty = NOT_RUN if reg.commit_hash else UNCOMMITTED
            found = [dict(horizon=h, measure="", value=None, t=None, verdict=empty, power=None, run_at=None)
                     for h in (reg.horizons or (None,))]
        rows += [ScoreRow(reg.key, reg.label, reg.kind, reg.registered_on, measure=f["measure"], horizon=f["horizon"],
                          value=f["value"], t=f["t"], bar=bar_text(reg), verdict=f["verdict"], power=f["power"],
                          legacy=reg.legacy, commit_hash=reg.commit_hash, run_at=f["run_at"], note=reg.note,
                          dsr=f.get("dsr"))
                 for f in found]
    n = hypotheses(regs)
    return Scoreboard(tuple(rows), n, bonferroni_bar(n + 1))
