"""The general signal test on a small synthetic testbed: point in time, a future-return signal
passes, a null one fails, model-read signals count after their cutoff, results are kept."""

import hashlib
import math
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.analysts.signal_test import INSIDE, Signal, daily_ics, momentum_12_1, reversal_1m
from oracle.config import Config
from oracle.market.bars import Bar
from oracle.services.activity_service import JobRunner, retry, submit_signal_test
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from oracle.services.signal_test_service import (
    SIGNALS, check_signal, load_panel, run_and_keep, run_signal_test, signal_tests,
)
from oracle.services.testbed_service import Testbed, research_context
from oracle.store.bars import BarRepo
from oracle.store.testbed import Snapshot, TestbedRepo
from tests.fakes.clock import FakeClock

NOW = datetime(2022, 1, 3, 12, 0, tzinfo=timezone.utc)
FIRST = date(2017, 6, 1)
LAST = date(2021, 12, 31)
TICKERS = [f"T{k:02d}" for k in range(24)]
SECTORS = {t: ("Energy", "Utilities", "Financials")[k % 3] for k, t in enumerate(TICKERS)}


def weekdays(a: date, b: date) -> list[date]:
    return [d for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


def walk(symbol: str, days: list[date], seed: int) -> list[Bar]:
    rng, price, out = random.Random(seed), 50.0, []
    for d in days:
        o = price * math.exp(rng.gauss(0, 0.005))
        price = o * math.exp(rng.gauss(0, 0.015))
        out.append(Bar(symbol, d, o, o, o, price, price, 1000, "USD", "yahoo"))
    return out


def seed(ctx, rows=None, bars=None):
    days = weekdays(FIRST, LAST)
    rows = rows or [(FIRST, tuple(TICKERS))]
    with research_context(ctx) as r:
        TestbedRepo(r.conn).save_snapshot(Snapshot("fja05680/sp500", "https://x", "abc1234", "2022-01-01T00:00:00+00:00",
                                                   rows[0][0], rows[-1][0], len(rows)), rows)
        TestbedRepo(r.conn).set_sectors(SECTORS, "wikipedia", "2022-01-01T00:00:00+00:00")
        repo = BarRepo(r.conn)
        repo.upsert(walk("SPY", days, 999), "2022-01-01T00:00:00+00:00")
        for k, t in enumerate(TICKERS):
            repo.upsert((bars or {}).get(t) or walk(t, days, k), "2022-01-01T00:00:00+00:00")


@pytest.fixture
def ctx(tmp_path):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW)) as c:
        yield c


def future(p, i, names):
    """Cheats: the 5-day return it is judged on."""
    return {s: r for s in names if (r := p.forward(s, i, 5)) is not None}


def null(p, i, names):
    return {s: int(hashlib.sha256(f"{s}{p.days[i]}".encode()).hexdigest()[:8], 16) for s in names}


REGISTRY = {"future": Signal("future", "the future return", (5,), future),
            "null": Signal("null", "noise", (5,), null),
            "model_future": Signal("model_future", "a model reading the future", (5,), future, model="some-model")}


async def test_an_injected_future_return_signal_passes_and_a_null_one_fails(ctx):
    seed(ctx)
    good = await run_signal_test(ctx, "future", registry=REGISTRY)
    assert good.verdict == "PASS" and good.passed and good.train_verdict == "PASS"
    c = good.confirm[0]
    assert c.window == "confirm" and c.start == date(2020, 1, 1) and c.counted == 4 and c.positive == 4
    assert c.ic > 0.5 and c.t > 2 and c.names == 24 and c.missing_share == 0.0
    assert c.power == pytest.approx(2 * math.sqrt(5 / (23 * c.days)), abs=1e-4)
    t = good.train[0]
    assert [s.counted for s in t.segments].count(True) == 6, "2017-H1 (its June) to 2019-H2; earlier ones have no days"
    assert t.segments[0].why == "0 IC days (needs 20)"
    bad = await run_signal_test(ctx, "null", registry=REGISTRY)
    assert bad.verdict == "FAIL" and not bad.passed and abs(bad.confirm[0].ic) < 0.05


async def test_a_model_read_signal_counts_only_after_its_training_cutoff(ctx, monkeypatch):
    monkeypatch.setattr("oracle.services.signal_test_service.training_cutoff", lambda tag: date(2021, 6, 30))
    seed(ctx)
    t = await run_signal_test(ctx, "model_future", registry=REGISTRY)
    assert t.cutoff == date(2021, 6, 30)
    whys = [s.why for s in t.confirm[0].segments]
    assert whys == [INSIDE, INSIDE, INSIDE, None], "2021-H1 ends on the cutoff; 2021-H2 is after it"
    assert t.verdict == "INCONCLUSIVE", "one counted segment"
    assert all(s.why == INSIDE for s in t.train[0].segments)


