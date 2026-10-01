"""Laya over the backfilled history, per (article, target) pair: each main-list company's own
backfilled news and filings as `own`, then the backfilled news of every company on its industry
map, for it. One long job: it shares one model, gives way to live model work (closing the model
meanwhile), stops on Cancel between batches and resumes by skipping scored pairs. The stored
relation is a label only: the predictive test recounts every pair point in time."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field

from oracle.research.laya import Scorer
from oracle.services import laya_service
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.history_names import default_search
from oracle.services.history_service import Company, company_history, history_companies, plain_company
from oracle.services.industry_research_service import relation_for_kinds
from oracle.services.industry_service import Related, industry_map
from oracle.services.jobs_service import single_flight
from oracle.services.laya_service import MODEL_TAG, MODEL_WORK, SharedScorer, live_model_work
from oracle.services.watch_service import DEFAULT_LIST
from oracle.store.db import to_utc_iso
from oracle.store.laya import LayaRepo
from oracle.store.meta import MetaRepo

__all__ = ["GPU_MS", "LayaHistoryRun", "MapSubject", "PairGroup", "PairPlan", "history_pairs", "last_history_run",
           "HISTORY_WAITS_FOR", "READER_JOB", "history_waits_anywhere", "map_subjects", "plan_history", "score_history"]

LAST_RUN = "laya_history_last_run"
JOB = "laya_history"
LEASE_S = 1800.0
GPU_WAIT_S = 60.0
"""How often a run with no GPU room tries the GPU again."""
HISTORY_WAITS_FOR = (*MODEL_WORK, "tick", "industry_research")
"""Job locks (any process) a history run gives way to: live model work, a tick, industry research."""
READER_JOB = "reader"
"""The stronger reader's lock (`reader_service.JOB`): it holds most of the GPU and CPU for a day, so
history scoring waits for it, and it for history scoring."""


def history_waits_anywhere(ctx: AppContext) -> str | None:
    """What a history run gives way to in any process: HISTORY_WAITS_FOR and the stronger reader."""
    return live_model_work(ctx, (*HISTORY_WAITS_FOR, READER_JOB))
GPU_MS = 19.0
"""Milliseconds a pair on the GPU (ticket 01's measure: 11 to 19), for an estimate before any run."""
CPU_MS = 410.0


@dataclass(frozen=True)
class MapSubject:
    symbol: str
    """The history company (its primary listing), whose backfill ids are its news."""
    related: Related
    """How it is related to the target, with each relation's source and first-known time."""


@dataclass(frozen=True)
class PairGroup:
    target: str
    subject: str
    relation: str
    ids: tuple[int, ...]


def map_subjects(ctx: AppContext, target: str, companies: Sequence[Company]) -> list[MapSubject]:
    """The history companies on `target`'s map today, each with its relations: a map line the
    backfill folded into another line of the same company (same search name) is found by name."""
    by_symbol = {c.symbol.casefold(): c for c in companies}
    by_name = {default_search(c.name).query.casefold(): c for c in companies}
    out: dict[str, MapSubject] = {}
    for r in industry_map(ctx, target).related:
        p = plain_company(ctx, r.symbol, strict=False)
        if p is None:
            continue
        c = by_symbol.get(p.primary_symbol.casefold()) or by_name.get(default_search(p.name).query.casefold())
        if c is None or c.symbol.casefold() == target.casefold():
            continue
        have = out.get(c.symbol)
        out[c.symbol] = MapSubject(c.symbol, r if have is None else Related(
            have.related.symbol, have.related.company_key, have.related.name, have.related.market_cap,
            have.related.relations + r.relations, have.related.link_id, have.related.evidence))
    return list(out.values())


def history_pairs(ctx: AppContext, watchlist: str = DEFAULT_LIST, *, only: Sequence[str] | None = None,
                  own_only: bool = False) -> list[PairGroup]:
    """Every main-list company's own backfill first, then each one's map companies' backfill."""
    companies = history_companies(ctx, watchlist)
    mains = [c for c in companies if c.main]
    if only:
        wanted = {laya_service.primary(ctx, s).casefold() for s in only}
        mains = [c for c in mains if c.symbol.casefold() in wanted]
    groups = [PairGroup(m.symbol, m.symbol, "own", tuple(company_history(ctx, m.symbol))) for m in mains]
    if not own_only:
        for m in mains:
            for s in map_subjects(ctx, m.symbol, companies):
                ids = tuple(company_history(ctx, s.symbol))
                if ids:
                    groups.append(PairGroup(m.symbol, s.symbol, relation_for_kinds(s.related.kinds), ids))
    return groups


@dataclass(frozen=True)
class PairPlan:
    targets: int
    groups: int
    pairs: int
    """(article, target) pairs, an article in two groups of a target counted once."""
    unscored: int
    ms_per_pair: float
    basis: str
    """Where the per-pair time comes from."""
    estimate_s: float
    by_relation: dict[str, int] = field(default_factory=dict)


def plan_history(ctx: AppContext, watchlist: str = DEFAULT_LIST, *, only: Sequence[str] | None = None,
                 own_only: bool = False, model: str = MODEL_TAG) -> PairPlan:
    """How many pairs a run would score and how long it would take, reading only."""
    groups = history_pairs(ctx, watchlist, only=only, own_only=own_only)
    repo = LayaRepo(ctx.conn)
    seen: dict[str, set[int]] = {}
    by_relation: dict[str, int] = {}
    unscored = 0
    for g in groups:
        have = seen.setdefault(g.target, set())
        new = [i for i in dict.fromkeys(g.ids) if i not in have]
        have.update(new)
        by_relation[g.relation] = by_relation.get(g.relation, 0) + len(new)
        unscored += repo.unscored_count(laya_service.primary(ctx, g.target), model, new)
    last = last_history_run(ctx)
    if last and last.scored and last.score_seconds:
        ms, basis = 1000 * last.score_seconds / last.scored, f"measured by the last run on {last.device}"
    else:
        ms, basis = GPU_MS, "ticket 01's GPU measure (CPU is about 410 ms a pair)"
    return PairPlan(len(seen), len(groups), sum(len(v) for v in seen.values()), unscored, round(ms, 2), basis,
                    round(unscored * ms / 1000, 1), by_relation)


@dataclass
class LayaHistoryRun:
    targets: int = 0
    groups: int = 0
    groups_done: int = 0
    pairs: int = 0
    scored: int = 0
    already: int = 0
    seconds: float = 0.0
    score_seconds: float = 0.0
    device: str | None = None
    stopped: str | None = None
    finished_at: str | None = None

    @property
    def ms_per_pair(self) -> float | None:
        return round(1000 * self.score_seconds / self.scored, 2) if self.scored else None

    @property
    def summary(self) -> str:
        head = f"stopped early ({self.stopped})" if self.stopped else "finished"
        rate = f", {self.ms_per_pair} ms a pair on {self.device}" if self.ms_per_pair else ""
        return (f"{head}: {self.groups_done} of {self.groups} groups for {self.targets} companies, {self.scored} "
                f"pairs scored, {self.already} already{rate}, {self.seconds:.0f} s")


def last_history_run(ctx: AppContext) -> LayaHistoryRun | None:
    raw = MetaRepo(ctx.conn).get(LAST_RUN)
    return LayaHistoryRun(**json.loads(raw)) if raw else None


class _Cancelled(Exception):
    pass


class _NoGpu(Exception):
    pass


def _gpu_loader(ctx: AppContext, allow_cpu: bool, stop: Callable[[], bool], say: Callable[[str], None],
                beat: Callable[[], None]) -> Callable[[], Scorer]:
    """A loader that never settles for the CPU unless allowed: a model that loads there is closed
    and the GPU is tried again every GPU_WAIT_S (the lock kept alive meanwhile), until it has room
    or `stop`; with no CUDA GPU at all it stops, saying so."""
    cpu_ok = allow_cpu or ctx.config.laya.device == "cpu"

    def load() -> Scorer:
        while True:
            model = laya_service.load_scorer(ctx)
            if cpu_ok or model.device != "cpu":
                return model
            model.close()
            why = getattr(model, "why", "") or ""
            if "no CUDA" in why:
                raise _NoGpu(f"{why.split(';')[0]}: allow the CPU to score there (about {CPU_MS:.0f} ms a pair)")
            say(f"waiting for GPU room: Laya loaded on the CPU (about {CPU_MS:.0f} ms a pair against {GPU_MS:.0f}); "
                f"trying again every {GPU_WAIT_S:.0f} s (allow the CPU to score there)")
            for _ in range(int(GPU_WAIT_S / laya_service.YIELD_STEP_S)):
                if stop():
                    raise _Cancelled
                beat()
                laya_service.wait(laya_service.YIELD_STEP_S)

    return load


def score_history(ctx: AppContext, watchlist: str = DEFAULT_LIST, *, only: Sequence[str] | None = None,
                  own_only: bool = False, allow_cpu: bool = False, scorer: Scorer | None = None,
                  stop: Callable[[], bool] | None = None, busy: Callable[[], str | None] | None = None,
                  on_progress: Callable[[str], None] | None = None) -> LayaHistoryRun:
    """Score every pair `history_pairs` plans that this model has not scored, own groups first,
    holding the `laya_history` lock (one run across processes). A model it loads is closed at the
    end and while `busy` or any process's live model work runs, and never kept on the CPU unless
    `allow_cpu` or Settings (`device = "cpu"`) say so."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    say = on_progress or (lambda _: None)
    halt = stop or (lambda: False)
    with single_flight(ctx, JOB, LEASE_S) as held:
        def waiting() -> str | None:
            held.check()
            return (busy() if busy else None) or history_waits_anywhere(ctx)

        say("planning the pairs")
        groups = history_pairs(ctx, watchlist, only=only, own_only=own_only)
        shared = scorer if scorer is not None else SharedScorer(_gpu_loader(ctx, allow_cpu, halt, say, held.check))
        run = LayaHistoryRun(len({g.target for g in groups}), len(groups))
        began = time.monotonic()
        try:
            for n, g in enumerate(groups, 1):
                if halt():
                    run.stopped = "cancelled"
                    break
                where = f"{g.target} {g.relation} ({g.subject}, group {n}/{len(groups)})"
                done = laya_service.score_articles(ctx, g.target, list(g.ids), relation=g.relation, scorer=shared,
                                                   stop=halt, busy=waiting,
                                                   on_progress=lambda line: say(f"{where}: {line}"))
                run.score_seconds += done.model_seconds
                run.pairs += done.articles
                run.scored += done.scored
                run.already += done.already
                run.device = done.device or run.device
                if done.stopped:
                    run.stopped = "cancelled" if halt() else "stopped while waiting for live work"
                    break
                run.groups_done += 1
        except _Cancelled:
            run.stopped = "cancelled"
        except _NoGpu as exc:
            run.stopped = str(exc)
        finally:
            if scorer is None and isinstance(shared, SharedScorer):
                shared.close()
            run.seconds = round(time.monotonic() - began, 1)
            run.score_seconds = round(run.score_seconds, 2)
            run.finished_at = to_utc_iso(ctx.clock.utcnow())
            MetaRepo(ctx.conn).set(LAST_RUN, json.dumps(asdict(run)))
    return run
