"""Supplier, customer, competitor and partner ties read from documents: stored headlines naming a
company and another universe company, and a US company's 10-K sentences naming another. Each
candidate is typed twice, by the keyword rule (`research.edges.rule_type`) and by Laya; measured
on a hand-labelled sample, Laya alone did worse than always answering "no tie", and the rule
alone was right on half its ties, so a tie becomes a link only where both agree. A tie only the
rule names is kept unconfirmed, for the user to confirm or delete on the Industry panel; one only
Laya names is kept as none (on the live copy they were three in four of the ties either named,
at Laya's 35% precision: noise the panel would bury the rest under).

Point in time: a link is first seen on its document's date (an article's published_at, else when
it was fetched; a filing's filing day, 00:00 UTC), so a test on day d leaves out a link its
documents did not yet show."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time as dtime, timezone

from oracle.net.client import RateLimitedClient
from oracle.research import edgar
from oracle.research.edges import Listed, NameIndex, common_words, named_pairs, rule_type, sentences_naming
from oracle.research.laya import MODEL_TAG, EdgeInput, EdgeType, EdgeTyper
from oracle.research.news import Article
from oracle.research.tools import ToolError
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, UnknownLink
from oracle.services.http import open_client
from oracle.services.laya_service import company_articles, load_scorer
from oracle.services.links_service import LinkFound, record_links
from oracle.services.never_list import never_company
from oracle.services.research_tools.filings import submissions
from oracle.services.research_tools.reading import FILING_BYTES
from oracle.services.research_tools.web import read_page
from oracle.services.universe_service import resolve_symbol
from oracle.store.articles import ArticleRepo
from oracle.store.db import to_utc_iso
from oracle.store.edges import EdgeRepo, NewEdge, StoredEdge
from oracle.store.links import LinkRepo
from oracle.store.sec import SecTickerRepo
from oracle.store.universe import UniverseRepo
from oracle.store.watchlists import WatchlistRepo
from oracle.universe.listings import is_exchange_product, is_receipt, is_receipt_symbol

__all__ = ["CONFIRMED", "DISMISSED", "Edge", "EdgeRun", "FILINGS_FROM", "INDEX_BANDS", "LINKED", "NONE", "SOURCE_FILING",
           "SOURCE_LAYA", "UNCONFIRMED", "company_edges", "confirm_edge", "decide", "dismiss_edge", "find_edges",
           "find_list_edges", "name_index"]

log = logging.getLogger(__name__)

SOURCE_FILING = "filing"
SOURCE_LAYA = "laya"
"""A link read from a headline and typed by Laya (with the rule)."""
LINKED, UNCONFIRMED, NONE, CONFIRMED, DISMISSED = "linked", "unconfirmed", "none", "confirmed", "dismissed"
SHOWN = (LINKED, UNCONFIRMED, CONFIRMED)
FILINGS_FROM = date(2022, 1, 1)
"""10-Ks filed since then are read: the history tests start 2022-09, so a tie in an older report
is dated by the first one read after it."""
MAX_FILINGS = 5
INDEX_BANDS = ("Mega Cap", "Large Cap", "Mid Cap")
"""Companies named in documents: smaller ones' names are mostly everyday words or people's."""
_PREFERRED = re.compile(r"-P(?:FD|R|F)[A-Z0-9]*\b|\bpreferred\b|\bpref\b", re.I)
"""A preferred share names its company but is no company of its own."""
YIELD_STEP_S = 5.0
wait = time.sleep


@dataclass(frozen=True)
class EdgeRun:
    symbols: tuple[str, ...]
    documents: int
    """Headlines and filings read for the first time."""
    filings: int
    candidates: int
    linked: int
    unconfirmed: int
    seconds: float
    device: str | None = None
    errors: tuple[str, ...] = ()
    stopped: bool = False


@dataclass(frozen=True)
class Edge:
    """`other` is `relation` of `symbol` as `evidence` (a 10-K sentence or a headline) shows."""

    id: int
    symbol: str
    other: str
    other_name: str | None
    kind: str
    relation: str
    rule: str
    choice: str
    prob: float
    """Laya's probability for its choice, uncalibrated."""
    model: str
    state: str
    evidence: str
    """The earliest document's sentence or headline."""
    known_at: str
    title: str
    url: str
    documents: int = 1
    """How many documents show this tie."""


