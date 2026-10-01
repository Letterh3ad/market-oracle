"""XBRL company facts point in time: known days, first-filed values, quarters with a derived
fourth, trailing years, SUE and the score timeline."""

from datetime import date

import pytest

from oracle.research import xbrl
from oracle.research.xbrl import Event, Fact, Period

D = date.fromisoformat


def fact(concept, start, end, value, accn="a1", filed="2020-05-01", form="10-Q", unit=None):
    return Fact(concept, unit or xbrl.UNITS[concept], D(start) if start else None, D(end), value, accn, form, D(filed))


def test_parse_keeps_wanted_concepts_units_and_forms_only():
    body = {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            {"start": "2020-01-01", "end": "2020-03-31", "val": 1.5, "accn": "x", "form": "10-Q", "filed": "2020-05-01"},
            {"start": "2020-01-01", "end": "2020-03-31", "val": 1.5, "accn": "y", "form": "8-K", "filed": "2020-04-20"},
            {"start": "2020-01-01", "end": "2020-03-31", "accn": "z", "form": "10-Q", "filed": "2020-05-01"}]}},
        "Assets": {"units": {"USD": [{"end": "2020-03-31", "val": 100, "accn": "x", "form": "10-Q",
                                      "filed": "2020-05-01"}], "EUR": [{"end": "2020-03-31", "val": 9}]}},
        "NetIncomeLoss": {"units": {"USD": [{"end": "2020-03-31", "val": 1}]}}}}}
    got = xbrl.parse_companyfacts(body)
    assert [(f.concept, f.start, f.value, f.form) for f in got] == [
        ("EarningsPerShareDiluted", D("2020-01-01"), 1.5, "10-Q"), ("Assets", None, 100.0, "10-Q")]
    assert xbrl.parse_companyfacts(None) == []


def test_acceptance_times_of_periodic_reports_only():
    cols = {"form": ["10-Q", "4", "10-K/A"], "accessionNumber": ["a", "b", "c"],
            "acceptanceDateTime": ["2020-05-01T18:05:00.000Z", "2020-05-02T10:00:00.000Z", ""]}
    assert xbrl.parse_acceptances(cols) == {"a": "2020-05-01T18:05"}


def test_known_day_is_the_acceptance_day_else_the_filing_date():
    f = fact("Assets", None, "2020-03-31", 1, accn="a", filed="2020-05-04")
    assert xbrl.known_day(f, {"a": "2020-05-01T18:05"}) == D("2020-05-01"), "accepted after 17:30, filed Monday"
    assert xbrl.known_day(f, {}) == D("2020-05-04")


def test_a_restatement_is_not_what_was_known_then():
    facts = [fact("EarningsPerShareDiluted", "2019-01-01", "2019-03-31", 2.0, accn="later", filed="2020-05-01"),
             fact("EarningsPerShareDiluted", "2019-01-01", "2019-03-31", 1.0, accn="first", filed="2019-05-01")]
    [p] = xbrl.first_filed(facts, {}).values()
    assert (p.value, p.known) == (1.0, D("2019-05-01"))


def test_concept_fallbacks_go_by_priority_per_period():
    facts = [fact("EarningsPerShareBasic", "2019-01-01", "2019-03-31", 1.1),
             fact("EarningsPerShareDiluted", "2019-01-01", "2019-03-31", 1.0),
             fact("EarningsPerShareBasic", "2019-04-01", "2019-06-30", 0.7)]
    got = xbrl.by_priority(facts, xbrl.EPS, {})
    assert {k[1]: p.value for k, p in got.items()} == {D("2019-03-31"): 1.0, D("2019-06-30"): 0.7}


def test_gross_profit_is_reported_else_revenue_minus_cost_known_when_both_are():
    facts = [fact("GrossProfit", "2019-01-01", "2019-03-31", 40),
             fact("Revenues", "2019-01-01", "2019-03-31", 100), fact("CostOfRevenue", "2019-01-01", "2019-03-31", 70),
             fact("Revenues", "2019-04-01", "2019-06-30", 100, filed="2019-08-01"),
             fact("CostOfGoodsAndServicesSold", "2019-04-01", "2019-06-30", 75, accn="b", filed="2019-08-09")]
    got = {k[1]: p for k, p in xbrl.gross_profit(facts, {}).items()}
    assert got[D("2019-03-31")].value == 40
    assert (got[D("2019-06-30")].value, got[D("2019-06-30")].known) == (25, D("2019-08-09"))


def q(start, end, value, known):
    return Period(D(start), D(end), value, D(known))


def periods(*ps):
    return {(p.start, p.end): p for p in ps}


