"""Daily industry research: for held, watched and main-list companies and each one's industry map,
search each company's new headlines (Google News, the last few days, by its history search name,
paced through the shared gate), score them with Laya per (article, target) pair under the
relation the map gives (own, competitor, supplier, customer, industry) and store the target's
daily features (`laya_features`). The tick runs it for the daily set; the Company tab's Research
industry button for one company. A run has a request and a time budget, steps aside for live model
work (closing its model meanwhile) and keeps whatever it finished.

Point in time: articles count on the day they were known (published_at, else fetched_at);
undated search results are dropped, never dated by the day fetched. A feature of day d acts no
earlier than the next session."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.research.laya import Scorer
from oracle.research.news import GOOGLE_NEWS, FeedUnreadable, google_news_params, parse_google_news
from oracle.services import laya_service
from oracle.services.context import AppContext
from oracle.store.discoveries import DISCOVERED_LIST
from oracle.services.errors import InvalidQuery, UnknownWatchlist
from oracle.services.history_names import SearchName
from oracle.services.history_service import plain_company, search_for
from oracle.services.http import open_client
from oracle.services.industry_service import industry_map
from oracle.services.jobs_service import job_statuses, single_flight
from oracle.services.laya_features import DailyFeature, known_day, signed, store_features, stored_features
from oracle.services.laya_service import MODEL_TAG, ScoredArticle, SharedScorer, company_articles, laya_scores, primary
from oracle.services.watch_service import DEFAULT_LIST, watchlist_symbols
from oracle.store.articles import ArticleRepo
from oracle.store.db import to_utc_iso
from oracle.store.industry_news import IndustryNewsRepo
from oracle.store.paper import PaperRepo
from oracle.store.watchlists import WatchlistRepo

__all__ = ["BUTTON_FRESH_H", "EVENTS_SHOWN", "FRESH_H", "KIND_RELATION", "LOOKBACK_DAYS", "CompanyNews", "IndustryNews",
           "IndustryRun", "ScoredEvent", "daily_targets", "industry_news", "live_model_work", "relation_for_kinds",
           "research_industry"]

log = logging.getLogger(__name__)

JOB = "industry_research"
LEASE_S = 3600.0
NEWS = "google_news"
LOOKBACK_DAYS = 3
"""Days of headlines a search asks for and a run scores: yesterday's late news and a missed tick."""
FRESH_H = 12.0
"""A company searched within this many hours is not searched again by the tick."""
BUTTON_FRESH_H = 1.0
WAIT_STEP_S = 5.0
FAILED_IN_A_ROW = 5
EVENTS_SHOWN = 3
KIND_RELATION = {"supplier": "supplier", "customer": "customer", "competitor": "competitor", "peer": "competitor",
                 "partner": "industry", "other": "industry"}
"""A map relation (what the related company is to the target) -> the feature its news counts in."""
_PRECEDENCE = ("supplier", "customer", "competitor", "industry")
MODEL_WORK = ("research", "discovery_chat", "discovery")
"""Locks of live model work the tick's stage gives way to (in any process)."""


def relation_for_kinds(kinds: Sequence[str]) -> str:
    """A named tie (supplier, customer) over a competitor, a competitor over a looser tie."""
    found = {KIND_RELATION.get(k, "industry") for k in kinds}
    return next(r for r in _PRECEDENCE if r in found) if found else "industry"


@dataclass(frozen=True)
class _Subject:
    symbol: str
    relation: str
    search: SearchName


@dataclass
class IndustryRun:
    targets: tuple[str, ...] = ()
    reached: int = 0
    """Targets whose news was scored and features stored."""
    searched: int = 0
    requests: int = 0
    found: int = 0
    kept: int = 0
    undated: int = 0
    outside: int = 0
    scored: int = 0
    already: int = 0
    feature_rows: int = 0
    search_seconds: float = 0.0
    score_seconds: float = 0.0
    seconds: float = 0.0
    device: str | None = None
    stopped: str | None = None
    """Why it ended before every target: cancelled, the time budget, Laya would not load."""
    searches_stopped: str | None = None
    """Why searching stopped early (the request budget, a resting source); scoring went on."""
    failures: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        head = f"{self.reached} of {len(self.targets)} companies"
        parts = [f"{self.searched} searched ({self.requests} requests, {self.kept} new headlines kept)",
                 f"{self.scored} pairs scored" + (f" on {self.device}" if self.device else ""),
                 f"{self.feature_rows} feature rows", f"{self.seconds:.0f} s"]
        if self.searches_stopped:
            parts.append(f"searching stopped: {self.searches_stopped}")
        if self.stopped:
            parts.append(f"stopped: {self.stopped}")
        if self.failures:
            parts.append(f"{len(self.failures)} failed, first: {self.failures[0]}")
        return f"{head}: " + ", ".join(parts)


