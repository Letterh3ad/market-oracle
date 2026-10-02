from datetime import date, timedelta

import pytest

from oracle.broker.trend_overlay import MONTHS, Session, crash_rows, figures, judge, month_end_decisions, simulate


def month_ends(closes, start=date(2000, 1, 31)):
    """One session per month (its last day), then a session on the 1st of the next month."""
    out, d = [], start
    for c in closes:
        out.append(Session(d, c, c))
        d = (d + timedelta(days=5)).replace(day=1)
        d = (d + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return out


def daily(start, closes, opens=None):
    days, d = [], start
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return [Session(day, (opens or closes)[i], c) for i, (day, c) in enumerate(zip(days, closes))]


def test_a_month_end_holds_at_or_above_its_ten_month_average_and_needs_ten_months():
    s = month_ends([100.0] * 9 + [100.0, 90.0, 120.0]) + [Session(date(2001, 1, 2), 1, 1)]
    got = month_end_decisions(s)
    assert [h for _, h in got[:9]] == [None] * 9
    assert got[9][1] is True  # equal to its average holds
    assert got[10][1] is False  # 90 under the average of the last 10 (99)
    assert got[11][1] is True
    assert MONTHS == 10


def test_the_last_month_is_no_month_end_until_a_later_month_has_a_session():
    s = daily(date(2024, 1, 1), [100.0] * 30)  # Jan 1 to Feb 9
    assert [d for d, _ in month_end_decisions(s)] == [date(2024, 1, 31)]


def test_no_look_ahead_a_decision_and_the_equity_to_a_day_ignore_every_later_close():
    closes = [100 * (1.01 ** (i % 37)) * (0.98 ** (i // 50)) for i in range(800)]
    s = daily(date(2001, 1, 1), closes)
    cut = 500
    later = s[:cut] + [Session(x.day, x.open * 3, x.close * 3) for x in s[cut:]]
    a, b = dict(month_end_decisions(s)), dict(month_end_decisions(later))
    for d in a:
        if d < s[cut - 1].day:
            assert a[d] == b[d]
    ra = simulate(s, s[0].day, s[-1].day, cost=0.001)
    rb = simulate(later, s[0].day, s[-1].day, cost=0.001)
    assert ra.equity[:cut] == rb.equity[:cut]


def test_a_decision_at_a_month_end_close_acts_at_the_next_sessions_open_with_a_cost():
    # Jan 2024 month ends: flat 100 for 11 months, then a fall to 50 on Nov 29 (a month end).
    s = month_ends([100.0] * 10 + [50.0], start=date(2023, 1, 31))
    s.append(Session(date(2023, 12, 1), 40.0, 30.0))
    s.append(Session(date(2023, 12, 4), 30.0, 10.0))
    r = simulate(s, s[0].day, s[-1].day, cost=0.01)
    v = dict(r.equity)
    assert v[date(2023, 11, 30)] == pytest.approx(0.5)
    # sold at Dec 1's open (40), less 1%, then cash
    assert v[date(2023, 12, 1)] == pytest.approx(0.5 * 40 / 50 * 0.99)
    assert v[date(2023, 12, 4)] == v[date(2023, 12, 1)]
    assert r.switches == 1 and r.cash_sessions == 2 and r.sessions == 13
    held = dict(r.held)
    assert held[date(2023, 12, 4)] == pytest.approx(0.1)


def test_a_buy_back_pays_the_cost_and_earns_from_the_open():
    s = month_ends([100.0] * 10 + [50.0, 200.0], start=date(2023, 1, 31))
    s.append(Session(date(2024, 1, 2), 100.0, 110.0))
    r = simulate(s, date(2023, 12, 1), s[-1].day, cost=0.01)
    v = r.equity
    assert v[0] == (date(2023, 12, 31), 1.0)  # starts in cash: Nov's month end said below
    assert v[1][1] == pytest.approx(0.99 * 110 / 100)
    assert r.switches == 1 and r.cash_sessions == 1


def test_a_missing_open_switches_at_that_sessions_close():
    s = month_ends([100.0] * 10 + [50.0], start=date(2023, 1, 31))
    s.append(Session(date(2023, 12, 1), None, 30.0))
    r = simulate(s, s[0].day, s[-1].day, cost=0.0)
    assert dict(r.equity)[date(2023, 12, 1)] == pytest.approx(0.3)


def test_before_ten_month_ends_the_rule_holds_the_index():
    s = daily(date(2001, 1, 1), [100.0 - i * 0.1 for i in range(150)])
    r = simulate(s, s[0].day, s[-1].day, cost=0.001)
    assert r.switches == 0 and r.cash_sessions == 0
    assert [v for _, v in r.equity] == pytest.approx([v for _, v in r.held])


def test_cash_earns_the_stored_t_bill_yield_when_given():
    s = month_ends([100.0] * 10 + [50.0], start=date(2023, 1, 31))
    s.append(Session(date(2023, 12, 1), 50.0, 50.0))
    s.append(Session(date(2023, 12, 4), 50.0, 50.0))
    r = simulate(s, s[0].day, s[-1].day, cost=0.0, cash_yield={date(2023, 12, 1): 5.04})
    v = dict(r.equity)
    assert v[date(2023, 12, 4)] == pytest.approx(v[date(2023, 12, 1)] * 1.0504 ** (1 / 252))


def test_cash_earns_nothing_before_the_first_stored_yield_then_the_latest_known():
    s = month_ends([100.0] * 10 + [50.0], start=date(2023, 1, 31))
    s += [Session(date(2023, 12, d), 50.0, 50.0) for d in (1, 4, 5, 6)]
    r = simulate(s, s[0].day, s[-1].day, cost=0.0,
                 cash_yield={date(2023, 12, 4): 2.52, date(2023, 12, 5): 5.04, date(2023, 12, 9): 9.0})
    v = [x for _, x in r.equity[-4:]]
    assert v[1] == v[0]
    assert v[2] == pytest.approx(v[1] * 1.0252 ** (1 / 252))
    assert v[3] == pytest.approx(v[2] * 1.0504 ** (1 / 252))


def test_figures_cagr_worst_fall_and_cash_share():
    s = daily(date(2001, 1, 1), [100.0, 50.0, 100.0, 200.0])
    r = simulate(s, s[0].day, s[-1].day, cost=0.0)
    f = figures(r.held)
    assert f.ret == pytest.approx(1.0) and f.max_fall == pytest.approx(-0.5)
    assert f.cagr == pytest.approx(2 ** (365.25 / 3) - 1)
    assert f.cash_share == 0


def test_judge_pass_needs_a_third_off_the_worst_fall_and_at_most_a_point_less_a_year():
    assert judge(-0.30, -0.45, 0.090, 0.100).passed
    assert not judge(-0.31, -0.45, 0.095, 0.100).passed  # fall cut under a third
    assert not judge(-0.20, -0.45, 0.089, 0.100).passed  # gave up 1.1 points
    j = judge(-0.25, -0.50, 0.08, 0.10)
    assert j.fall_cut == pytest.approx(0.5) and j.given_up == pytest.approx(0.02) and not j.passed


def test_crash_rows_clip_to_the_window_and_read_both_lines():
    eq = [(date(2008, 1, 2), 1.0), (date(2008, 6, 2), 0.9), (date(2009, 12, 31), 0.95), (date(2010, 1, 4), 1.2)]
    held = [(d, v * 2) for d, v in eq]
    rows = crash_rows(eq, held, [False, True, True, False], (("2008", date(2008, 1, 1), date(2009, 12, 31)),
                                                             ("2020", date(2020, 1, 1), date(2020, 12, 31))))
    assert len(rows) == 1
    r = rows[0]
    assert r.name == "2008" and r.trend_return == pytest.approx(-0.05) and r.held_return == pytest.approx(-0.05)
    assert r.trend_fall == pytest.approx(-0.1) and r.cash_share == pytest.approx(2 / 3)
