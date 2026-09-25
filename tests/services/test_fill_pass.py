"""The fill pass: prices, FX and fills only, so an order placed by the evening tick fills at
the next open the same day instead of at the next evening's tick."""

from datetime import date, datetime, timezone

import pytest

from oracle.config import Config, DiscoveryConfig
from oracle.services.context import open_context
from oracle.services.paper_service import account_view, decision_log, place_buy
from oracle.services.tick_service import FILL_STAGES, last_fill, last_tick, run_fill, run_tick
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.paper import PaperRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

MONDAY_TICK = datetime(2026, 9, 21, 21, 30, tzinfo=timezone.utc)  # 22:30 BST
TUESDAY_FILL = datetime(2026, 9, 22, 8, 30, tzinfo=timezone.utc)  # 09:30 BST


def no_open(day: str, close: float) -> tuple:
    return (date.fromisoformat(day), None, close, close, close, close, 1000)


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    y.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 100.0), bar("2026-09-21", 100.0)])
    y.add("SAP.DE", "EUR", "Europe/Berlin", [bar("2026-09-18", 50.0), bar("2026-09-21", 50.0)])
    y.add("GBPUSD=X", "USD", "Europe/London", [bar("2026-09-18", 1.25), bar("2026-09-21", 1.25)])
    y.add("GBPEUR=X", "EUR", "Europe/London", [bar("2026-09-18", 1.2), bar("2026-09-21", 1.2)])
    return y


@pytest.fixture
async def ctx(tmp_path, yahoo):
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD"),
                                       equity("SAP.DE", "SAP", "Germany", "GER", currency="EUR")]), yahoo)
    config = Config(db_path=tmp_path / "o.db", discovery=DiscoveryConfig(enabled=False))
    with open_context(config, None, FakeClock(MONDAY_TICK), web.transport) as c:
        await refresh_universe(c)
        watch(c, "AAPL")
        await run_tick(c)
        yield c


def fill_summary(run) -> str:
    return next(s.summary for s in run.stages if s.name == "fill")


async def test_a_fill_pass_runs_prices_fx_and_fill_only_and_is_not_a_tick(ctx):
    tick, decided = last_tick(ctx), len(decision_log(ctx, "strategy"))
    ctx.clock.advance((TUESDAY_FILL - MONDAY_TICK).total_seconds())
    run = await run_fill(ctx, "scheduled")
    assert [s.name for s in run.stages] == list(FILL_STAGES) == ["prices", "fx", "fill"]
    assert (run.kind, run.outcome) == ("fill", "ok")
    assert last_tick(ctx).id == tick.id and last_fill(ctx).id == run.id
    assert len(decision_log(ctx, "strategy")) == decided  # no step ran


async def test_an_order_placed_monday_evening_fills_in_tuesdays_morning_pass_once(ctx, yahoo):
    place_buy(ctx, "SAP.DE", 1000.0)
    yahoo.series["SAP.DE"].bars.append(bar("2026-09-22", 52.0))
    yahoo.series["GBPEUR=X"].bars.append(bar("2026-09-22", 1.2))
    ctx.clock.advance((TUESDAY_FILL - MONDAY_TICK).total_seconds())
    run = await run_fill(ctx, "scheduled")
    assert fill_summary(run) == "1 filled, 0 rejected"
    [fill] = PaperRepo(ctx.conn).fills("manual")
    assert (fill.date, fill.symbol) == (date(2026, 9, 22), "SAP.DE")
    # The evening tick finds it filled and leaves it alone.
    ctx.clock.advance(13 * 3600)
    assert fill_summary(await run_tick(ctx)) == "0 filled, 0 rejected"
    assert len(PaperRepo(ctx.conn).fills("manual")) == 1


async def test_a_market_not_open_yet_leaves_the_order_pending(ctx, yahoo):
    place_buy(ctx, "AAPL", 1000.0)  # New York opens at 14:30 BST
    yahoo.series["AAPL"].bars.append(no_open("2026-09-22", 100.0))
    ctx.clock.advance((TUESDAY_FILL - MONDAY_TICK).total_seconds())
    run = await run_fill(ctx, "scheduled")
    assert fill_summary(run) == "0 filled, 0 rejected"
    assert [o.symbol for o in account_view(ctx, "manual").pending] == ["AAPL"]


async def test_a_listing_with_a_pending_order_is_priced_though_nobody_watches_it(ctx, yahoo):
    place_buy(ctx, "SAP.DE", 1000.0)  # watched by nobody, held by nobody
    yahoo.series["SAP.DE"].bars.append(bar("2026-09-22", 52.0))
    yahoo.series["GBPEUR=X"].bars.append(bar("2026-09-22", 1.2))
    ctx.clock.advance((TUESDAY_FILL - MONDAY_TICK).total_seconds())
    run = await run_fill(ctx, "scheduled")
    assert run.stages[0].summary.startswith("2 of 2 listings updated")