def daily_targets(ctx: AppContext) -> list[str]:
    """Held first, then the main list, the user's other lists and the discovered list: each
    company once (its primary listing), never a fund or a never-listed company."""
    repo = WatchlistRepo(ctx.conn)
    try:
        main = watchlist_symbols(ctx, DEFAULT_LIST)[1]
    except UnknownWatchlist:
        main = []
    discovered = sorted(set(repo.all_symbols()) - set(repo.symbols_outside(DISCOVERED_LIST)))
    out: dict[str, None] = {}
    for symbol in (*PaperRepo(ctx.conn).held_symbols(), *main, *repo.symbols_outside(DISCOVERED_LIST), *discovered):
        p = plain_company(ctx, symbol, strict=False)
        if p is not None:
            out.setdefault(p.primary_symbol)
    return list(out)


def live_model_work(ctx: AppContext) -> str | None:
    """Live model work another job holds a lock for (research, a question, discovery), by name."""
    return next((s.job.replace("_", " ") for s in job_statuses(ctx) if s.job in MODEL_WORK and s.stale_reason is None),
                None)


async def research_industry(ctx: AppContext, symbols: Sequence[str] | None = None, *, max_requests: int | None = None,
                            minutes: float | None = None, fresh_h: float = FRESH_H,
                            scorer: Scorer | None = None, stop: Callable[[], bool] | None = None,
                            busy: Callable[[], str | None] | None = None,
                            on_progress: Callable[[str], None] | None = None) -> IndustryRun:
    """Search, score and store features for `symbols` (default the daily set) and their maps,
    within `max_requests` searches and `minutes` (default Settings' [laya] industry budget). Laya is
    loaded when first needed and closed while `busy` names live model work and at the end."""
    laya = ctx.config.laya
    if not laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    targets = [primary(ctx, s) for s in symbols] if symbols else daily_targets(ctx)
    for s in targets:
        if plain_company(ctx, s, strict=False) is None:
            raise InvalidQuery(f"{s} is a fund or on your never list: no industry research")
    with single_flight(ctx, JOB, LEASE_S) as held:
        run = _Run(ctx, held.check, list(dict.fromkeys(targets)), laya.industry_requests if max_requests is None
                   else max_requests, (laya.industry_minutes if minutes is None else minutes) * 60, fresh_h,
                   scorer, stop, busy, on_progress or (lambda _: None))
        try:
            await run.all()
        finally:
            run.close()
    return run.result


class _Stopped(Exception):
    pass


