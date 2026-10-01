"""The stronger reader over the backfilled history (ticket 09): each article read once, for its own
subject company, by a local Ollama model, stored in `laya_scores` under the reader's model tag.
A (article, target) pair whose subject is another company (competitor, industry, ...) reuses the
subject's reading under the pair's relation, so 06's aggregator and test read the reader exactly
as they read Laya; the walk-forward fit gives each relation its sign.

One long job: batched (one call reads `batch` headlines of any companies), cached by (article,
subject, model) so a rerun asks only what is missing, one run across processes (lock), giving way
to live model work and to a scheduled tick (the caller's `busy`), cancelled between batches."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from oracle.research.laya import LayaInput
from oracle.research.reader import BATCH, OllamaReader, ReaderError, reader_tag
from oracle.services import laya_service
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, JobAlreadyRunning
from oracle.services.laya_history_service import HISTORY_WAITS_FOR, JOB as HISTORY_JOB, PairGroup, history_pairs
from oracle.services.jobs_service import single_flight
from oracle.services.watch_service import DEFAULT_LIST
from oracle.store.db import to_utc_iso
from oracle.store.laya import ArticleText, LayaRepo
from oracle.store.meta import MetaRepo

__all__ = ["BAKE_OFF_RATES", "JOB", "PairGroup", "ReaderPlan", "ReaderRun", "last_reader_run", "load_reader",
           "plan_reader", "reader_groups", "reader_tag", "score_reader", "waits_anywhere"]

JOB = "reader"
READER_WAITS_FOR = (*HISTORY_WAITS_FOR, HISTORY_JOB)
"""Job locks (any process) the reader gives way to: what Laya's history scoring waits for, and that scoring
(which goes first: it never waits for the reader, so the two cannot wait on each other)."""
LEASE_S = 1800.0
LAST_RUN = "reader_last_run"
BAKE_OFF_RATES = {"qwen3:4b-instruct-2507-q8_0": 16.6, "gemma4:12b": 3.79, "qwen2.5:32b": 1.02}
"""Headlines a second in the bake-off (batch 25, the RTX 5060 shared with the live app): the
estimate before any run of the configured model."""
UNKNOWN_RATE = 1.0


def reader_groups(ctx: AppContext, watchlist: str = DEFAULT_LIST, *, own_only: bool = False,
                  only: Sequence[str] | None = None) -> list[PairGroup]:
    """The same (article, target) groups 06's history scoring plans: each main-list company's own
    backfill, then (not `own_only`) its map companies' backfill for it."""
    return history_pairs(ctx, watchlist, only=only, own_only=own_only)


def waits_anywhere(ctx: AppContext) -> str | None:
    """Live model work any process holds a lock for (what Laya's history scoring gives way to), or
    a scheduled run due within 30 minutes (or due and not yet run), by name: what the reader leaves
    Ollama to, from the app or the CLI."""
    # Imported here: the activity module imports this one.
    from oracle.services.activity_service import scheduled_run_near

    return laya_service.live_model_work(ctx, READER_WAITS_FOR) or scheduled_run_near(ctx)


def load_reader(ctx: AppContext, model: str | None = None) -> OllamaReader:
    return OllamaReader(model or ctx.config.laya.reader_model, ctx.config.llm.base_url)


@dataclass
class ReaderRun:
    model: str
    groups: int = 0
    subject_articles: int = 0
    """Distinct (subject, article) readings the groups need."""
    scored: int = 0
    already: int = 0
    unparsed: int = 0
    """Left unscored after a retry: asked again next run."""
    copied: int = 0
    """Pairs stored from their subject's reading."""
    seconds: float = 0.0
    model_seconds: float = 0.0
    """Spent waiting on the model only (no waits for live work)."""
    stopped: str | None = None
    finished_at: str | None = None

    @property
    def rate(self) -> float | None:
        return round((self.scored + self.unparsed) / self.model_seconds, 2) if self.model_seconds else None

    @property
    def summary(self) -> str:
        head = f"stopped early ({self.stopped})" if self.stopped else "finished"
        rate = f", {self.rate} headlines a second" if self.rate else ""
        return (f"{head}: {self.scored} of {self.subject_articles} headlines read by {self.model} "
                f"({self.already} already, {self.unparsed} unanswered), {self.copied} pairs from their subject"
                f"{rate}, {self.seconds:.0f} s")


def last_reader_run(ctx: AppContext) -> ReaderRun | None:
    raw = MetaRepo(ctx.conn).get(LAST_RUN)
    return ReaderRun(**json.loads(raw)) if raw else None


@dataclass(frozen=True)
class _Work:
    subject: str
    label: str
    article: ArticleText


def _needed(ctx: AppContext, groups: Sequence[PairGroup], tag: str) -> tuple[int, list[_Work]]:
    """Distinct (subject, article) readings, and those not yet stored, newest article first across
    every subject: a run cut short has read the latest days for every company, not a few
    companies whole (a cross-section needs every company; the cutoff rule counts only late days)."""
    ids: dict[str, dict[int, None]] = {}
    for g in groups:
        ids.setdefault(laya_service.primary(ctx, g.subject), {}).update(dict.fromkeys(g.ids))
    repo, todo = LayaRepo(ctx.conn), []
    for subject, wanted in ids.items():
        _, label = laya_service.named(ctx, subject)
        todo += [_Work(subject, label, a) for a in repo.unscored(subject, tag, list(wanted))]
    todo.sort(key=lambda w: _moment(w.article.known), reverse=True)
    return sum(len(v) for v in ids.values()), todo


