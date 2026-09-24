"""Links between companies (supplier, customer, competitor, partner, named beside, same
industry), kept with the articles that showed them. Discovery records them as it reads the
news, the discovery chat will too, and research on a company reads its strongest links."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, UnknownLink
from oracle.services.never_list import never_company
from oracle.services.universe_service import resolve_symbol
from oracle.store.db import to_utc_iso
from oracle.store.discoveries import DiscoveryEvidence
from oracle.store.links import LinkRepo, StoredLink
from oracle.store.universe import Resolved, UniverseRepo

__all__ = [
    "RELATIONS", "SOURCE_DISCOVERY", "CompanyLink", "LinkFound", "company_links", "delete_link", "record_links",
    "research_links",
]

SOURCE_DISCOVERY = "discovery"
# What the other company is to the one it was found from; "other" is named beside it.
RELATIONS = ("supplier", "customer", "competitor", "partner", "other", "peer")
_INVERSE = {"supplier": "customer", "customer": "supplier"}
_WORDS = {"supplier": "a supplier", "customer": "a customer", "competitor": "a competitor", "partner": "a partner",
          "other": "named beside it in the news", "peer": "in the same industry"}


@dataclass(frozen=True)
class LinkFound:
    """`other` is `relation` of `symbol`, as the articles `evidence` show."""

    symbol: str
    other: str
    relation: str
    evidence: tuple[int, ...] = ()


@dataclass(frozen=True)
class CompanyLink:
    """A linked company as the company asked about sees it: `symbol` is each of `relations`
    of it. Every stored link between the two companies, either way round, is gathered here."""

    id: int
    symbol: str
    name: str | None
    relations: tuple[str, ...]
    source: str
    """What found it first."""
    first_seen: str
    last_seen: str
    evidence: tuple[DiscoveryEvidence, ...]
    ids: tuple[int, ...] = ()
    """The stored links it gathers; `id` is the strongest."""

    @property
    def why(self) -> str:
        return " and ".join(_WORDS[r] for r in self.relations)

    @property
    def label(self) -> str:
        return f"{self.name or self.symbol} ({self.symbol}), {self.why}"


def record_links(ctx: AppContext, found: Iterable[LinkFound], source: str) -> int:
    """Keep each link with its evidence, adding new evidence to one already kept. A link the
    user deleted (between any listings of the two companies), a company's link to itself, and
    links to unknown listings or the never list are left out. Returns how many were kept. An
    unknown relation refuses the whole batch before anything is written."""
    found = list(found)
    if bad := sorted({f.relation for f in found} - set(RELATIONS)):
        raise InvalidQuery(f"unknown link {', '.join(map(repr, bad))}; one of {', '.join(RELATIONS)}")
    universe, repo, now = UniverseRepo(ctx.conn), LinkRepo(ctx.conn), to_utc_iso(ctx.clock.utcnow())
    kept = 0
    for f in found:
        a, b = universe.resolve(f.symbol), universe.resolve(f.other)
        if a is None or b is None or a.company_key == b.company_key:
            continue
        if never_company(ctx, a.symbol, user=True) or never_company(ctx, b.symbol, user=True):
            continue
        if repo.deleted(_listings(universe, a), _listings(universe, b)):
            continue
        repo.record(a.symbol, b.symbol, f.relation, source, f.evidence, now)
        kept += 1
    return kept


def company_links(ctx: AppContext, symbol: str, as_of: date | None = None) -> list[CompanyLink]:
    """Companies linked to the one behind any of its listings, most evidence first, then
    newest; none on the never list as it stands now. With `as_of`, only what was known by the
    end of that day: links first seen by then, evidence published by then, last seen capped."""
    universe = UniverseRepo(ctx.conn)
    mine = _listings(universe, resolve_symbol(ctx, symbol))
    before = (as_of + timedelta(days=1)).isoformat() if as_of else None
    groups: dict[str, list[tuple[StoredLink, str]]] = {}
    for link in LinkRepo(ctx.conn).of(mine, before):
        forward = link.symbol.upper() in mine
        other = link.other if forward else link.symbol
        found = universe.resolve(other)
        relation = link.relation if forward else _INVERSE.get(link.relation, link.relation)
        groups.setdefault(found.company_key if found else other.upper(), []).append((link, relation))
    out = []
    for rows in groups.values():
        first = rows[0][0]
        other = first.other if first.symbol.upper() in mine else first.symbol
        found = universe.resolve(other)
        if found and never_company(ctx, found.symbol, user=True):
            continue
        links = [link for link, _ in rows]
        evidence = {e.id: e for link in links for e in link.evidence
                    if before is None or (e.published_at is not None and e.published_at < before)}
        last = max(link.last_seen for link in links)
        out.append(CompanyLink(first.id, other, found.name if found else None,
                               tuple(dict.fromkeys(r for _, r in rows)), first.source,
                               min(link.first_seen for link in links),
                               min(last, as_of.isoformat()) if as_of else last,
                               tuple(sorted(evidence.values(), key=lambda e: e.id)), tuple(link.id for link in links)))
    out.sort(key=lambda x: x.last_seen, reverse=True)
    return sorted(out, key=lambda x: len(x.evidence), reverse=True)


def research_links(ctx: AppContext, symbol: str, limit: int, as_of: date | None = None) -> list[CompanyLink]:
    """The strongest `limit` linked companies with evidence."""
    return [x for x in company_links(ctx, symbol, as_of) if x.evidence][:limit]


def delete_link(ctx: AppContext, symbol: str, link_id: int) -> CompanyLink:
    """Delete every link between the two companies; none is recorded again."""
    link = next((x for x in company_links(ctx, symbol) if link_id in x.ids), None)
    if link is None:
        raise UnknownLink(f"no link {link_id} for {symbol}")
    repo, universe = LinkRepo(ctx.conn), UniverseRepo(ctx.conn)
    stored = repo.get(link_id)

    def company(s: str) -> set[str]:
        found = universe.resolve(s)
        return _listings(universe, found) if found else {s.upper()}

    repo.delete_pair(company(stored.symbol), company(stored.other), to_utc_iso(ctx.clock.utcnow()))
    return link


def _listings(universe: UniverseRepo, found: Resolved) -> set[str]:
    return {s.upper() for s in universe.company_symbols(found.company_key)} | {found.symbol.upper()}
