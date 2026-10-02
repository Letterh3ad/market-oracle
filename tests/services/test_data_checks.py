"""The data-error checks, one by one: each flags seeded bad data and stays quiet on clean data."""

from datetime import date, timedelta

from oracle.services.data_checks import (
    BAD_PRICE, DUPLICATE_CLASS, JUNK_TICKER, MEMBER_DAYS, MISSING_DIVIDENDS, RENAME_GAP, SPLIT_JUMP, STALE_BARS,
    Alias, dividend_steps, duplicate_flags, junk_flags, member_day_flags, rename_gap_flags, series_flags, stale_flags,
    unadjusted_payer,
)
from oracle.store.testbed import YearTicker


def days(start: str, n: int) -> list[str]:
    d, out = date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(1)
    return out


def bars(closes, start="2020-01-01", factors=None):
    """(day, open, high, low, close, adj_close, volume); `factors` gives adj_close / close per bar."""
    out = []
    for i, (d, c) in enumerate(zip(days(start, len(closes)), closes)):
        f = factors[i] if factors else 1.0
        out.append((d, c * 0.99, c * 1.01 + i * 1e-6, c * 0.98, c, c * f, 1000))
    return out


def wiggle(n, base=100.0):
    return [base + (i % 7) * 0.5 for i in range(n)]


def of(flags, check):
    return [f for f in flags if f.check == check]


# split-sized jumps

def test_a_split_sized_jump_with_no_recorded_split_is_flagged():
    closes = wiggle(20)
    closes[10:] = [c / 4 for c in closes[10:]]
    flags = of(series_flags("research", "AAA", bars(closes), {}), SPLIT_JUMP)
    assert len(flags) == 1 and flags[0].symbol == "AAA" and flags[0].day == bars(closes)[10][0]
    assert "1:4" in flags[0].detail or "0.25" in flags[0].detail


def test_a_jump_explained_by_a_recorded_split_is_not_flagged():
    closes = wiggle(20)
    closes[10:] = [c / 4 for c in closes[10:]]
    split_day = bars(closes)[10][0]
    assert not of(series_flags("research", "AAA", bars(closes), {"AAA": [(split_day, 4.0)]}), SPLIT_JUMP)


def test_ordinary_moves_and_odd_sized_jumps_are_not_split_sized():
    for move in (1.3, 1.5, 1 / 1.5):  # 3:2 sized moves were mostly real news on the live copy
        closes = wiggle(20)
        closes[10:] = [c * move for c in closes[10:]]
        assert not of(series_flags("research", "AAA", bars(closes), {}), SPLIT_JUMP)


def test_a_jump_across_a_long_gap_in_the_bars_is_not_a_split():
    b = bars(wiggle(20))
    later = bars([c * 2 for c in wiggle(20)], "2021-01-01")
    assert not of(series_flags("research", "AAA", b + later, {}), SPLIT_JUMP)


# stale or repeated bars

def test_a_run_of_identical_bars_is_flagged_once():
    b = bars(wiggle(20))
    frozen = b[5]
    b[5:12] = [(d, *frozen[1:]) for d, *_ in b[5:12]]
    flags = of(series_flags("live", "AAA", b, {}), STALE_BARS)
    assert len(flags) == 1 and flags[0].day == b[5][0] and "7 bars" in flags[0].detail


def test_a_short_repeat_and_clean_bars_are_not_flagged():
    b = bars(wiggle(20))
    b[6] = (b[6][0], *b[5][1:])
    assert not of(series_flags("live", "AAA", b, {}), STALE_BARS)


def test_a_series_behind_the_newest_bar_or_with_none_is_stale():
    flags = stale_flags("live", {"OLD": "2026-09-01", "NEW": "2026-09-30", "NONE": None}, "2026-10-01")
    assert {f.symbol for f in flags} == {"OLD", "NONE"} and all(f.check == STALE_BARS for f in flags)


def test_series_within_a_week_of_the_newest_are_not_stale():
    assert stale_flags("live", {"A": "2026-09-25", "B": "2026-10-01"}, "2026-10-01") == []


# zero or negative prices

def test_zero_and_negative_prices_are_flagged_per_bar():
    b = bars(wiggle(10))
    b[3] = (b[3][0], 0.0, *b[3][2:])
    b[7] = (b[7][0], b[7][1], b[7][2], b[7][3], -1.0, -1.0, 5)
    flags = of(series_flags("research", "AAA", b, {}), BAD_PRICE)
    assert [f.day for f in flags] == [b[3][0], b[7][0]] and "open 0" in flags[0].detail


def test_positive_prices_and_missing_opens_are_not_flagged():
    b = bars(wiggle(10))
    b[2] = (b[2][0], None, None, None, b[2][4], b[2][5], None)
    assert not of(series_flags("research", "AAA", b, {}), BAD_PRICE)


# junk or fund tickers in stock sets

def test_funds_and_junk_tickers_are_flagged():
    flags = junk_flags("live", [("VUSA.L", "Vanguard S&P 500 UCITS ETF", "etf"), ("ABC-WT", "Abc warrants", "equity"),
                                ("^GSPC", None, None), ("XYZ", "Xyz Acquisition Corp", "equity"),
                                ("GONE", None, None)], need_listing=True)
    assert {f.symbol for f in flags} == {"VUSA.L", "ABC-WT", "^GSPC", "XYZ", "GONE"}
    assert all(f.check == JUNK_TICKER for f in flags)


