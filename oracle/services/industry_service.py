"""A company's industry map: the companies related to it and how (competitors from the
universe's static fields and the peers, plus every stored link), each relation dated by when it
was first known so a test on day d can leave out what came later. Stored data only."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta

from oracle.analysts.breaks import first_break
from oracle.market.bars import Bar
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.events_service import bar_change, stored_releases
from oracle.services.links_service import company_links, relation_words
from oracle.services.never_list import never_company
from oracle.services.peers_service import MIN_PEERS, choose_peers
from oracle.services.universe_service import resolve_symbol
from oracle.store.articles import ArticleRepo
from oracle.store.bars import BarRepo
from oracle.store.discoveries import DiscoveryEvidence
from oracle.store.universe import ListingProfile, PeerRow, UniverseRepo
from oracle.universe.listings import MARKET_CAP_BANDS, is_exchange_product, normalise_name

__all__ = ["CAP", "Activity", "IndustryMap", "Related", "Relation", "company_activity", "industry_map", "relation_of"]

CAP = 15
"""Competitors kept, biggest market-cap band first; linked companies are all kept."""
HEADLINES = 3
MOVES = (5, 20)
"""Trading days."""


@dataclass(frozen=True)
class Relation:
    kind: str
    """What the related company is to the mapped one: competitor, supplier, customer, partner,
    other (named beside it) or peer (discovery's same industry)."""
    why: str
    source: str
    """industry, industry_group or peers for a competitor from static fields; else what found the link."""
    first_known: str | None
    """ISO time the link was first seen; None for a static field, known on any day."""


@dataclass(frozen=True)
class Related:
    symbol: str
    company_key: str
    name: str | None
    market_cap: str | None
    relations: tuple[Relation, ...]
    link_id: int | None = None
    """The stored link to delete, when there is one."""
    evidence: tuple[DiscoveryEvidence, ...] = ()

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(r.kind for r in self.relations))

    @property
    def first_known(self) -> str | None:
        """When any relation was first known; None when one comes from static fields."""
        known = [r.first_known for r in self.relations]
        return None if None in known else min(known)


@dataclass(frozen=True)
class IndustryMap:
    symbol: str
    """The company's primary listing."""
    name: str
    industry: str | None
    industry_group: str | None
    basis: str | None
    """Which companies count as competitors, in words."""
    note: str | None
    """Why there are no competitors."""
    as_of: date | None
    related: tuple[Related, ...]
    """Linked companies first (most evidence first), then competitors, biggest first."""

    def known_by(self, day: date) -> IndustryMap:
        """The map as known at the end of `day`, without reading the store again: relations
        first known later and evidence published later (or undated) are left out. Linked
        companies keep today's order."""
        end = (day + timedelta(days=1)).isoformat()
        related = []
        for r in self.related:
            relations = tuple(x for x in r.relations if x.first_known is None or x.first_known < end)
            if not relations:
                continue
            linked = any(x.first_known is not None for x in relations)
            evidence = tuple(e for e in r.evidence if e.published_at is not None and e.published_at < end)
            related.append(replace(r, relations=relations, link_id=r.link_id if linked else None,
                                   evidence=evidence if linked else ()))
        return replace(self, as_of=day, related=tuple(related))


@dataclass(frozen=True)
class Activity:
    """What a company is doing now, from stored data; None where nothing is stored."""

    symbol: str
    last_close: date | None
    move_5d: float | None
    move_20d: float | None
    last_earnings: date | None
    next_earnings: date | None
    headlines: tuple[DiscoveryEvidence, ...]


def industry_map(ctx: AppContext, symbol: str, as_of: date | None = None) -> IndustryMap:
    """With `as_of`, links first seen after that day are left out; competitors come from
    today's static fields either way."""
    universe = UniverseRepo(ctx.conn)
    profile = universe.profile(resolve_symbol(ctx, symbol).symbol)
    related: dict[str, Related] = {}
    for link in company_links(ctx, profile.symbol, as_of):
        found = universe.resolve(link.symbol)
        key = found.company_key if found else link.symbol.upper()
        relations = tuple(Relation(r, relation_words(r), source, since)
                          for r, since, source in zip(link.relations, link.since, link.sources))
        related[key] = Related(link.symbol, key, link.name, found.market_cap if found else None, relations,
                               link.id, link.evidence)
    rows, basis, note = _competitors(ctx, profile)
    for row, relations in rows:
        found = universe.resolve(row.symbol)
        if (have := related.get(found.company_key)) is not None:
            related[found.company_key] = Related(have.symbol, have.company_key, have.name, have.market_cap,
                                                 have.relations + tuple(relations), have.link_id, have.evidence)
        else:
            related[found.company_key] = Related(row.symbol, found.company_key, row.name, row.market_cap, tuple(relations))
    return IndustryMap(profile.primary_symbol, profile.name, profile.industry, profile.industry_group, basis, note,
                       as_of, tuple(related.values()))


