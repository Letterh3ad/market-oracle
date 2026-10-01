from datetime import date, datetime, timezone

import pytest

from oracle.market.bars import local_date, major_unit


@pytest.mark.parametrize("quoted, expected", [
    ("GBp", ("GBP", 100)),
    ("GBX", ("GBP", 100)),
    ("ZAc", ("ZAR", 100)),
    ("ILA", ("ILS", 100)),
    ("USD", ("USD", 1)),
    ("GBP", ("GBP", 1)),
])
def test_sub_units_convert_to_their_major_currency(quoted, expected):
    assert major_unit(quoted) == expected


def test_a_sydney_summer_open_is_the_next_local_day():
    # 10:00 AEDT on 16 January is 23:00 UTC on the 15th.
    ts = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc).timestamp()
    assert local_date(ts, "Australia/Sydney") == date(2026, 1, 16)


def test_a_sydney_winter_open_is_the_same_day():
    ts = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc).timestamp()
    assert local_date(ts, "Australia/Sydney") == date(2026, 7, 16)


def test_a_new_york_open_either_side_of_dst():
    assert local_date(datetime(2026, 3, 6, 14, 30, tzinfo=timezone.utc).timestamp(), "America/New_York") == date(2026, 3, 6)
    assert local_date(datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc).timestamp(), "America/New_York") == date(2026, 3, 9)


def test_local_date_before_1970():
    from datetime import date

    from oracle.market.bars import local_date
    assert local_date(-252322200, "America/New_York") == date(1962, 1, 2), "IBM's first trade day from Yahoo"
