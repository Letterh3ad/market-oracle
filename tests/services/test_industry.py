import json
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import AnalystsConfig, Config
from oracle.market.bars import Bar
from oracle.research.news import Article
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, NeverListed, UnknownSymbol
from oracle.services.industry_service import CAP, company_activity, industry_map, relation_of
from oracle.services.links_service import LinkFound, add_link, company_links, delete_link, record_links
from oracle.services.universe_service import refresh_universe
from oracle.store.articles import ArticleRepo
from oracle.store.bars import BarRepo
from oracle.store.meta import MetaRepo
from oracle.store.research import ResearchRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity, etf
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
AUTOS = dict(sector="Consumer Discretionary", industry_group="Automobiles & Components")


def car(symbol, name, cap, industry="Automobiles", **extra):
    return equity(symbol, name, currency="USD", market_cap=cap, industry=industry, **{**AUTOS, **extra})


BASE = [
    car("7203.T", "Toyota Motor Corporation", "Mega Cap", country="Japan", exchange="JPX"),
    equity("TM", "Toyota Motor Corporation", "Japan", "NYQ"),
    car("TSLA", "Tesla, Inc.", "Mega Cap"),
    car("HMC", "Honda Motor Co.", "Large Cap"),
    car("GM", "General Motors", "Large Cap"),
    car("F", "Ford Motor", "Large Cap"),
    car("STLA", "Stellantis", "Large Cap"),
    car("NSANY", "Nissan", "Mid Cap"),
    car("6902.T", "DENSO Corporation", "Large Cap", industry="Auto Components", country="Japan", exchange="JPX"),
    car("BWA", "BorgWarner", "Mid Cap", industry="Auto Components"),
    equity("AAPL", "Apple Inc.", currency="USD", market_cap="Mega Cap", sector="Information Technology",
           industry_group="Technology Hardware & Equipment", industry="Technology Hardware"),
]
# Twenty more carmakers, smallest first, to overflow the cap.
MANY = BASE + [car(f"CAR{i:02d}", f"Carmaker {i:02d}", band, country="Germany")
               for i, band in enumerate(["Nano Cap"] * 8 + ["Small Cap"] * 8 + ["Mega Cap"] * 4)]


async def open_ctx(tmp_path, rows):
    web = FakeWeb(FakeFinanceDatabase(rows, [etf("CARZ", "Global Auto ETF")]), FakeYahoo())
    manager = open_context(Config(db_path=tmp_path / "o.db", analysts=AnalystsConfig(peers=5)), None,
                           FakeClock(NOW), web.transport)
    c = manager.__enter__()
    await refresh_universe(c)
    return manager, c, web


@pytest.fixture
async def ctx(tmp_path):
    manager, c, _ = await open_ctx(tmp_path, BASE)
    yield c
    manager.__exit__(None, None, None)


def symbols(m):
    return [r.symbol for r in m.related]


def article(ctx, title, published=datetime(2026, 9, 20, tzinfo=timezone.utc)):
    [a] = ArticleRepo(ctx.conn).save([Article(f"https://news.test/{title}", title, "Wire", published)],
                                     "google_news", "2026-09-29T00:00:00+00:00")
    return a


def test_competitors_are_the_same_industry_biggest_first_without_tesla_or_itself(ctx):
    m = industry_map(ctx, "7203.T")
    assert symbols(m) == ["F", "GM", "HMC", "STLA", "6902.T", "NSANY"], "biggest band first, then by name"
    assert all(r.kinds == ("competitor",) for r in m.related)
    assert "TSLA" not in symbols(m) and "7203.T" not in symbols(m) and "CARZ" not in symbols(m)
    honda = next(r for r in m.related if r.symbol == "HMC")
    [same] = [x for x in honda.relations if x.source == "industry"]
    assert same.kind == "competitor" and same.why == "same industry (Automobiles)" and same.first_known is None
    assert honda.first_known is None, "a static field is known on any day"


def test_the_peers_join_the_same_industry_ones(ctx):
    denso = next(r for r in industry_map(ctx, "7203.T").related if r.symbol == "6902.T")
    [peer] = denso.relations
    assert (peer.kind, peer.source) == ("competitor", "peers")
    assert peer.why == "a peer (industry group Automobiles & Components)"


