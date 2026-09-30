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
import time
from collections.abc import Callable
from dataclasses import dataclass
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
    known_at: str
    title: str
    url: str


def decide(rule: str, typed: EdgeType) -> tuple[str, str]:
    """The relation kept and its state: linked where the rule and Laya name the same tie; else the
    rule's tie unconfirmed; else none, whatever Laya says alone."""
    if rule == "none":
        return "none", NONE
    return rule, LINKED if typed.choice == rule else UNCONFIRMED


def name_index(ctx: AppContext) -> NameIndex:
    """Universe companies by name: live equities of INDEX_BANDS, never depositary receipts or
    exchange products, each under its primary listing; everyday words learned from the
    universe's own descriptions."""
    rows = ctx.conn.execute(
        "SELECT l.symbol, l.name, l.market_cap, p.symbol FROM listings l LEFT JOIN listings p "
        "ON p.company_key = l.company_key AND p.is_primary WHERE l.type = 'equity' AND NOT l.delisted "
        f"AND l.market_cap IN ({','.join('?' * len(INDEX_BANDS))})", INDEX_BANDS).fetchall()
    listed = [Listed(primary or symbol, name, band) for symbol, name, band, primary in rows
              if not is_receipt(name) and not is_exchange_product(name) and not is_receipt_symbol(symbol)]
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
            found += [_Candidate(o, "news", article_id, title, known)
                      for o in sorted(named_pairs(self.index, c.symbol, title)) if not self.banned(o)]
        if self.filings and client is not None:
            for article_id, filed, text in await self._filings(client, c, tag, titles):
                read.append(article_id)
                self.filings_read += 1
                found += [_Candidate(o, "filing", article_id, s, filed)
                          for o, s in sentences_naming(self.index, c.symbol, text) if not self.banned(o)]
        self.documents += len(read)
        typed = self._type(c, found) if found else []
        edges = []
        for cand, t in zip(found, typed):
            rule = rule_type(cand.text, self.index.keys_of(c.symbol), self.index.keys_of(cand.other))
            relation, state = decide(rule, t)
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

    def _type(self, c: _Company, found: list[_Candidate]) -> list[EdgeType]:
        names = {o: self._label(o) for o in {x.other for x in found}}
        self._wait()
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
    """Ties read for the company or naming it, newest document first, as the company sees them."""
    c = _company(ctx, symbol)
    universe = UniverseRepo(ctx.conn)
    out = []
    for e in EdgeRepo(ctx.conn).of([c.symbol], states):
        if e.symbol.upper() != c.symbol.upper():
            continue
        found = universe.resolve(e.other)
        if found and never_company(ctx, found.symbol, user=True):
            continue
        out.append(Edge(e.id, e.symbol, e.other, found.name if found else None, e.kind, e.relation, e.rule, e.choice,
                        e.prob, e.model, e.state, e.evidence, e.known_at, e.title, e.url))
    return out


def _pending(ctx: AppContext, symbol: str, edge_id: int) -> StoredEdge:
    c = _company(ctx, symbol)
    e = EdgeRepo(ctx.conn).get(edge_id)
    if e is None or e.symbol.upper() != c.symbol.upper():
        raise UnknownLink(f"no tie {edge_id} for {symbol}")
    if e.state != UNCONFIRMED:
        raise InvalidQuery(f"tie {edge_id} is already {e.state}")
    return e


def _one(ctx: AppContext, symbol: str, edge_id: int) -> Edge:
    return next(x for x in company_edges(ctx, symbol, (LINKED, UNCONFIRMED, CONFIRMED, DISMISSED, NONE))
                if x.id == edge_id)


def confirm_edge(ctx: AppContext, symbol: str, edge_id: int) -> Edge:
    """The user's yes: the tie becomes a link, first seen on its document's date."""
    e = _pending(ctx, symbol, edge_id)
    if not _link(ctx, e):
        raise InvalidQuery("you deleted the link between these two companies; add it by hand to bring it back")
    EdgeRepo(ctx.conn).decide(edge_id, CONFIRMED, to_utc_iso(ctx.clock.utcnow()))
    return _one(ctx, symbol, edge_id)


def dismiss_edge(ctx: AppContext, symbol: str, edge_id: int) -> Edge:
    """The user's no: the tie is no longer shown."""
    _pending(ctx, symbol, edge_id)
    EdgeRepo(ctx.conn).decide(edge_id, DISMISSED, to_utc_iso(ctx.clock.utcnow()))
    return _one(ctx, symbol, edge_id)