def relation_of(ctx: AppContext, symbol: str, other: str, as_of: date | None = None) -> Related | None:
    """How `other` is related to `symbol` on its map (as known by `as_of`), or None."""
    key = resolve_symbol(ctx, other).company_key
    return next((r for r in industry_map(ctx, symbol, as_of).related if r.company_key == key), None)


def company_activity(ctx: AppContext, symbols: Iterable[str]) -> dict[str, Activity]:
    """Each company's latest moves, earnings dates and headlines, never fetching."""
    universe, bars, articles = UniverseRepo(ctx.conn), BarRepo(ctx.conn), ArticleRepo(ctx.conn)
    today = ctx.clock.utcnow().date()
    out = {}
    for symbol in symbols:
        found = universe.resolve(symbol)
        listings = [symbol, *(universe.company_symbols(found.company_key) if found else ())]
        primary = universe.profile(symbol).primary_symbol if found else symbol
        history = bars.history(symbol, max(MOVES) + 1) or bars.history(primary, max(MOVES) + 1)
        days = sorted(r.day for r in stored_releases(ctx, symbol)) if found else []
        out[symbol] = Activity(symbol, history[-1].date if history else None, *(_move(history, n) for n in MOVES),
                               max((d for d in days if d <= today), default=None),
                               min((d for d in days if d > today), default=None),
                               tuple(articles.latest_about(list(dict.fromkeys(listings)), HEADLINES)))
    return out


def _move(history: list[Bar], days: int) -> float | None:
    """None with too few closes or a split-sized jump among them."""
    if len(history) <= days:
        return None
    span = history[-days - 1:]
    return None if first_break([b.close for b in span]) is not None else bar_change(span[0], span[-1])


def _competitors(ctx: AppContext, profile: ListingProfile) -> tuple[list[tuple[PeerRow, list[Relation]]], str | None, str | None]:
    """Same industry (its industry group when the industry has too few) and the peers, never
    listed ones left out, capped by market-cap band; with the basis in words, or why none."""
    if profile.type != "equity":
        return [], None, f"{profile.symbol} is a fund: competitors are for companies"
    universe = UniverseRepo(ctx.conn)

    def left_out(r: PeerRow) -> bool:
        return is_exchange_product(r.name) or never_company(ctx, r.symbol, user=True) is not None

    def same(field: str, value: str) -> list[PeerRow]:
        return [r for r in universe.peers(profile.company_key, field, value, CAP * 2) if not left_out(r)]

    found: dict[str, tuple[PeerRow, list[Relation]]] = {}
    basis = None
    rows = same("industry", profile.industry) if profile.industry else []
    field, value = "industry", profile.industry
    if len(rows) < MIN_PEERS and profile.industry_group:
        rows = same("industry_group", profile.industry_group)
        field, value = "industry_group", profile.industry_group
    if rows:
        words = field.replace("_", " ")
        basis = f"{words} {value}" + (f" ({profile.industry} has too few)" if field != "industry" and profile.industry else "")
        for r in rows:
            found[r.symbol] = (r, [Relation("competitor", f"same {words} ({value})", field, None)])
    try:
        choice = choose_peers(ctx, profile.symbol)
    except InvalidQuery:
        choice = None
    if choice is not None:
        for r in choice.rows:
            if left_out(r):
                continue
            found.setdefault(r.symbol, (r, []))[1].append(Relation("competitor", f"a peer ({choice.basis})", "peers", None))
        basis = basis or f"peers ({choice.basis})"
    if not found:
        return [], None, f"{profile.symbol} has no industry, industry group or sector in the universe"
    bars = BarRepo(ctx.conn)
    ranked = sorted(found.values(), key=lambda x: (_band(x[0].market_cap), bars.last_date(x[0].symbol) is None))
    kept: list[tuple[PeerRow, list[Relation]]] = []
    names = [normalise_name(profile.name)]
    for row, relations in ranked:
        name = normalise_name(row.name)
        # Another line of a company already kept ("Alphabet Inc. Class C", a GDR) that the
        # universe lists as a company of its own.
        if any(name == n or name.startswith(n + " ") or n.startswith(name + " ") for n in names):
            continue
        names.append(name)
        kept.append((row, relations))
    return kept[:CAP], basis, None


def _band(market_cap: str | None) -> int:
    return MARKET_CAP_BANDS.index(market_cap) if market_cap in MARKET_CAP_BANDS else len(MARKET_CAP_BANDS)