def test_ordinary_stocks_and_share_classes_are_not_junk():
    assert junk_flags("live", [("BRK-B", "Berkshire Hathaway", "equity"), ("BT-A.L", "BT Group", "equity"),
                               ("AIR.PA", "Airbus", "equity"), ("MC.PA", "LVMH", "equity"),
                               ("MSFT", "Microsoft", "equity"), ("NTRS", "Northern Trust Corp", "equity")],
                      need_listing=True) == []


def test_testbed_members_without_a_name_are_only_judged_by_ticker():
    assert junk_flags("research", [("AAA", None, None)], need_listing=False) == []


# missing dividends

def payer_factors(n, every=60, gap=None):
    """adj_close / close rising toward 1 at each ex-dividend day, as Yahoo adjusts; `gap` skips steps there."""
    steps = [i for i in range(every, n, every) if not (gap and gap[0] <= i < gap[1])]
    out, f = [], 1.0 - 0.005 * len(steps)
    for i in range(n):
        if i in steps:
            f += 0.005
        out.append(f)
    return out


def test_a_payer_with_a_long_stretch_without_an_adjustment_is_flagged():
    n = 1200
    b = bars(wiggle(n), "2015-01-01", payer_factors(n, gap=(300, 700)))
    flags = of(series_flags("research", "KO", b, {}), MISSING_DIVIDENDS)
    assert len(flags) == 1 and "no dividend adjustment" in flags[0].detail


def test_a_tiny_dividend_still_counts_as_an_adjustment():
    n = 1200
    tiny = [1 - 8e-5 * (20 - i // 60) for i in range(n)]  # $0.01 on a $120 stock each quarter
    assert len(dividend_steps(bars(wiggle(n), "2015-01-01", tiny))) == 19


def test_a_regular_payer_and_a_non_payer_are_not_flagged():
    n = 1200
    assert not of(series_flags("research", "KO", bars(wiggle(n), "2015-01-01", payer_factors(n)), {}),
                  MISSING_DIVIDENDS)
    assert not of(series_flags("research", "GOOG", bars(wiggle(n), "2015-01-01"), {}), MISSING_DIVIDENDS)


def test_a_live_series_never_adjusted_for_a_known_payer_is_flagged():
    n = 400
    research_steps = dividend_steps(bars(wiggle(n), "2020-01-01", payer_factors(n)))
    live = bars(wiggle(n), "2020-01-01")
    flag = unadjusted_payer("live", "KO", live, research_steps)
    assert flag is not None and flag.check == MISSING_DIVIDENDS and "adj_close equals close" in flag.detail


def test_a_live_series_adjusted_or_of_a_non_payer_is_not_flagged():
    n = 400
    research_steps = dividend_steps(bars(wiggle(n), "2020-01-01", payer_factors(n)))
    assert unadjusted_payer("live", "KO", bars(wiggle(n), "2020-01-01", payer_factors(n)), research_steps) is None
    assert unadjusted_payer("live", "GOOG", bars(wiggle(n), "2020-01-01"), []) is None


# rename gaps

ALIAS = Alias("OLD", "NEW", date(2020, 3, 2), True, "accepted", "2026-10-01", date(2020, 6, 30))


def test_member_days_around_a_rename_without_a_bar_are_flagged():
    member = days("2020-01-01", 100)
    own = set(member[:40])  # OLD stops trading on 2020-02-25, NEW starts a week later
    new = set(member[45:])
    flags = rename_gap_flags([ALIAS], {"OLD": member}, lambda t, a, b: own if t == "OLD" else new)
    assert len(flags) == 1 and flags[0].check == RENAME_GAP and "5 member-days" in flags[0].detail


def test_a_rename_priced_across_is_not_flagged():
    member = days("2020-01-01", 100)
    flags = rename_gap_flags([ALIAS], {"OLD": member},
                             lambda t, a, b: set(member[:41]) if t == "OLD" else set(member[41:]))
    assert flags == []


# duplicate share classes

def test_two_members_of_one_company_on_the_same_days_are_flagged():
    shared = days("2020-01-01", 30)
    flags = duplicate_flags("research", [(("GOOGL", "GOOG"), "one SEC filer")],
                            {"GOOGL": set(shared), "GOOG": set(shared[10:])})
    assert len(flags) == 1 and flags[0].check == DUPLICATE_CLASS and "20 days" in flags[0].detail


def test_one_company_under_two_tickers_on_different_days_is_a_rename_not_a_duplicate():
    flags = duplicate_flags("research", [(("ABC", "COR"), "one SEC filer")],
                            {"ABC": set(days("2020-01-01", 30)), "COR": set(days("2021-01-01", 30))})
    assert flags == []


def test_watchlists_holding_two_listings_of_one_company_are_flagged():
    flags = duplicate_flags("live", [(("SHEL", "SHEL.L"), "one company in the universe")], None)
    assert [f.symbol for f in flags] == ["SHEL"]


# testbed member-days missing by year

def test_years_with_member_days_missing_are_flagged_naming_the_worst():
    cells = [YearTicker(2012, "AAA", 250, 250, 250), YearTicker(2012, "BBB", 250, 0, 0),
             YearTicker(2013, "AAA", 250, 250, 250)]
    flags = member_day_flags(cells)
    assert len(flags) == 1 and flags[0].check == MEMBER_DAYS and flags[0].day == "2012"
    assert "250 of 500" in flags[0].detail and "BBB" in flags[0].detail


def test_fully_priced_years_are_not_flagged():
    assert member_day_flags([YearTicker(2012, "AAA", 250, 240, 250)]) == []