def test_the_fourth_quarter_is_the_year_minus_its_first_nine_months():
    got = xbrl.quarters(periods(
        q("2019-01-01", "2019-03-31", 1, "2019-05-01"), q("2019-04-01", "2019-06-30", 2, "2019-08-01"),
        q("2019-07-01", "2019-09-30", 3, "2019-11-01"), q("2019-01-01", "2019-12-31", 10, "2020-02-20")))
    assert [(p.end, p.value, p.known) for p in got][-1] == (D("2019-12-31"), 4, D("2020-02-20"))
    by_nine = xbrl.quarters(periods(q("2019-01-01", "2019-09-30", 5, "2019-11-01"),
                                    q("2019-01-01", "2019-12-31", 9, "2020-02-20")))
    assert [(p.start, p.value) for p in by_nine] == [(D("2019-10-01"), 4)]
    gap = xbrl.quarters(periods(q("2019-01-01", "2019-03-31", 1, "2019-05-01"),
                                q("2019-01-01", "2019-12-31", 10, "2020-02-20")))
    assert len(gap) == 1, "no fourth quarter without the first three"


def test_trailing_year_needs_four_consecutive_quarters():
    qs = [q("2019-01-01", "2019-03-31", 1, "2019-05-01"), q("2019-04-01", "2019-06-30", 2, "2019-08-01"),
          q("2019-07-01", "2019-09-30", 3, "2019-11-01"), q("2019-10-01", "2019-12-31", 4, "2020-02-20"),
          q("2020-04-01", "2020-06-30", 5, "2020-08-01")]
    [ttm] = xbrl.trailing_year(qs)
    assert (ttm.end, ttm.value, ttm.known) == (D("2019-12-31"), 10, D("2020-02-20"))


def quarterly(values, first_year=2017):
    out, y, k = [], first_year, 0
    ends = ("03-31", "06-30", "09-30", "12-31")
    starts = ("01-01", "04-01", "07-01", "10-01")
    for v in values:
        out.append(q(f"{y}-{starts[k]}", f"{y}-{ends[k]}", v, f"{y + (k == 3)}-{('05', '08', '11', '02')[k]}-01"))
        k = (k + 1) % 4
        y += k == 0
    return out


def test_sue_is_the_year_over_year_change_over_the_spread_of_the_changes_before_it():
    qs = quarterly([1, 1, 1, 1, 2, 1, 2, 1, 2, 3, 2, 3])
    ev = xbrl.sue_events(qs)
    assert [e.value for e in ev[:8]] == [None] * 8, "under SUE_MIN earlier changes"
    changes = [1, 0, 1, 0, 0, 2, 0, 2]
    past = changes[:4]
    sd = (sum((x - 0.5) ** 2 for x in past) / 3) ** 0.5
    assert ev[8].value == pytest.approx(0 / sd)
    sd9 = (sum((x - sum(changes[:5]) / 5) ** 2 for x in changes[:5]) / 4) ** 0.5
    assert ev[9].value == pytest.approx(2 / sd9)
    assert ev[9].known == qs[9].known


def test_timeline_drops_stale_comparatives_and_keeps_the_newest_period_on_a_day():
    ev = [Event(D("2020-05-01"), D("2020-03-31"), 1.0), Event(D("2020-05-01"), D("2019-03-31"), 9.0),
          Event(D("2020-06-01"), D("2019-12-31"), 8.0), Event(D("2020-08-01"), D("2020-06-30"), 2.0),
          Event(D("2021-01-01"), D("2020-03-31"), 7.0)]
    assert [e.value for e in xbrl.timeline(ev)] == [1.0, 2.0]


def test_splits_put_per_share_figures_in_todays_shares():
    splits = [(D("2020-08-31"), 4.0), (D("2014-06-09"), 7.0)]
    assert xbrl.split_factor(splits, D("2014-01-01")) == 28
    assert xbrl.split_factor(splits, D("2020-08-31")) == 1, "a split on the known day is not after it"
    [p] = xbrl.todays_shares([q("2020-01-01", "2020-03-31", 2.8, "2020-05-01")], splits)
    assert p.value == pytest.approx(0.7)


def test_ratio_events_need_a_positive_bottom_at_the_same_end():
    top = [q("2019-01-01", "2019-12-31", 30, "2020-02-01"), q("2019-04-01", "2020-03-31", 30, "2020-05-01")]
    bottom = {D("2019-12-31"): Period(None, D("2019-12-31"), 100, D("2020-02-05"))}
    a, b = xbrl.ratio_events(top, bottom)
    assert (a.value, a.known) == (0.3, D("2020-02-05")) and b.value is None