def _moment(stamp: str | None) -> datetime:
    return datetime.fromisoformat(stamp).astimezone(timezone.utc) if stamp else datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ReaderPlan:
    model: str
    groups: int
    pairs: int
    """(article, target) pairs the test reads."""
    subject_articles: int
    unscored: int
    rate: float
    basis: str
    estimate_s: float


def plan_reader(ctx: AppContext, watchlist: str = DEFAULT_LIST, *, own_only: bool = False,
                groups: Sequence[PairGroup] | None = None, model: str | None = None) -> ReaderPlan:
    """How many headlines a run would read and how long it would take, reading only."""
    groups = list(groups) if groups is not None else reader_groups(ctx, watchlist, own_only=own_only)
    name = model or ctx.config.laya.reader_model
    tag = reader_tag(name)
    total, todo = _needed(ctx, groups, tag)
    pairs = {(laya_service.primary(ctx, g.target), i) for g in groups for i in g.ids}
    last = last_reader_run(ctx)
    if last and last.model == tag and last.rate:
        rate, basis = last.rate, f"measured by the last run ({last.finished_at})"
    elif name in BAKE_OFF_RATES:
        rate, basis = BAKE_OFF_RATES[name], f"the bake-off's rate for {name}"
    else:
        rate, basis = UNKNOWN_RATE, f"a guess: {name} was not in the bake-off"
    return ReaderPlan(tag, len(groups), len(pairs), total, len(todo), rate, basis, round(len(todo) / rate, 1))


def _eta(done: int, total: int, seconds: float) -> str:
    if not done or not seconds:
        return ""
    rate = done / seconds
    return f", {rate:.2f} a second, about {(total - done) / rate / 3600:.1f} h left"


def score_reader(ctx: AppContext, watchlist: str = DEFAULT_LIST, *, own_only: bool = False,
                 groups: Sequence[PairGroup] | None = None, reader=None, batch: int = BATCH,
                 stop: Callable[[], bool] | None = None, busy: Callable[[], str | None] | None = None,
                 on_progress: Callable[[str], None] | None = None) -> ReaderRun:
    """Read every (subject, article) the groups need that the model has not read, then store each
    pair from its subject's reading. A target's own articles are only ever its own reading, even
    when a run stops before reaching them. While `busy` names live work the model is unloaded from
    Ollama (its memory goes to the live model) and loaded again after."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    say = on_progress or (lambda _: None)
    halt = stop or (lambda: False)
    owned = reader is None
    with single_flight(ctx, JOB, LEASE_S) as held:
        say("planning the headlines")
        groups = list(groups) if groups is not None else reader_groups(ctx, watchlist, own_only=own_only)
        reader = reader or load_reader(ctx)
        run = ReaderRun(reader.tag, len(groups))
        repo, began, model_s = LayaRepo(ctx.conn), time.monotonic(), reader.stats.seconds
        try:
            run.subject_articles, todo = _needed(ctx, groups, reader.tag)
            run.already = run.subject_articles - len(todo)
            for i in range(0, len(todo), batch):
                if halt():
                    run.stopped = "cancelled"
                    break
                held.check()
                if busy and (other := busy()):
                    reader.release()
                    while other and not halt():
                        say(f"{run.scored} of {len(todo)} read; waiting for {other}")
                        laya_service.wait(laya_service.YIELD_STEP_S)
                        held.check()
                        other = busy()
                    if other:
                        run.stopped = "cancelled"
                        break
                part = todo[i:i + batch]
                answers = reader.score([LayaInput(w.label, w.article.title) for w in part])
                run.model_seconds = reader.stats.seconds - model_s
                at = to_utc_iso(ctx.clock.utcnow())
                by_subject: dict[str, list] = {}
                for w, s in zip(part, answers):
                    if s is None:
                        run.unparsed += 1
                    else:
                        by_subject.setdefault(w.subject, []).append((w.article.id, s))
                for subject, scored in by_subject.items():
                    repo.save(subject, reader.tag, "own", scored, at)
                    run.scored += len(scored)
                done = run.scored + run.unparsed
                say(f"{done} of {len(todo)} read ({run.unparsed} unanswered)"
                    + _eta(done, len(todo), run.model_seconds))
            run.copied = _copy_pairs(ctx, groups, reader.tag)
        except ReaderError as exc:
            run.stopped = f"failed: {exc}"
            raise InvalidQuery(str(exc)) from exc
        except JobAlreadyRunning:
            run.stopped = "taken over by another process"
            raise
        except Exception as exc:
            run.stopped = f"failed: {exc}"
            raise
        finally:
            if owned:
                reader.close()
            run.seconds = round(time.monotonic() - began, 1)
            run.model_seconds = round(run.model_seconds, 2)
            run.finished_at = to_utc_iso(ctx.clock.utcnow())
            MetaRepo(ctx.conn).set(LAST_RUN, json.dumps(asdict(run)))
    return run


def _copy_pairs(ctx: AppContext, groups: Sequence[PairGroup], tag: str) -> int:
    """Each pair whose subject is another company, from the subject's reading, never over the
    target's own articles; an earlier group's relation wins, as in 06's history scoring."""
    own: dict[str, set[int]] = {}
    for g in groups:
        if g.relation == "own":
            own.setdefault(laya_service.primary(ctx, g.target), set()).update(g.ids)
    repo, at, copied = LayaRepo(ctx.conn), to_utc_iso(ctx.clock.utcnow()), 0
    for g in groups:
        target, subject = laya_service.primary(ctx, g.target), laya_service.primary(ctx, g.subject)
        if target == subject:
            continue
        ids = [i for i in dict.fromkeys(g.ids) if i not in own.get(target, set())]
        copied += repo.copy(subject, target, tag, g.relation, ids, at)
    return copied