async def test_future_bars_and_later_members_change_nothing_before_the_day(ctx, tmp_path):
    seed(ctx)
    with research_context(ctx) as r:
        before = load_panel(Testbed(r))
    x = date(2021, 3, 1)
    other = Config(db_path=tmp_path / "live2.db", research_db_path=tmp_path / "research2.db")
    with open_context(other, None, FakeClock(NOW)) as c2:
        days = weekdays(FIRST, LAST)
        changed = {t: [b if b.date < x else Bar(t, b.date, b.open * 1.7, b.high, b.low, b.close * 1.3,
                                                b.adj_close * 1.3, 1, "USD", "yahoo")
                       for b in walk(t, days, k)] for k, t in enumerate(TICKERS)}
        seed(c2, rows=[(FIRST, tuple(TICKERS)), (x, tuple(TICKERS[:20]) + ("NEW",))], bars=changed)
        with research_context(c2) as r:
            after = load_panel(Testbed(r))
    for score, h in ((momentum_12_1, 20), (reversal_1m, 5)):
        a, b = daily_ics(before, score, h), daily_ics(after, score, h)
        early = [d for d, (_, _, exit_day) in a.items() if exit_day < x]
        assert len(early) > 500
        assert {d: a[d] for d in early} == {d: b[d] for d in early}
        assert any(a[d] != b.get(d) for d in a if d >= x), "the change is seen from its day on"


async def test_the_later_of_a_duplicate_pair_is_dropped_while_both_are_members(ctx):
    seed(ctx, rows=[(FIRST, tuple(TICKERS) + ("KORS", "CPRI")), (date(2019, 1, 2), tuple(TICKERS) + ("CPRI",))])
    with research_context(ctx) as r:
        p = load_panel(Testbed(r))
    assert "KORS" in p.members[0] and "CPRI" not in p.members[0]
    assert "CPRI" in p.members[p.at[date(2019, 1, 2)]]


async def test_results_are_kept_in_the_research_database_and_load_back(ctx):
    seed(ctx)
    kept = await run_and_keep(ctx, "future", registry=REGISTRY)
    assert kept.id is not None
    loaded = signal_tests(ctx)["future"]
    assert loaded == kept and loaded.confirm[0].segments[0].start == date(2020, 1, 1)
    assert "survivorship" in loaded.caveat and loaded.snapshot.startswith("fja05680/sp500 at abc1234")


async def test_it_waits_for_live_work_and_stops_when_told(ctx):
    seed(ctx)
    asked = []

    def busy():
        asked.append(1)
        return "a tick" if len(asked) < 3 else None

    said = []
    t = await run_signal_test(ctx, "null", registry=REGISTRY, busy=busy, on_progress=said.append)
    assert t is not None and "waiting for a tick" in said and ctx.clock.sleeps[:2] == [5.0, 5.0]
    assert await run_signal_test(ctx, "null", registry=REGISTRY, stop=lambda: True) is None


def test_the_registry_holds_the_pre_registered_signals():
    assert {n: s.horizons for n, s in SIGNALS.items()} == {"momentum_12_1": (20,), "reversal_1m": (5,), "sue": (60,),
                                                           "gross_profitability": (20,), "earnings_yield": (20,),
                                                           "insider_buys": (20, 60)}
    assert all(s.model is None for s in SIGNALS.values())
    with pytest.raises(InvalidQuery, match="no signal 'x'"):
        check_signal("x")


async def test_an_empty_testbed_says_build_it_first(ctx):
    with pytest.raises(InvalidQuery, match="build it first"):
        await run_signal_test(ctx, "null", registry=REGISTRY)


async def test_the_job_runs_and_keeps_the_test_and_a_cancelled_one_retries(ctx):
    seed(ctx)
    runner = JobRunner(ctx)
    job = submit_signal_test(ctx, runner, "reversal_1m")
    assert submit_signal_test(ctx, runner, "reversal_1m") is job, "pressed twice joins the first"
    runner.cancel(job.id)
    again = await runner.wait(retry(ctx, runner, job.id).id)
    assert again.state == "done", again.error
    assert signal_tests(ctx)["reversal_1m"].confirm[0].horizon == 5
    with pytest.raises(InvalidQuery):
        submit_signal_test(ctx, runner, "nope")


async def test_a_train_only_run_reads_nothing_after_2019_and_is_kept_beside_the_full_result(ctx):
    seed(ctx)
    full = await run_and_keep(ctx, "future", registry=REGISTRY)
    train = await run_and_keep(ctx, "future", registry=REGISTRY, train_only=True)
    assert train.train_only and train.confirm == () and train.verdict == "TRAIN ONLY" and not train.passed
    assert train.last_day == date(2019, 12, 31) and train.train[0].verdict == "PASS"
    assert train.train[0].days == full.train[0].days, "the same train days as a full run"
    kept, trains = signal_tests(ctx), signal_tests(ctx, train_only=True)
    assert kept["future"].id == full.id and trains["future"].id == train.id


async def test_a_signal_test_waits_for_a_testbed_build(ctx):
    import asyncio

    from oracle.services.activity_service import _signal_test_waits
    runner, hold = JobRunner(ctx), asyncio.Event()

    async def building(c):
        await hold.wait()

    job = runner.submit("testbed", "Build the signal testbed", building, subject="testbed")
    await asyncio.sleep(0.05)
    assert _signal_test_waits(ctx, runner) == "Build the signal testbed"
    hold.set()
    await runner.wait(job.id)
    assert _signal_test_waits(ctx, runner) is None


async def test_a_cancelled_train_only_job_retries_as_train_only(ctx):
    seed(ctx)
    runner = JobRunner(ctx)
    job = submit_signal_test(ctx, runner, "reversal_1m", train_only=True)
    runner.cancel(job.id)
    again = await runner.wait(retry(ctx, runner, job.id).id)
    assert again.state == "done", again.error
    assert signal_tests(ctx, train_only=True)["reversal_1m"].train_only and "reversal_1m" not in signal_tests(ctx)