class _Run:
    def __init__(self, ctx: AppContext, heartbeat, targets: list[str], max_requests: int, budget_s: float,
                 fresh_h: float, scorer, stop, busy, say) -> None:
        self.ctx, self.heartbeat, self.max_requests, self.fresh_h = ctx, heartbeat, max_requests, fresh_h
        self.scorer = scorer if scorer is not None else SharedScorer(lambda: laya_service.load_scorer(ctx))
        self.stop, self.busy, self.say = stop or (lambda: False), busy, say
        self.repo, self.articles = IndustryNewsRepo(ctx.conn), ArticleRepo(ctx.conn)
        self.result = IndustryRun(tuple(targets))
        self.started = time.monotonic()
        self.deadline = ctx.clock.utcnow() + timedelta(seconds=budget_s)
        self.budget_min = round(budget_s / 60)
        now = ctx.clock.utcnow()
        self.since_day = now.date() - timedelta(days=LOOKBACK_DAYS)
        self.since = datetime.combine(self.since_day, datetime.min.time(), timezone.utc)
        self.searched_now: set[str] = set()
        self.failed_in_a_row = 0

    async def all(self) -> None:
        try:
            async with open_client(self.ctx) as client:
                for n, target in enumerate(self.result.targets, 1):
                    self._check()
                    subjects = self._subjects(target)
                    for s in subjects:
                        await self._search(client, s, f"{target} ({n}/{len(self.result.targets)})")
                    self._score(target, subjects)
                    self.result.feature_rows += store_features(self.ctx, target, self.since_day,
                                                               self.ctx.clock.utcnow().date(),
                                                               model=self.scorer.tag)
                    self.result.reached += 1
        except _Stopped:
            pass
        self.result.seconds = round(time.monotonic() - self.started, 1)

    def _subjects(self, target: str) -> list[_Subject]:
        own = plain_company(self.ctx, target, strict=False)
        out = {target: _Subject(target, "own", search_for(self.ctx, target, own.name))}
        for r in industry_map(self.ctx, target).related:
            p = plain_company(self.ctx, r.symbol)
            if p is not None and p.primary_symbol not in out:
                out[p.primary_symbol] = _Subject(p.primary_symbol, relation_for_kinds(r.kinds),
                                                 search_for(self.ctx, p.primary_symbol, p.name))
        return list(out.values())

    async def _search(self, client, s: _Subject, where: str) -> None:
        if s.search.skip or s.symbol in self.searched_now or self.result.searches_stopped:
            return
        last = self.repo.fetched_at(s.symbol)
        now = self.ctx.clock.utcnow()
        if last and now - datetime.fromisoformat(last) < timedelta(hours=self.fresh_h):
            return
        if self.result.requests >= self.max_requests:
            self.result.searches_stopped = f"request budget ({self.max_requests})"
            return
        await self._wait()
        self.say(f"{where}: searching {s.symbol} news; {self.result.requests} requests")
        began = time.monotonic()
        self.searched_now.add(s.symbol)
        query = f"{s.search.query} after:{self.since_day.isoformat()}"
        self.result.requests += 1
        try:
            resp = await client.get(NEWS, GOOGLE_NEWS, params=google_news_params(query))
            if resp.status_code != 200:
                raise RequestFailed(NEWS, resp.status_code, f"HTTP {resp.status_code}")
            found = parse_google_news(resp.text)
        except CircuitOpen as exc:
            self.result.searches_stopped = f"{NEWS} resting: {exc}"
            return
        except (RequestFailed, FeedUnreadable) as exc:
            self.result.failures.append(f"{s.symbol} news: {exc}")
            self.failed_in_a_row += 1
            if self.failed_in_a_row >= FAILED_IN_A_ROW:
                self.result.searches_stopped = f"{FAILED_IN_A_ROW} searches failed in a row"
            return
        finally:
            self.result.search_seconds += time.monotonic() - began
        self.failed_in_a_row = 0
        dated = [a for a in found if a.published is not None]
        inside = [a for a in dated if self.since <= a.published <= now + timedelta(hours=1)]
        ids = self.articles.save(inside, NEWS, to_utc_iso(now), query)
        self.repo.record_fetch(s.symbol, ids, len(found), to_utc_iso(now))
        self.result.searched += 1
        self.result.found += len(found)
        self.result.kept += len(ids)
        self.result.undated += len(found) - len(dated)
        self.result.outside += len(dated) - len(inside)

    def _score(self, target: str, subjects: list[_Subject]) -> None:
        """Own news first, so an article about the target and a related company counts as its own."""
        since = to_utc_iso(self.since)
        taken: set[int] = set()
        groups: list[tuple[str, list[tuple[int, str]]]] = []
        for relation in ("own", *_PRECEDENCE):
            pairs = []
            for s in (x for x in subjects if x.relation == relation):
                ids = self.repo.recent(s.symbol, since)
                if relation == "own":
                    ids += self.repo.known_since(company_articles(self.ctx, target), since)
                pairs += [(i, s.symbol) for i in dict.fromkeys(ids) if i not in taken]
                taken.update(i for i, _ in pairs)
            if pairs:
                groups.append((relation, pairs))
        for relation, pairs in groups:
            self.repo.set_subjects(target, pairs)
            began = time.monotonic()
            try:
                done = laya_service.score_articles(
                    self.ctx, target, [i for i, _ in pairs], relation=relation, scorer=self.scorer,
                    stop=self._should_stop, busy=self.busy,
                    on_progress=lambda line: self.say(f"{target} {relation}: {line}"))
            except InvalidQuery as exc:
                self.result.stopped = f"Laya: {exc}"
                raise _Stopped from exc
            finally:
                self.result.score_seconds += time.monotonic() - began
            self.result.scored += done.scored
            self.result.already += done.already
            self.result.device = done.device or self.result.device
            if done.stopped:
                self._check()
                # Stopped with nothing to say why: live work outlasted a cancel.
                self.result.stopped = self.result.stopped or "stopped while waiting"
                raise _Stopped

    def _should_stop(self) -> bool:
        return self.stop() or self.ctx.clock.utcnow() >= self.deadline

    def _check(self) -> None:
        self.heartbeat()
        if self.stop():
            self.result.stopped = "cancelled"
            raise _Stopped
        if self.ctx.clock.utcnow() >= self.deadline:
            self.result.stopped = f"time budget ({self.budget_min} min)"
            raise _Stopped

    async def _wait(self) -> None:
        """Between searches: give way to live model work, the model closed meanwhile."""
        self._check()
        while self.busy and (other := self.busy()):
            if isinstance(self.scorer, SharedScorer):
                self.scorer.close()
            self.say(f"waiting for {other} to finish")
            await self.ctx.clock.sleep(WAIT_STEP_S)
            self._check()

    def close(self) -> None:
        """A plain scorer the caller passed stays the caller's to close."""
        if isinstance(self.scorer, SharedScorer):
            self.scorer.close()