LIST_NAMES = 4
"""A sentence naming this many universe companies is a list: its ties are never linked by themselves."""


def decide(rule: str, typed: EdgeType, names: int = 2) -> tuple[str, str]:
    """The relation kept and its state: linked where the rule and Laya name the same tie in a
    sentence that is no list of companies; else the rule's tie unconfirmed; else none, whatever
    Laya says alone. A list is where rule and model fail together (a 10-K's competitors described
    as "suppliers of ..."), so agreeing there proves nothing."""
    if rule == "none":
        return "none", NONE
    return rule, LINKED if typed.choice == rule and names < LIST_NAMES else UNCONFIRMED


def name_index(ctx: AppContext) -> NameIndex:
    """Universe companies by name: live equities of INDEX_BANDS, never depositary receipts or
    exchange products, each under its primary listing; everyday words learned from the
    universe's own descriptions."""
    rows = ctx.conn.execute(
        "SELECT l.symbol, l.name, l.market_cap, p.symbol FROM listings l LEFT JOIN listings p "
        "ON p.company_key = l.company_key AND p.is_primary WHERE l.type = 'equity' AND NOT l.delisted "
        f"AND l.market_cap IN ({','.join('?' * len(INDEX_BANDS))})", INDEX_BANDS).fetchall()
    listed = [Listed(primary or symbol, name, band) for symbol, name, band, primary in rows
              if not is_receipt(name) and not is_exchange_product(name) and not is_receipt_symbol(symbol)
              and not _PREFERRED.search(f"{symbol} {name}")]
    return NameIndex(listed, common_words(r[0] for r in ctx.conn.execute(
        "SELECT summary FROM listings WHERE summary IS NOT NULL AND type = 'equity'")))


@dataclass(frozen=True)
class _Company:
    symbol: str
    label: str
    symbols: tuple[str, ...]


def _company(ctx: AppContext, symbol: str) -> _Company:
    found = resolve_symbol(ctx, symbol)
    universe = UniverseRepo(ctx.conn)
    primary = universe.primary_symbol(found.company_key) or found.symbol
    return _Company(primary, f"{found.name} ({primary})", tuple(universe.company_symbols(found.company_key) or [primary]))


@dataclass(frozen=True)
class _Candidate:
    other: str
    kind: str
    article_id: int
    text: str
    known_at: str
    names: int