def test_a_thin_industry_falls_back_to_its_industry_group(ctx):
    m = industry_map(ctx, "6902.T")
    assert "industry group Automobiles & Components" in m.basis and "Auto Components has too few" in m.basis
    toyota = next(r for r in m.related if r.symbol == "7203.T")
    assert any(x.source == "industry_group" and x.why == "same industry group (Automobiles & Components)"
               for x in toyota.relations)
    assert "TSLA" not in symbols(m)


async def test_competitors_are_capped_by_market_cap(tmp_path):
    manager, ctx, _ = await open_ctx(tmp_path, MANY)
    try:
        m = industry_map(ctx, "7203.T")
    finally:
        manager.__exit__(None, None, None)
    assert len(m.related) == CAP == 15
    assert not any(s.startswith("CAR") and int(s[3:]) < 8 for s in symbols(m)), "nano caps go first"
    bands = [r.market_cap for r in m.related]
    order = ["Mega Cap", "Large Cap", "Mid Cap", "Small Cap", "Micro Cap", "Nano Cap"]
    assert bands == sorted(bands, key=order.index)


def test_within_a_band_a_company_with_stored_prices_comes_first(ctx):
    BarRepo(ctx.conn).upsert(bars("STLA", [10.0] * 3), "x")
    assert symbols(industry_map(ctx, "7203.T"))[:2] == ["STLA", "F"]


async def test_another_line_of_a_company_already_on_the_map_is_left_out(tmp_path):
    manager, ctx, _ = await open_ctx(tmp_path, BASE + [car("HMC-B", "Honda Motor Co. Class B", "Large Cap"),
                                                       car("HMCX", "Honda Motors of Ohio", "Mid Cap")])
    try:
        m = industry_map(ctx, "7203.T")
    finally:
        manager.__exit__(None, None, None)
    assert "HMC" in symbols(m) and "HMC-B" not in symbols(m) and "HMCX" in symbols(m)


async def test_exchange_traded_products_typed_as_equity_are_never_competitors(tmp_path):
    products = [car("TM3.L", "Leverage Shares 3x Toyota ETC", "Mega Cap"),
                car("TMS.L", "Leverage Shares -1x Toyota ETC", "Mega Cap"),
                car("TM2.L", "Toyota 2x ETP", "Mega Cap"),
                car("AUTN", "UBS ETRACS Auto ETN", "Large Cap")]
    manager, ctx, _ = await open_ctx(tmp_path, BASE + products)
    try:
        m = industry_map(ctx, "7203.T")
    finally:
        manager.__exit__(None, None, None)
    assert not {p["symbol"] for p in products} & set(symbols(m))
    assert symbols(m) == ["F", "GM", "HMC", "STLA", "6902.T", "NSANY"]


def test_known_by_keeps_what_was_known_that_day(ctx):
    a = article(ctx, "Denso to supply Toyota", datetime(2026, 9, 20, tzinfo=timezone.utc))
    b = article(ctx, "Denso deal widened", datetime(2026, 10, 2, tzinfo=timezone.utc))
    record_links(ctx, [LinkFound("7203.T", "6902.T", "supplier", (a, b)), LinkFound("7203.T", "AAPL", "partner")],
                 "discovery")
    m = industry_map(ctx, "7203.T")
    day = ctx.clock.utcnow().date()
    before = m.known_by(day - timedelta(days=1))
    assert before.as_of == day - timedelta(days=1) and "AAPL" not in symbols(before)
    assert next(r for r in before.related if r.symbol == "6902.T").kinds == ("competitor",)
    on_day = m.known_by(day)
    denso = next(r for r in on_day.related if r.symbol == "6902.T")
    assert denso.kinds == ("supplier", "competitor") and [e.title for e in denso.evidence] == ["Denso to supply Toyota"]
    assert symbols(on_day) == symbols(industry_map(ctx, "7203.T", as_of=day))


def test_links_join_the_map_with_their_evidence_and_when_first_known(ctx):
    a = article(ctx, "Denso to supply Toyota")
    record_links(ctx, [LinkFound("7203.T", "6902.T", "supplier", (a,)), LinkFound("7203.T", "AAPL", "partner")],
                 "discovery")
    m = industry_map(ctx, "7203.T")
    assert symbols(m)[:2] == ["6902.T", "AAPL"], "linked companies first, most evidence first"
    denso = m.related[0]
    assert denso.kinds == ("supplier", "competitor")
    link = denso.relations[0]
    at = ctx.clock.utcnow().isoformat()
    assert (link.why, link.source, link.first_known) == ("a supplier", "discovery", at)
    assert denso.link_id is not None and [e.title for e in denso.evidence] == ["Denso to supply Toyota"]
    assert denso.first_known is None
    apple = m.related[1]
    assert apple.kinds == ("partner",) and apple.first_known == at