@dataclass(frozen=True)
class ScoredEvent:
    article_id: int
    title: str
    url: str
    outlet: str | None
    known_at: str
    relation: str
    direction: str
    direction_prob: float
    signed: float
    event: str
    event_prob: float


@dataclass(frozen=True)
class CompanyNews:
    symbol: str
    relation: str
    events: tuple[ScoredEvent, ...]
    """Its latest scored events, newest first."""
    day_articles: int
    day_score: float
    """What its news known on the day added to the target's feature."""


@dataclass(frozen=True)
class IndustryNews:
    symbol: str
    day: date
    model: str
    features: tuple[DailyFeature, ...]
    """The target's features on `day`."""
    recent: tuple[DailyFeature, ...]
    own: CompanyNews
    related: tuple[CompanyNews, ...]
    """Related companies with scored news, the day's biggest movers first."""
    last_searched: str | None
    caveat: str = ("Laya's probabilities are uncalibrated; its relevance is near chance, so no article is left out "
                   "for it. A day's features act from the next session.")


def industry_news(ctx: AppContext, symbol: str, day: date | None = None, days: int = 7,
                  model: str = MODEL_TAG) -> IndustryNews:
    """Stored data only: the company's features on `day` (today UTC) and the `days` before, and
    each related company's latest scored events with what they moved on `day`."""
    target = primary(ctx, symbol)
    day = day or ctx.clock.utcnow().date()
    subjects = IndustryNewsRepo(ctx.conn).subjects(target)
    by: dict[str, list[ScoredArticle]] = {}
    for s in laya_scores(ctx, target, model=model):
        who = target if s.relation == "own" else subjects.get(s.article_id)
        if who is not None and known_day(s.known_at) <= day:
            by.setdefault(who, []).append(s)

    def news(who: str, scores: list[ScoredArticle]) -> CompanyNews:
        today = [s for s in scores if known_day(s.known_at) == day]
        events = tuple(ScoredEvent(s.article_id, s.title, s.url, s.outlet, s.known_at, s.relation, s.direction,
                                   s.direction_probs.get(s.direction, 0.0), round(signed(s), 4), s.event, s.event_prob)
                       for s in scores[:EVENTS_SHOWN])
        return CompanyNews(who, scores[0].relation if scores else "own", events, len(today),
                           round(sum(signed(s) for s in today), 4))

    related = sorted((news(who, scores) for who, scores in by.items() if who != target),
                     key=lambda c: c.events[0].known_at, reverse=True)
    related.sort(key=lambda c: (-abs(c.day_score), -c.day_articles))
    recent = stored_features(ctx, target, day - timedelta(days=days), day, model=model)
    return IndustryNews(target, day, model, tuple(r for r in recent if r.day == day), tuple(recent),
                        news(target, by.get(target, [])), tuple(related),
                        IndustryNewsRepo(ctx.conn).fetched_at(target))