class _Finder:
    """One run over companies: the name index, the model (loaded when first needed) and the SEC client."""

    def __init__(self, ctx: AppContext, typer: EdgeTyper | None, filings: bool, stop, busy, say) -> None:
        self.ctx, self.typer, self.filings = ctx, typer, filings
        self.owned = typer is None
        self.stop, self.busy, self.say = stop or (lambda: False), busy, say
        self.index = name_index(ctx)
        self.repo = EdgeRepo(ctx.conn)
        self.never: dict[str, bool] = {}
        self.errors: list[str] = []
        self.documents = self.filings_read = self.candidates = self.linked = self.unconfirmed = 0
        self.device: str | None = None
        self.stopped = False

    @property
    def tag(self) -> str:
        return self.typer.tag if self.typer is not None else MODEL_TAG

    def banned(self, symbol: str) -> bool:
        if symbol not in self.never:
            self.never[symbol] = never_company(self.ctx, symbol, user=True) is not None
        return self.never[symbol]

    async def company(self, client: RateLimitedClient | None, symbol: str) -> None:
        c = _company(self.ctx, symbol)
        tag = self.tag
        found: list[_Candidate] = []
        read: list[int] = []
        titles = ArticleRepo(self.ctx.conn)
        news = self.repo.unread(c.symbol, tag, company_articles(self.ctx, c.symbol))
        for article_id, title, known in _articles(self.ctx, news):
            read.append(article_id)
            named = named_pairs(self.index, c.symbol, title)
            found += [_Candidate(o, "news", article_id, title, known, len(named) + 1)
                      for o in sorted(named) if not self.banned(o)]
        if self.filings and client is not None:
            for article_id, filed, text in await self._filings(client, c, tag, titles):
                read.append(article_id)
                self.filings_read += 1
                found += [_Candidate(o, "filing", article_id, s, filed, len(self.index.find(s)))
                          for o, s in sentences_naming(self.index, c.symbol, text) if not self.banned(o)]
        typed = self._type(c, found) if found else []
        if typed is None:
            # Cancelled while waiting for live model work: nothing is kept, so the next run reads these.
            self.stopped = True
            return
        self.documents += len(read)
        dismissed = self.repo.dismissed(c.symbol)
        edges = []
        for cand, t in zip(found, typed):
            rule = rule_type(cand.text, self.index.keys_of(c.symbol), self.index.keys_of(cand.other))
            relation, state = decide(rule, t, cand.names)
            if state != NONE and (cand.other.upper(), relation) in dismissed:
                state = DISMISSED
            edges.append(NewEdge(c.symbol, cand.other, cand.kind, cand.article_id, cand.text, cand.known_at, rule,
                                 tag, t.choice, t.prob, relation, state))
        ids = self.repo.save(c.symbol, tag, read, edges, to_utc_iso(self.ctx.clock.utcnow()))
        refused = []
        for edge_id, e in zip(ids, edges):
            if e.state == LINKED and not _link(self.ctx, e):
                refused.append(edge_id)
        self.repo.set_state(refused, DISMISSED)
        self.candidates += len(edges)
        self.linked += sum(e.state == LINKED for e in edges) - len(refused)
        self.unconfirmed += sum(e.state == UNCONFIRMED for e in edges)
        self.say(f"{c.symbol}: {len(read)} documents, {len(edges)} ties")

    def _type(self, c: _Company, found: list[_Candidate]) -> list[EdgeType] | None:
        """None when stopped while waiting for live model work: the model is then never loaded."""
        names = {o: self._label(o) for o in {x.other for x in found}}
        self._wait()
        if self.stop():
            return None
        if self.typer is None:
            self.typer = load_scorer(self.ctx)
        self.device = self.typer.device
        return self.typer.type_edges([EdgeInput(c.label, names[x.other], x.text, x.kind) for x in found])

    def _wait(self) -> None:
        """Gives way to live model work, closing a model it loaded meanwhile."""
        if not self.busy or not (other := self.busy()):
            return
        if self.owned and self.typer is not None:
            self.typer.close()
            self.typer = None
        while other and not self.stop():
            self.say(f"waiting for {other} to finish")
            wait(YIELD_STEP_S)
            other = self.busy()

    def _label(self, symbol: str) -> str:
        found = UniverseRepo(self.ctx.conn).resolve(symbol)
        return f"{found.name} ({symbol})" if found else symbol

    async def _filings(self, client: RateLimitedClient, c: _Company, tag: str,
                       titles: ArticleRepo) -> list[tuple[int, str, str]]:
        """(article id, known at, text) of the company's 10-Ks since FILINGS_FROM not yet read."""
        filings = await self._ten_ks(client, c)
        out = []
        for ticker, f in filings:
            filed = datetime.combine(f.filed, dtime(), timezone.utc)
            [article_id] = titles.save([Article(f.url, f.title, "SEC", filed)], "sec_edgar",
                                       to_utc_iso(self.ctx.clock.utcnow()), query=ticker)
            if not self.repo.unread(c.symbol, tag, [article_id]):
                continue
            try:
                page = await read_page(client, f.url, self.ctx.config.research.contact, FILING_BYTES)
            except ToolError as exc:
                self.errors.append(f"{c.symbol} {f.form} filed {f.filed}: {exc}")
                continue
            out.append((article_id, to_utc_iso(filed), page.text))
        return out

    async def _ten_ks(self, client: RateLimitedClient, c: _Company) -> list[tuple[str, edgar.Filing]]:
        """The first US listing SEC knows, and its 10-Ks since FILINGS_FROM, newest first."""
        for ticker in (s for s in dict.fromkeys((c.symbol, *c.symbols)) if "." not in s):
            try:
                cik, body = await submissions(client, SecTickerRepo(self.ctx.conn), self.ctx.clock,
                                              self.ctx.config.research.contact, ticker)
            except ToolError as exc:
                if "No SEC filer" not in str(exc):
                    self.errors.append(f"{c.symbol}: SEC {exc}")
                continue
            filings = [f for f in edgar.parse_filings(body, cik) if f.form == "10-K" and f.filed >= FILINGS_FROM]
            return [(ticker, f) for f in filings[:MAX_FILINGS]]
        return []

    def close(self) -> None:
        if self.owned and self.typer is not None:
            self.typer.close()


