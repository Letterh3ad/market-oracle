from dataclasses import replace
from datetime import datetime, timezone

import pytest

from oracle.config import Config, UniverseConfig
from oracle.services.context import open_context
from oracle.services.universe_service import proposed_merges, refresh_universe
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import BASIC, FakeFinanceDatabase, equity

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
ROWS = BASIC + [
    equity("BT-A.L", "BT GROUP PLC", "United Kingdom", "LSE", isin="GB0030913577", currency="GBP"),
    equity("BTQ.DE", "BT Group", "United Kingdom", "GER", currency="EUR"),
    equity("BTQ.F", "BT Group", "United Kingdom", "FRA", currency="EUR"),
    equity("ABC.L", "Abc plc", "United Kingdom", "LSE", isin="GB0000000001"),
    equity("ABC.DE", "Abc Ltd", "United Kingdom", "GER", isin="GB0000000002"),
    equity("VALPQ", "Valaris plc", "", "PNK", isin="GB00BMY5LS59"),
    equity("E65F.F", "Valaris Limited", "", "FRA"),
]


@pytest.fixture
async def ctx(tmp_path):
    fdb = FakeFinanceDatabase(ROWS)
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), fdb.transport) as c:
        await refresh_universe(c)
        yield c


def stored(ctx):
    return ctx.conn.execute("SELECT symbol, company_key, is_primary FROM listings ORDER BY symbol").fetchall()


def keys(ctx, *symbols):
    return {r[1] for r in stored(ctx) if r[0] in symbols}


def test_the_report_lists_the_merges_suffix_blind_grouping_would_make_flagged_first(ctx):
    report = proposed_merges(ctx)
    assert not report.enabled
    assert report.flag_counts == {"legal forms differ": 1, "no country": 1}
    valaris, bt = report.merges
    assert valaris.flags == ("legal forms differ", "no country")
    assert [(c.name, c.forms, c.countries, c.isins) for c in valaris.companies] == [
        ("Valaris Limited", ("limited",), (), ()),
        ("Valaris plc", ("plc",), (), ("GB00BMY5LS59",)),
    ]
    assert bt.flags == ()
    assert [(c.name, c.symbols, c.exchanges, c.countries, c.isins, c.forms) for c in bt.companies] == [
        ("BT GROUP PLC", ("BT-A.L",), ("LSE",), ("United Kingdom",), ("GB0030913577",), ("plc",)),
        ("BT Group", ("BTQ.DE", "BTQ.F"), ("FRA", "GER"), ("United Kingdom",), (), ()),
    ]
    assert bt.matched_on == ("bt group, United Kingdom",)


def test_the_report_never_writes_even_with_the_setting_on(ctx):
    before = [tuple(r) for r in stored(ctx)]
    ctx.config = replace(ctx.config, universe=UniverseConfig(suffix_blind_grouping=True))
    assert proposed_merges(ctx).enabled
    assert [tuple(r) for r in stored(ctx)] == before
    assert len(keys(ctx, "BT-A.L", "BTQ.DE")) == 2


async def test_turning_it_on_applies_the_merges_at_the_next_refresh(ctx):
    ctx.config = replace(ctx.config, universe=UniverseConfig(suffix_blind_grouping=True))
    await refresh_universe(ctx)
    assert len(keys(ctx, "BT-A.L", "BTQ.DE", "BTQ.F")) == 1
    assert len(keys(ctx, "ABC.L", "ABC.DE")) == 2
    assert proposed_merges(ctx).merges == ()


async def test_off_the_refresh_groups_as_before(ctx):
    await refresh_universe(ctx)
    assert len(keys(ctx, "BT-A.L", "BTQ.DE")) == 2
