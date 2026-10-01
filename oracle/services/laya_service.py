"""Laya scores stored articles per (article, target company, model): how likely the news is
about the target, which way it points, and its event type. The one place that loads the model,
picks its device, batches, gives way to live model work and tags every score with the model.

Point in time: an article is known at `published_at` when it has one, else `fetched_at`; history
use (`history=True`) leaves out articles with no `published_at`, never dating them by fetch day.
A score known on day d acts no earlier than the next session's open (the caller's lag)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from oracle.research.laya import MODEL_TAG, LayaInput, LayaModel, LayaUnavailable, Scorer
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.jobs_service import job_statuses
from oracle.services.universe_service import resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.laya import LayaRepo
from oracle.store.universe import UniverseRepo

__all__ = ["BATCH", "MODEL_TAG", "MODEL_WORK", "RELATIONS", "ScoreRun", "ScoredArticle", "SharedScorer", "YIELD_STEP_S",
           "company_articles", "laya_scores", "live_model_work", "load_scorer", "named", "primary", "score_articles",
           "score_company"]

log = logging.getLogger(__name__)

RELATIONS = ("own", "competitor", "supplier", "customer", "industry")
"""What the article's subject is to the target: "supplier" is news about one of the target's
suppliers (competitor and industry read the same either way)."""
BATCH = 16
YIELD_STEP_S = 5.0
SNIPPET_CHARS = 600
"""Headline and snippet fit well inside the checkpoint's 1024-token context."""
wait = time.sleep


@dataclass(frozen=True)
class ScoreRun:
    symbol: str
    model: str
    device: str | None
    """None when nothing needed the model."""
    articles: int
    scored: int
    already: int
    seconds: float
    stopped: bool = False
    model_seconds: float = 0.0
    """Time spent scoring alone: no waits for live work, no model loads."""


@dataclass(frozen=True)
class ScoredArticle:
    article_id: int
    title: str
    url: str
    outlet: str | None
    published_at: str | None
    known_at: str
    """published_at, else fetched_at: when the news was known."""
    dated: bool
    """Has its own published_at, so history tests may use it."""
    relation: str
    relevant: float
    direction: str
    direction_probs: dict[str, float]
    event: str
    event_prob: float
    model: str
    scored_at: str


MODEL_WORK = ("research", "discovery_chat", "discovery")
"""Job locks of live model work Laya gives way to: research, a question, discovery."""


def live_model_work(ctx: AppContext, locks: Sequence[str] = MODEL_WORK) -> str | None:
    """Live model work a job in any process holds one of `locks` for, by name; stale locks block
    nothing."""
    return next((s.job.replace("_", " ") for s in job_statuses(ctx) if s.job in locks and s.stale_reason is None), None)


def load_scorer(ctx: AppContext) -> Scorer:
    """The real model on the device Settings picks; tests replace this."""
    try:
        return LayaModel(ctx.config.laya.device, BATCH)
    except LayaUnavailable as exc:
        raise InvalidQuery(str(exc)) from exc


class SharedScorer:
    """One model for many `score_articles` calls (a run over many companies): loaded when first
    asked to score, closed by `score_articles` while live model work runs and loaded again after,
    and closed by its owner when the run ends."""

    def __init__(self, load: Callable[[], Scorer], tag: str = MODEL_TAG) -> None:
        self._load, self.tag = load, tag
        self._model: Scorer | None = None
        self.device: str | None = None
        self.loads = 0

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def warm(self) -> None:
        """Load the model now, if it is not loaded."""
        if self._model is None:
            self._model = self._load()
            self.loads += 1
            self.device = self._model.device

    def score(self, items: list[LayaInput]) -> list:
        self.warm()
        return self._model.score(items)

    def close(self) -> None:
        if self._model is not None:
            self._model.close()
            self._model = None


@dataclass(frozen=True)
class _Company:
    symbol: str
    """The primary listing's: scores are stored under it, so every listing reads the same."""
    label: str
    symbols: list[str]


def _company(ctx: AppContext, symbol: str) -> _Company:
    found = resolve_symbol(ctx, symbol)
    universe = UniverseRepo(ctx.conn)
    primary = universe.primary_symbol(found.company_key) or found.symbol
    return _Company(primary, f"{found.name} ({primary})", universe.company_symbols(found.company_key) or [found.symbol])