def test_as_of_leaves_out_links_first_seen_later_but_keeps_static_relations(ctx):
    record_links(ctx, [LinkFound("7203.T", "6902.T", "supplier"), LinkFound("7203.T", "AAPL", "partner")], "discovery")
    before = NOW.date() - timedelta(days=1)
    m = industry_map(ctx, "7203.T", as_of=before)
    assert m.as_of == before and "AAPL" not in symbols(m)
    assert next(r for r in m.related if r.symbol == "6902.T").kinds == ("competitor",)
    assert relation_of(ctx, "7203.T", "AAPL", as_of=before) is None
    assert relation_of(ctx, "TM", "AAPL").kinds == ("partner",)
    assert relation_of(ctx, "7203.T", "AAPL").first_known == ctx.clock.utcnow().isoformat()
    assert relation_of(ctx, "7203.T", "HMC", as_of=date(2020, 1, 1)).kinds == ("competitor",)


def test_each_relation_keeps_its_own_first_seen(ctx, monkeypatch):
    at = ctx.clock.utcnow()
    record_links(ctx, [LinkFound("7203.T", "AAPL", "partner")], "discovery")
    later = at + timedelta(days=3)
    monkeypatch.setattr(ctx.clock, "utcnow", lambda: later)
    record_links(ctx, [LinkFound("AAPL", "7203.T", "customer")], "chat")
    apple = relation_of(ctx, "7203.T", "AAPL")
    assert {x.kind: x.first_known for x in apple.relations} == {"partner": at.isoformat(),
                                                                "supplier": later.isoformat()}
    assert apple.first_known == at.isoformat()
    on_day = relation_of(ctx, "7203.T", "AAPL", as_of=at.date())
    assert on_day.kinds == ("partner",)


def test_each_relation_keeps_its_own_source(ctx):
    record_links(ctx, [LinkFound("AAPL", "7203.T", "partner", (article(ctx, "Toyota and Apple talk"),))],
                 "discovery")
    add_link(ctx, "AAPL", "7203.T", "supplier")
    toyota = relation_of(ctx, "AAPL", "7203.T")
    assert {x.kind: x.source for x in toyota.relations} == {"partner": "discovery", "supplier": "user"}


def test_adding_a_link_already_found_keeps_who_found_it(ctx):
    record_links(ctx, [LinkFound("AAPL", "7203.T", "partner")], "discovery")
    first = relation_of(ctx, "AAPL", "7203.T").first_known
    add_link(ctx, "AAPL", "7203.T", "partner")
    [x] = relation_of(ctx, "AAPL", "7203.T").relations
    assert (x.source, x.first_known) == ("discovery", first)


def test_a_fund_or_a_company_without_classification_has_links_only(ctx):
    m = industry_map(ctx, "CARZ")
    assert m.related == () and "fund" in m.note


def test_an_unknown_symbol_is_refused(ctx):
    with pytest.raises(UnknownSymbol):
        industry_map(ctx, "NOPE")


def test_the_user_adds_a_link_and_it_reads_as_added_by_you(ctx):
    link = add_link(ctx, "TM", "AAPL", "customer")
    assert (link.symbol, link.relations, link.source) == ("AAPL", ("customer",), "user")
    [x] = [r for r in industry_map(ctx, "7203.T").related if r.symbol == "AAPL"]
    assert x.relations[0].why == "a customer" and x.relations[0].source == "user"
    [back] = company_links(ctx, "AAPL")
    assert back.relations == ("supplier",)


def test_adding_a_link_is_checked(ctx):
    with pytest.raises(InvalidQuery, match="one of supplier, customer, competitor, partner"):
        add_link(ctx, "7203.T", "AAPL", "peer")
    with pytest.raises(InvalidQuery, match="itself"):
        add_link(ctx, "7203.T", "TM", "partner")
    with pytest.raises(UnknownSymbol):
        add_link(ctx, "7203.T", "NOPE", "partner")
    with pytest.raises(NeverListed):
        add_link(ctx, "7203.T", "TSLA", "competitor")


