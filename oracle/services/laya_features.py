"""Laya's daily features per company: for each day and relation (own, competitor, supplier,
customer, industry), how many scored articles, the sum of direction x its probability (up +p,
down -p, none 0), and per event type the count and the same signed sum. One aggregator for the live tick and for history
tests (06), over any date range.

Point in time: an article counts on the UTC day it was known (published_at, else fetched_at);
`history` leaves out articles with no published_at. A feature of day d is usable no earlier than
the next session's open: `usable_on(rows, session)` keeps days before the session's day.
Relevance is never a filter (near chance on the sanity set)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from oracle.research.laya import EVENT_TYPES
from oracle.services.context import AppContext
from oracle.services.laya_service import MODEL_TAG, RELATIONS, ScoredArticle, laya_scores, primary
from oracle.store.db import to_utc_iso
from oracle.store.industry_news import DailyRow, IndustryNewsRepo

__all__ = ["SIGN", "DailyFeature", "daily_features", "known_day", "signed", "store_features", "stored_features",
           "usable_on"]

SIGN = {"up": 1.0, "down": -1.0, "none": 0.0}


@dataclass(frozen=True)
class DailyFeature:
    symbol: str
    day: date
    """The UTC day the articles were known; usable from the next session."""
    relation: str
    articles: int
    score: float
    """Sum over the day's articles of direction x its probability."""
    events: dict[str, int] = field(default_factory=dict)
    """Event type -> articles; types with none are left out."""
    event_scores: dict[str, float] = field(default_factory=dict)
    """Event type -> the sum of direction x its probability over those articles (same keys as `events`)."""
    relevance: float = field(default=0.0, compare=False)
    """Sum of the articles' relevance: 06 reports it as a secondary figure (never a filter). Not
    stored in `laya_daily`."""


def signed(s: ScoredArticle) -> float:
    """+p for up, -p for down, 0 for none, p being the chosen direction's probability."""
    return SIGN.get(s.direction, 0.0) * s.direction_probs.get(s.direction, 0.0)


def known_day(known_at: str) -> date:
    return datetime.fromisoformat(known_at).astimezone(timezone.utc).date()


def daily_features(ctx: AppContext, symbol: str, start: date, end: date, *, model: str = MODEL_TAG,
                   history: bool = False,
                   relation_for: Callable[[ScoredArticle], str | None] | None = None,
                   day_for: Callable[[ScoredArticle], date] | None = None) -> list[DailyFeature]:
    """The company's features for each day from `start` to `end` (inclusive) with any scored
    article, by day then relation; a missing day or relation had none. `relation_for` overrides
    the stored relation of each score (None leaves it out): 06 uses it to count a link only from
    when it was known, or to leave Laya-typed links to a sensitivity run. `day_for` overrides the
    day an article counts on (06: its first usable decision day for the listing)."""
    groups: dict[tuple[date, str], list[ScoredArticle]] = {}
    for s in laya_scores(ctx, symbol, model=model, history=history):
        day = day_for(s) if day_for else known_day(s.known_at)
        if not start <= day <= end:
            continue
        relation = relation_for(s) if relation_for else s.relation
        if relation is not None:
            groups.setdefault((day, relation), []).append(s)
    listing = primary(ctx, symbol)
    out = []
    for (day, relation), scores in sorted(groups.items(), key=lambda x: (x[0][0], _order(x[0][1]))):
        events: dict[str, int] = {}
        sums: dict[str, float] = {}
        for s in scores:
            events[s.event] = events.get(s.event, 0) + 1
            sums[s.event] = sums.get(s.event, 0.0) + signed(s)
        order = [e for e in EVENT_TYPES if e in events] + [e for e in events if e not in EVENT_TYPES]
        out.append(DailyFeature(listing, day, relation, len(scores), round(sum(signed(s) for s in scores), 6),
                                {e: events[e] for e in order}, {e: round(sums[e], 6) for e in order},
                                round(sum(s.relevant for s in scores), 6)))
    return out


def usable_on(rows: Iterable[DailyFeature], session: date) -> list[DailyFeature]:
    """The features a decision for `session`'s open may use: days before it."""
    return [r for r in rows if r.day < session]


def store_features(ctx: AppContext, symbol: str, start: date, end: date, *, model: str = MODEL_TAG) -> int:
    """Recompute and store the features of `start`..`end`, replacing what was stored for those days;
    the rows stored."""
    rows = daily_features(ctx, symbol, start, end, model=model)
    IndustryNewsRepo(ctx.conn).save_daily(
        primary(ctx, symbol), model, start.isoformat(), end.isoformat(),
        [DailyRow(r.symbol, r.day.isoformat(), r.relation, r.articles, r.score, r.events, r.event_scores)
         for r in rows],
        to_utc_iso(ctx.clock.utcnow()))
    return len(rows)


def stored_features(ctx: AppContext, symbol: str, start: date, end: date, *,
                    model: str = MODEL_TAG) -> list[DailyFeature]:
    rows = IndustryNewsRepo(ctx.conn).daily(primary(ctx, symbol), model, start.isoformat(), end.isoformat())
    return sorted((DailyFeature(r.symbol, date.fromisoformat(r.day), r.relation, r.articles, r.score, r.events,
                                r.event_scores)
                   for r in rows), key=lambda r: (r.day, _order(r.relation)))


def _order(relation: str) -> int:
    return RELATIONS.index(relation) if relation in RELATIONS else len(RELATIONS)