def primary(ctx: AppContext, symbol: str) -> str:
    """The company's primary listing, under which its scores are stored."""
    return _company(ctx, symbol).symbol


def named(ctx: AppContext, symbol: str) -> tuple[str, str]:
    """The company's primary listing and how a model is told it ("Name (SYMBOL)")."""
    c = _company(ctx, symbol)
    return c.symbol, c.label


def company_articles(ctx: AppContext, symbol: str) -> list[int]:
    """The stored articles about a company: what research on any of its listings read, and its
    SEC filings."""
    return LayaRepo(ctx.conn).company_articles(_company(ctx, symbol).symbols)


def score_company(ctx: AppContext, symbol: str, **kw) -> ScoreRun:
    """Score every stored article about the company that this model has not scored for it."""
    return score_articles(ctx, symbol, company_articles(ctx, symbol), **kw)


def score_articles(ctx: AppContext, symbol: str, article_ids: list[int], *, relation: str = "own",
                   scorer: Scorer | None = None, batch: int = BATCH, stop: Callable[[], bool] | None = None,
                   busy: Callable[[], str | None] | None = None,
                   on_progress: Callable[[str], None] | None = None) -> ScoreRun:
    """Score `article_ids` for `symbol` as `relation`, a batch at a time, each batch kept as it
    finishes. Without a `scorer` the model is loaded only when something needs scoring, and closed
    after. `busy` names live model work (research, a tick): before each batch it waits while that
    runs, closing a model it loaded (or a SharedScorer's) so the GPU is Ollama's meanwhile. `stop`
    ends it between batches."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    if relation not in RELATIONS:
        raise InvalidQuery(f"relation must be one of {', '.join(RELATIONS)}, got {relation!r}")
    listing = _company(ctx, symbol)
    repo = LayaRepo(ctx.conn)
    owned = scorer is None
    tag = MODEL_TAG if owned else scorer.tag
    ids = list(dict.fromkeys(article_ids))
    todo = repo.unscored(listing.symbol, tag, ids)
    started, scored, stopped, device, model_s = time.monotonic(), 0, False, None, 0.0
    say = on_progress or (lambda _: None)
    try:
        for i in range(0, len(todo), batch):
            if stop and stop():
                stopped = True
                break
            if busy and (other := busy()):
                if owned and scorer is not None:
                    scorer.close()
                    scorer = None
                elif isinstance(scorer, SharedScorer):
                    scorer.close()
                while other and not (stop and stop()):
                    say(f"{scored} of {len(todo)} scored; waiting for {other} to finish")
                    wait(YIELD_STEP_S)
                    other = busy()
                if other:
                    stopped = True
                    break
            if scorer is None:
                scorer = load_scorer(ctx)
            if isinstance(scorer, SharedScorer):
                scorer.warm()
            part = todo[i:i + batch]
            began = time.monotonic()
            scores = scorer.score([LayaInput(listing.label, a.title, (a.snippet or "")[:SNIPPET_CHARS] or None)
                                   for a in part])
            model_s += time.monotonic() - began
            device = scorer.device
            repo.save(listing.symbol, tag, relation, [(a.id, s) for a, s in zip(part, scores)],
                      to_utc_iso(ctx.clock.utcnow()))
            scored += len(part)
            say(f"{scored} of {len(todo)} scored on {device}")
    finally:
        if owned and scorer is not None:
            scorer.close()
    return ScoreRun(listing.symbol, tag, device, len(ids), scored, len(ids) - len(todo),
                    round(time.monotonic() - started, 2), stopped, round(model_s, 3))


def laya_scores(ctx: AppContext, symbol: str, *, model: str = MODEL_TAG, history: bool = False,
                known_by: str | None = None) -> list[ScoredArticle]:
    """A company's scores by `model`, newest known first. `history` keeps only dated articles;
    `known_by` (UTC ISO) only those known by then."""
    out = []
    for s in LayaRepo(ctx.conn).scores(_company(ctx, symbol).symbol, model):
        known = s.published_at or s.fetched_at
        if (history and s.published_at is None) or (known_by is not None and known > known_by):
            continue
        out.append(ScoredArticle(s.article_id, s.title, s.url, s.outlet, s.published_at, known,
                                 s.published_at is not None, s.relation, s.relevant, s.direction, s.direction_probs,
                                 s.event, s.event_prob, s.model, s.scored_at))
    return sorted(out, key=lambda s: (s.known_at, s.article_id), reverse=True)