def _articles(ctx: AppContext, ids: list[int]) -> list[tuple[int, str, str]]:
    """(id, title, known at) of stored articles: published_at, else when fetched."""
    out = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        out += ctx.conn.execute(f"SELECT id, title, COALESCE(published_at, fetched_at) FROM articles "
                                f"WHERE id IN ({','.join('?' * len(part))}) AND source != 'sec_edgar' ORDER BY id",
                                part).fetchall()
    return out


def _link(ctx: AppContext, e: NewEdge | StoredEdge) -> bool:
    """Keep the tie as a company link first seen on its document's date; False when the user
    deleted a link between the two (or either is never linked)."""
    source = SOURCE_FILING if e.kind == "filing" else SOURCE_LAYA
    return record_links(ctx, [LinkFound(e.symbol, e.other, e.relation, (e.article_id,), e.known_at)], source) > 0


async def find_edges(ctx: AppContext, symbols: list[str], *, typer: EdgeTyper | None = None, filings: bool = True,
                     stop: Callable[[], bool] | None = None, busy: Callable[[], str | None] | None = None,
                     on_progress: Callable[[str], None] | None = None) -> EdgeRun:
    """Read each company's stored headlines and (US listings) 10-Ks not yet read by this model for
    ties to other universe companies, type them and keep them. Without a `typer` Laya is loaded
    when first needed and closed after; `busy` names live model work to wait for, `stop` ends
    the run between companies."""
    if not ctx.config.laya.enabled:
        raise InvalidQuery("Laya is off; turn it on in Settings ([laya] enabled)")
    listings = [_company(ctx, s).symbol for s in symbols]
    for s in listings:
        if company := never_company(ctx, s, user=True):
            raise InvalidQuery(f"{company} is on your never list: it is never read for ties")
    started = time.monotonic()
    finder = _Finder(ctx, typer, filings, stop, busy, on_progress or (lambda _: None))
    stopped = False
    try:
        async with open_client(ctx) as client:
            for s in dict.fromkeys(listings):
                if finder.stop():
                    stopped = True
                    break
                await finder.company(client if filings else None, s)
                if finder.stopped:
                    stopped = True
                    break
    finally:
        finder.close()
    return EdgeRun(tuple(dict.fromkeys(listings)), finder.documents, finder.filings_read, finder.candidates,
                   finder.linked, finder.unconfirmed, round(time.monotonic() - started, 2), finder.device,
                   tuple(finder.errors), stopped)


