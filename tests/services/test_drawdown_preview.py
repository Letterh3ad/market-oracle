"""The drawdown preview: each real crash on the plan's fund in GBP, the S&P 500 standing in where the
fund's proxy starts too late, what it does to the pot and to the monthly plan, and the recovery."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from oracle.config import Config, PlanConfig
from oracle.services import guardrails_service
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.fund_compare_service import FundSeries
from oracle.services.guardrails_service import EXAMPLE_POT, drawdown_preview
from tests.fakes.clock import FakeClock

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
END = date(2026, 9, 30)
# One V per crash: (peak day, trough day, back day, depth).
VS = ((date(2000, 9, 1), date(2002, 10, 9), date(2006, 10, 2), 0.45),
      (date(2007, 10, 9), date(2009, 3, 9), date(2012, 3, 1), 0.35),
      (date(2020, 2, 19), date(2020, 3, 23), date(2020, 8, 18), 0.25),
      (date(2022, 1, 3), date(2022, 10, 12), date(2023, 12, 1), 0.15))


def v_shape(start: date) -> tuple[tuple[date, float], ...]:
    out, d = [], start
    while d <= END:
        value = 1.0
        for peak, trough, back, depth in VS:
            if peak <= d <= trough:
                value -= depth * (d - peak).days / (trough - peak).days
            elif trough < d < back:
                value -= depth * (back - d).days / (back - trough).days
        if d.weekday() < 5:
            out.append((d, value))
        d += timedelta(days=1)
    return tuple(out)


SERIES = {"all_world": FundSeries("all_world", "All-world", "proxy: ACWI", "total return", 0.0019, v_shape(date(2008, 3, 28))),
          "sp500": FundSeries("sp500", "S&P 500", "proxy: ^SP500TR", "total return", 0.0007, v_shape(date(1999, 1, 4)))}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    stored = dict(SERIES)

    def series(ctx, key, monthly=False):
        if key not in stored:
            raise NoPrices(f"no stored history for {key}")
        return stored[key]

    monkeypatch.setattr(guardrails_service, "fund_series", series)
    monkeypatch.setattr(guardrails_service, "plan_fund", lambda ctx: "all_world")
    with open_context(Config(db_path=tmp_path / "o.db", research_db_path=tmp_path / "r.db"), None, FakeClock(NOW)) as c:
        c.stored = stored
        yield c


def by_name(p) -> dict:
    return {r.crash.name: r for r in p.rows}


def test_each_crash_on_the_plans_fund_with_the_sp500_where_the_fund_starts_too_late(ctx):
    p = drawdown_preview(ctx, 20_000)
    rows = by_name(p)
    assert p.fund == "all_world" and not p.example and p.pot == 20_000
    assert [r.fund for r in p.rows] == ["sp500", "sp500", "all_world", "all_world"]
    assert rows["2000-02"].stand_in and not rows["2020"].stand_in
    covid = rows["2020"]
    assert (covid.fall.peak_day, covid.fall.trough_day) == (date(2020, 2, 19), date(2020, 3, 23))
    assert covid.fall.fall == pytest.approx(-0.25)
    assert covid.pot_loss == pytest.approx(-5_000) and covid.pot_low == pytest.approx(15_000)
    assert covid.months_down == 2 and covid.fall.recovered == date(2020, 8, 18) and covid.months_back == 5
    assert any("stands in" in n for n in p.notes)


def test_paying_in_monthly_gets_back_to_the_money_paid_in_before_the_price_is_back(ctx):
    ctx.config = replace(ctx.config, plan=PlanConfig(monthly=500.0))
    gfc = by_name(drawdown_preview(ctx, 20_000))["2008-09"]
    assert gfc.paying.paid_at_trough > 20_000 and gfc.paying.worst_gap < 0
    assert gfc.paying.even < gfc.fall.recovered and gfc.months_even <= gfc.months_back


def test_no_monthly_amount_means_no_paying_column_and_a_note_saying_how(ctx):
    p = drawdown_preview(ctx)
    assert all(r.paying is None for r in p.rows) and p.example and p.pot == EXAMPLE_POT
    assert any("monthly amount" in n for n in p.notes)


def test_a_crash_no_stored_series_spans_says_so(ctx):
    del ctx.stored["sp500"]
    rows = by_name(drawdown_preview(ctx))
    assert rows["2000-02"].fall is None and "spans" in rows["2000-02"].note
    assert rows["2022"].fall is not None


def test_nothing_stored_asks_for_the_fund_comparison(ctx):
    ctx.stored.clear()
    with pytest.raises(NoPrices, match="Compare funds"):
        drawdown_preview(ctx)


def test_the_pot_must_be_above_zero(ctx):
    with pytest.raises(InvalidQuery, match="pot"):
        drawdown_preview(ctx, 0)


def test_the_note_names_a_price_only_series_as_price_only(ctx):
    ctx.stored["all_world"] = replace(SERIES["all_world"], kind="price only")
    first = drawdown_preview(ctx).notes[0]
    assert first.startswith("In GBP, price only") and "dividends reinvested" not in first
