from datetime import datetime

import pytest

from oracle.services.compute_window import next_open, window_open


@pytest.mark.parametrize("when, open_", [
    (datetime(2026, 10, 5, 0, 59), False),   # Monday before 01:00
    (datetime(2026, 10, 5, 1, 0), True),
    (datetime(2026, 10, 5, 17, 59), True),
    (datetime(2026, 10, 5, 18, 0), False),
    (datetime(2026, 10, 4, 7, 59), True),    # Sunday
    (datetime(2026, 10, 4, 8, 0), False),
    (datetime(2026, 10, 3, 12, 0), False),   # Saturday
])
def test_the_window_is_weekdays_1_to_18_and_weekends_1_to_8(when, open_):
    assert window_open(when) is open_


def test_the_window_next_opens_at_the_coming_1am():
    assert next_open(datetime(2026, 10, 4, 8, 30)) == datetime(2026, 10, 5, 1, 0)
    assert next_open(datetime(2026, 10, 5, 0, 30)) == datetime(2026, 10, 5, 1, 0)
    open_now = datetime(2026, 10, 5, 9, 0)
    assert next_open(open_now) == open_now