async def find_list_edges(ctx: AppContext, watchlist: str = "main", **kw) -> EdgeRun:
    """Every company on the list, never-listed ones left out."""
    symbols = [i.symbol for i in WatchlistRepo(ctx.conn).items(watchlist) if not i.missing]
    if not symbols:
        raise InvalidQuery(f"list {watchlist} is empty or unknown")
    return await find_edges(ctx, [s for s in symbols if not never_company(ctx, s, user=True)], **kw)


def company_edges(ctx: AppContext, symbol: str, states: tuple[str, ...] = SHOWN) -> list[Edge]:
    """The company's ties, one per (other company, relation) however many documents show it: its
    earliest document's evidence and date, and linked when any document linked it. A tie whose
    link the user deleted on the map is left out, as is one naming a never-listed company. Newest
    first."""
    c = _company(ctx, symbol)
    universe, links = UniverseRepo(ctx.conn), LinkRepo(ctx.conn)
    groups: dict[tuple[str, str], list[StoredEdge]] = {}
    for e in EdgeRepo(ctx.conn).of([c.symbol], SHOWN):
        if e.symbol.upper() == c.symbol.upper():
            groups.setdefault((e.other.upper(), e.relation), []).append(e)
    mine = _listings(universe, c.symbol)
    out = []
    for rows in groups.values():
        first = min(rows, key=lambda e: (e.known_at, e.id))
        state = next((s for s in (LINKED, CONFIRMED) if any(e.state == s for e in rows)), UNCONFIRMED)
        if state not in states:
            continue
        found = universe.resolve(first.other)
        if found and never_company(ctx, found.symbol, user=True):
            continue
        if state != UNCONFIRMED and links.deleted(mine, _listings(universe, first.other)):
            continue
        out.append(Edge(first.id, first.symbol, first.other, found.name if found else None, first.kind, first.relation,
                        first.rule, first.choice, first.prob, first.model, state, first.evidence, first.known_at,
                        first.title, first.url, len({e.article_id for e in rows})))
    return sorted(out, key=lambda e: (e.known_at, e.id), reverse=True)


def _listings(universe: UniverseRepo, symbol: str) -> set[str]:
    found = universe.resolve(symbol)
    if found is None:
        return {symbol.upper()}
    return {s.upper() for s in universe.company_symbols(found.company_key)} | {found.symbol.upper()}


def _pending(ctx: AppContext, symbol: str, edge_id: int) -> Edge:
    c = _company(ctx, symbol)
    e = EdgeRepo(ctx.conn).get(edge_id)
    if e is None or e.symbol.upper() != c.symbol.upper():
        raise UnknownLink(f"no tie {edge_id} for {symbol}")
    tie = next((x for x in company_edges(ctx, symbol, SHOWN)
                if x.other.upper() == e.other.upper() and x.relation == e.relation), None)
    if tie is None:
        raise InvalidQuery(f"tie {edge_id} is already {e.state}")
    if tie.state != UNCONFIRMED:
        raise InvalidQuery(f"tie {edge_id} is already {tie.state}")
    return tie


def confirm_edge(ctx: AppContext, symbol: str, edge_id: int) -> Edge:
    """The user's yes: the tie becomes a link, first seen on its earliest document's date."""
    tie = _pending(ctx, symbol, edge_id)
    if not _link(ctx, EdgeRepo(ctx.conn).get(tie.id)):
        raise InvalidQuery("you deleted the link between these two companies; add it by hand to bring it back")
    EdgeRepo(ctx.conn).decide_tie(tie.symbol, tie.other, tie.relation, CONFIRMED, to_utc_iso(ctx.clock.utcnow()))
    return replace(tie, state=CONFIRMED)


def dismiss_edge(ctx: AppContext, symbol: str, edge_id: int) -> Edge:
    """The user's no: every document's row of the tie is dropped, and later documents showing it
    never link it."""
    tie = _pending(ctx, symbol, edge_id)
    EdgeRepo(ctx.conn).decide_tie(tie.symbol, tie.other, tie.relation, DISMISSED, to_utc_iso(ctx.clock.utcnow()))
    return replace(tie, state=DISMISSED)