def test_the_user_can_add_back_a_link_they_deleted(ctx):
    record_links(ctx, [LinkFound("7203.T", "AAPL", "partner"), LinkFound("7203.T", "AAPL", "competitor")],
                 "discovery")
    [x] = company_links(ctx, "7203.T")
    delete_link(ctx, "7203.T", x.id)
    assert company_links(ctx, "7203.T") == []
    add_link(ctx, "7203.T", "AAPL", "partner")
    [back] = company_links(ctx, "7203.T")
    assert back.relations == ("partner",) and back.source == "user"
    record_links(ctx, [LinkFound("7203.T", "AAPL", "competitor")], "discovery")
    assert company_links(ctx, "7203.T")[0].relations == ("partner",), "the relation still deleted stays muted"


def bars(symbol, closes, end=date(2026, 9, 29)):
    days, d = [], end
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return [Bar(symbol, day, None, None, None, c, None, None, "USD", "yahoo")
            for day, c in zip(reversed(days), closes)]


async def test_activity_reads_only_stored_data_and_names_what_is_missing(tmp_path):
    manager, ctx, web = await open_ctx(tmp_path, BASE)
    try:
        BarRepo(ctx.conn).upsert(bars("HMC", [100.0] * 15 + [110.0] * 5 + [121.0]), "2026-09-29T22:00:00+00:00")
        MetaRepo(ctx.conn).set("earnings_dates:HMC", json.dumps(
            {"source": "SEC", "fetched_at": "2026-09-01T00:00:00+00:00",
             "releases": [["2026-08-05", True], ["2026-05-06", True], ["2026-11-04", None]]}))
        runs = ResearchRepo(ctx.conn)
        run = runs.start("HMC", "ollama", "m", "2026-09-28T00:00:00+00:00")
        old = article(ctx, "Honda old news", datetime(2026, 9, 1, tzinfo=timezone.utc))
        new = article(ctx, "Honda recalls cars", datetime(2026, 9, 27, tzinfo=timezone.utc))
        undated = article(ctx, "Honda investor relations", None)
        [own] = ArticleRepo(ctx.conn).save([Article("oracle://HMC", "HMC stored prices", "stored data",
                                                    datetime(2026, 9, 28, tzinfo=timezone.utc))], "oracle", "x")
        runs.add_evidence(run, [old, new, undated, own])
        seen = len(web.requests)
        now = company_activity(ctx, ["HMC", "GM"])
        assert len(web.requests) == seen, "stored data only"
    finally:
        manager.__exit__(None, None, None)
    honda = now["HMC"]
    assert honda.last_close == date(2026, 9, 29)
    assert honda.move_5d == pytest.approx(0.1) and honda.move_20d == pytest.approx(0.21)
    assert (honda.last_earnings, honda.next_earnings) == (date(2026, 8, 5), date(2026, 11, 4))
    assert [h.title for h in honda.headlines] == ["Honda recalls cars", "Honda old news", "Honda investor relations"], \
        "dated first; Market Oracle's own stored-data summaries are not news"
    gm = now["GM"]
    assert (gm.last_close, gm.move_5d, gm.move_20d, gm.last_earnings, gm.next_earnings, gm.headlines) == (
        None, None, None, None, None, ())


async def test_a_short_or_broken_series_gives_no_move(tmp_path):
    manager, ctx, _ = await open_ctx(tmp_path, BASE)
    try:
        BarRepo(ctx.conn).upsert(bars("HMC", [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]), "x")
        BarRepo(ctx.conn).upsert(bars("GM", [100.0] * 20 + [20.0]), "x")
        now = company_activity(ctx, ["HMC", "GM"])
    finally:
        manager.__exit__(None, None, None)
    assert now["HMC"].move_5d == pytest.approx(106 / 101 - 1) and now["HMC"].move_20d is None
    assert now["GM"].move_5d is None and now["GM"].move_20d is None, "a split-sized jump is not a move"


def test_headlines_found_by_a_links_search_are_not_the_company_s_own(ctx):
    runs = ResearchRepo(ctx.conn)
    run = runs.start("7203.T", "ollama", "m", "2026-09-28T00:00:00+00:00")
    runs.set_queries(run, [("Toyota shares", None), ("Denso Toyota", "DENSO (6902.T), a supplier")])
    own, via = article(ctx, "Toyota sales rise"), article(ctx, "Denso wins order")
    runs.add_evidence(run, [own], "Toyota shares")
    runs.add_evidence(run, [via], "Denso Toyota")
    assert [h.title for h in company_activity(ctx, ["TM"])["TM"].headlines] == ["Toyota sales rise"]
