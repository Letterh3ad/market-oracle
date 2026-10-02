"""The data checks over seeded live and research databases: every check flags its bad data, clean
data gives no flag, market data is never edited, and explanations stay off unless asked (Ollama only)."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from oracle.config import CompareConfig, Config, DataChecksConfig
from oracle.services import data_check_service
from oracle.services.activity_service import JobRunner, nightly_data_check, retry, submit_data_check
from oracle.services.context import open_context
from oracle.services.data_check_service import EXPLAIN_MAX, last_data_check, run_data_checks
from oracle.services.data_checks import CHECKS
from oracle.services.testbed_service import research_context
from oracle.store.testbed import Alias, Snapshot, TestbedRepo
from tests.fakes.clock import FakeClock

NOW = datetime(2021, 1, 4, 12, 0, tzinfo=timezone.utc)
AT = "2021-01-04T00:00:00+00:00"


def weekdays(start: str, end: str) -> list[str]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [d.isoformat() for d in (a + timedelta(n) for n in range((b - a).days + 1)) if d.weekday() < 5]


YEAR = weekdays("2020-01-01", "2020-12-31")


def clean(symbol: str, days=YEAR, base=50.0, factor=lambda i: 1.0) -> list[tuple]:
    out = []
    for i, d in enumerate(days):
        c = base + (i % 9) * 0.25
        out.append((symbol, d, c * 0.99, c * 1.01, c * 0.98, c, c * factor(i), 1000 + i))
    return out


def payer(i: int) -> float:
    return 0.97 + 0.01 * (i // 60)


def put_bars(conn, rows) -> None:
    conn.executemany("INSERT OR REPLACE INTO bars (symbol, date, open, high, low, close, adj_close, volume, currency, "
                     "source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'USD', 'yahoo', ?)",
                     [(*r, AT) for r in rows])


def seed_research(ctx, membership, bars, splits=(), aliases=(), ciks=None) -> None:
    with research_context(ctx) as r:
        repo = TestbedRepo(r.conn)
        repo.save_snapshot(Snapshot("test", "url", None, AT, date(2020, 1, 1), date(2020, 12, 31), len(membership)),
                           [(date.fromisoformat(d), tuple(ts)) for d, ts in membership])
        put_bars(r.conn, bars)
        r.conn.executemany("INSERT INTO tb_splits (ticker, day, ratio) VALUES (?, ?, ?)", list(splits))
        repo.save_aliases(aliases)
        if ciks:
            repo.set_ciks(ciks)


def seed_live(ctx, held, bars) -> None:
    """`held`: (symbol, name, type, company_key) on the main watchlist; type None leaves it out of the universe."""
    c = ctx.conn
    c.execute("INSERT INTO watchlists (name, created_at) VALUES ('main', ?)", (AT,))
    for symbol, name, kind, key in held:
        if kind is not None:
            c.execute("INSERT INTO listings (symbol, name, type, company_key, is_primary, delisted) "
                      "VALUES (?, ?, ?, ?, 1, 0)", (symbol, name, kind, key))
        c.execute("INSERT INTO watchlist_items (watchlist, symbol, added_at) VALUES ('main', ?, ?)", (symbol, AT))
    put_bars(c, bars)


@pytest.fixture
def ctx(tmp_path):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db",
                    compare=CompareConfig(benchmarks=()))
    with open_context(config, None, FakeClock(NOW)) as c:
        yield c


def seed_clean(ctx) -> None:
    seed_research(ctx, [("2020-01-01", ["AAA", "BBB"])],
                  clean("SPY") + clean("ACWI") + clean("AAA") + clean("BBB", factor=payer) + clean("KO", factor=payer))
    seed_live(ctx, [("AAA", "Aaa Inc", "equity", "aaa"), ("KO", "Coca-Cola", "equity", "ko")],
              clean("AAA") + clean("KO", factor=payer))


def seed_dirty(ctx) -> None:
    ccc = clean("CCC")
    for i in range(100, len(ccc)):  # an unrecorded 1:4 split from bar 100
        s, d, o, h, l, c, a, v = ccc[i]
        ccc[i] = (s, d, o / 4, h / 4, l / 4, c / 4, a / 4, v)
    for i in range(150, 160):  # ten frozen bars
        ccc[i] = (ccc[i][0], ccc[i][1], *ccc[149][2:])
    s, d, *_ = ccc[200]
    ccc[200] = (s, d, 0.0, 1.0, 0.0, 12.0, 12.0, 0)
    old = [b for b in clean("OLD") if b[1] <= "2020-05-15"]  # renamed to NEW on 2020-06-01, NEW priced from then
    new = [b for b in clean("NEW") if b[1] >= "2020-06-01"]
    seed_research(
        ctx,
        [("2020-01-01", ["AAA", "CCC", "DDD", "GOOG", "GOOGL", "OLD"]),
         ("2020-06-01", ["AAA", "CCC", "DDD", "GOOG", "GOOGL", "NEW"])],
        clean("SPY") + clean("ACWI") + clean("AAA") + ccc + clean("GOOG") + clean("GOOGL") + old + new
        + clean("KO", factor=payer),
        aliases=[Alias("OLD", "NEW", date(2020, 6, 1), True, "accepted", AT, date(2020, 9, 30))],
        ciks={"GOOG": 1652044, "GOOGL": 1652044})
    seed_live(ctx, [("AAA", "Aaa Inc", "equity", "aaa"), ("KO", "Coca-Cola", "equity", "ko"),
                    ("VUSA.L", "Vanguard S&P 500 UCITS ETF", "etf", "vusa"), ("SHEL", "Shell", "equity", "shell"),
                    ("SHEL.L", "Shell", "equity", "shell"), ("STALE", "Stale Co", "equity", "stale")],
              clean("AAA") + clean("KO") + clean("VUSA.L") + clean("SHEL") + clean("SHEL.L")
              + [b for b in clean("STALE") if b[1] <= "2020-06-30"])


def counts(run) -> dict[str, int]:
    return {c.code: c.count for c in run.checks}


async def test_clean_data_gives_no_flag(ctx):
    seed_clean(ctx)
    run = await run_data_checks(ctx)
    assert counts(run) == dict.fromkeys(CHECKS, 0), [f for c in run.checks for f in c.rows]


async def test_every_check_flags_its_seeded_bad_data(ctx):
    seed_dirty(ctx)
    run = await run_data_checks(ctx)
    by = {c.code: c.rows for c in run.checks}
    assert all(counts(run)[code] for code in CHECKS), counts(run)
    assert {(f.db, f.symbol) for f in by["split_jump"]} == {("research", "CCC")}
    assert {("research", "CCC"), ("research", "DDD"), ("live", "STALE")} <= {(f.db, f.symbol) for f in by["stale_bars"]}
    assert {(f.db, f.symbol) for f in by["bad_price"]} == {("research", "CCC")}
    assert {(f.db, f.symbol) for f in by["junk_ticker"]} == {("live", "VUSA.L")}
    assert {(f.db, f.symbol) for f in by["missing_dividends"]} == {("live", "KO")}
    assert [(f.symbol, f.day) for f in by["rename_gap"]] == [("OLD", "2020-05-18")]
    assert {f.symbol for f in by["duplicate_class"]} == {"GOOG", "SHEL"}
    assert [f.day for f in by["member_days"]] == ["2020"] and "DDD" in by["member_days"][0].detail


async def test_market_data_is_never_edited_and_the_run_is_kept(ctx):
    seed_dirty(ctx)
    bars_before = list(ctx.conn.execute("SELECT * FROM bars ORDER BY symbol, date"))
    with research_context(ctx) as r:
        research_before = list(r.conn.execute("SELECT * FROM bars ORDER BY symbol, date"))
    run = await run_data_checks(ctx)
    assert [tuple(r) for r in ctx.conn.execute("SELECT * FROM bars ORDER BY symbol, date")] == \
        [tuple(r) for r in bars_before]
    with research_context(ctx) as r:
        assert [tuple(x) for x in r.conn.execute("SELECT * FROM bars ORDER BY symbol, date")] == \
            [tuple(x) for x in research_before]
    assert last_data_check(ctx) == run


async def test_no_research_database_runs_the_live_checks_and_says_so(ctx):
    seed_live(ctx, [("VUSA.L", "Vanguard S&P 500 UCITS ETF", "etf", "vusa")], clean("VUSA.L"))
    run = await run_data_checks(ctx)
    assert counts(run)["junk_ticker"] == 1 and "no research database" in run.notes[0]
    assert not ctx.config.research_db_path.exists()


class Explainer:
    name, model = "ollama", "fake"
    fell_back_to: set = set()
    reply_tokens = 200

    def __init__(self):
        self.calls = []

    async def reply(self, messages, schema):
        self.calls.append(messages)
        return {"explanation": f"Probably a data glitch: {messages[-1]['content'].splitlines()[2]}"}


async def test_explanations_are_off_by_default(ctx):
    seed_dirty(ctx)
    model = Explainer()
    run = await run_data_checks(ctx, model=model)
    assert model.calls == [] and run.explained == 0
    assert all(f.explanation is None for c in run.checks for f in c.rows)


async def test_with_explain_on_a_local_model_explains_flags_a_check_at_a_time(ctx):
    seed_dirty(ctx)
    ctx.config = Config(**{**ctx.config.__dict__, "data_checks": DataChecksConfig(explain=True)})
    model = Explainer()
    run = await run_data_checks(ctx, model=model)
    explained = [f for c in run.checks for f in c.rows if f.explanation]
    assert run.explained == len(explained) == len(model.calls) <= EXPLAIN_MAX
    assert {f.check for f in explained} == set(CHECKS), "the first round covers every check that flagged"
    assert explained[0].explanation.startswith("Probably a data glitch")


async def test_a_paid_backend_is_never_asked_for_explanations(ctx, monkeypatch):
    seed_dirty(ctx)
    ctx.config = Config(**{**ctx.config.__dict__, "data_checks": DataChecksConfig(explain=True)})
    monkeypatch.setattr(data_check_service, "model_profiles",
                        lambda config: {"default": SimpleNamespace(provider="claude_api")})
    model = Explainer()
    run = await run_data_checks(ctx, model=model)
    assert model.calls == [] and run.explained == 0 and any("only an Ollama profile" in n for n in run.notes)


async def test_the_checks_run_as_a_job_and_can_be_retried(ctx):
    seed_dirty(ctx)
    runner = JobRunner(ctx)
    job = await runner.wait(submit_data_check(ctx, runner).id)
    assert job.state == "done" and job.result.total > 0 and job.kind == "data_check"
    job.state = "failed"
    again = await runner.wait(retry(ctx, runner, job.id).id)
    assert again.state == "done"


async def test_the_nightly_run_follows_its_setting_and_the_automation_switch(ctx):
    runner = JobRunner(ctx)
    job = nightly_data_check(ctx, runner)
    assert job is not None and job.automatic
    await runner.wait(job.id)
    ctx.config = Config(**{**ctx.config.__dict__, "data_checks": DataChecksConfig(nightly=False)})
    assert nightly_data_check(ctx, runner) is None
