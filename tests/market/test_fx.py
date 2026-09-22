from datetime import date

from oracle.market.fx import FxRate, pick_rate

RATES = [FxRate("USD", date(2026, 9, d), 1.30 + d / 100, "yahoo") for d in (14, 15, 18)]


def test_the_days_own_rate_wins():
    assert pick_rate(RATES, date(2026, 9, 15)).units_per_base == 1.45


def test_a_weekend_uses_the_last_earlier_rate():
    assert pick_rate(RATES, date(2026, 9, 17)).date == date(2026, 9, 15)


def test_never_a_later_rate():
    assert pick_rate(RATES, date(2026, 9, 13)) is None


def test_five_days_back_is_the_limit():
    assert pick_rate(RATES, date(2026, 9, 23)).date == date(2026, 9, 18)
    assert pick_rate(RATES, date(2026, 9, 24)) is None


def test_no_rates_no_answer():
    assert pick_rate([], date(2026, 9, 15)) is None
