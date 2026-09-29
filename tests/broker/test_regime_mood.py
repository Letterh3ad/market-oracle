import random

import pytest

from oracle.analysts.indicators import YEAR
from oracle.broker.regime.mood import Composite, step_returns
from tests.analysts.bars import weekdays

N = 400


def one(returns):
    """A composite of a single listing with these daily returns."""
    return Composite({"X": dict(zip(weekdays(len(returns)), returns))})


def alternating(n, drift, size):
    return [drift + size * (-1) ** i for i in range(n)]


def last(c):
    return c.mood(c.dates[-1])


@pytest.mark.parametrize(("returns", "mood"), [
    (alternating(N - 20, 0.001, 0.01) + alternating(20, 0.001, 0.002), "calm_up"),
    (alternating(N - 20, 0.001, 0.005) + alternating(20, 0.002, 0.03), "volatile_up"),
    (alternating(N - 20, -0.001, 0.01) + alternating(20, -0.001, 0.002), "calm_down"),
    (alternating(N - 20, -0.001, 0.005) + alternating(20, -0.002, 0.03), "volatile_down"),
])
def test_the_mood_reads_the_composites_trend_and_volatility(returns, mood):
    m = last(one(returns))
    assert m.mood == mood
    assert set(m.readings) >= {"trend_gap", "vol_pct", "ret_12m", "ret_1m"}
    assert (m.readings["trend_gap"] >= 0) == mood.endswith("_up")


def test_no_mood_under_a_year_of_composite():
    c = one(alternating(YEAR - 1, 0.001, 0.01))
    assert c.moods[-1] is None and c.mood(c.dates[-1]) is None
    assert one(alternating(YEAR + 1, 0.001, 0.01)).moods[-1] is not None
    assert c.mood(c.dates[0].replace(year=2000)) is None


def test_rebound_flags_a_sharp_month_after_a_losing_year():
    m = last(one(alternating(300, -0.002, 0.004) + [0.005] * 21))
    assert m.readings["ret_12m"] < 0 and m.readings["ret_1m"] > 0.08
    assert "rebound" in m.flags
    assert "rebound" not in last(one(alternating(321, 0.001, 0.004))).flags


def test_choppy_flags_three_crossings_of_the_average_in_sixty_days():
    flat = alternating(340, 0.0, 0.002)
    chop = ([0.01] * 10 + [-0.01] * 10) * 3
    m = last(one(flat + chop))
    assert "choppy" in m.flags
    assert "choppy" not in last(one(alternating(400, 0.001, 0.002))).flags


def test_clustered_flags_listings_moving_together_more_than_usual():
    rng = random.Random(3)
    days = weekdays(N)
    common = [rng.gauss(0, 0.01) for _ in days]
    returns = {f"L{k}": {d: (common[i] if i >= N - 60 else rng.gauss(0, 0.01)) for i, d in enumerate(days)}
               for k in range(8)}
    c = Composite(returns)
    m = c.mood(days[-1])
    assert m.readings["corr_pct"] == pytest.approx(1.0)
    assert "clustered" in m.flags
    # Half the listings mirror the other half: less together than in the year before.
    split = {f"L{k}": {d: common[i] * (-1) ** k if i >= N - 60 else common[i] + rng.gauss(0, 0.002)
                       for i, d in enumerate(days)} for k in range(8)}
    m = Composite(split).mood(days[-1])
    assert m.readings["corr_pct"] < 0.1 and "clustered" not in m.flags


def test_no_correlation_reading_with_one_listing():
    m = last(one(alternating(N, 0.001, 0.01)))
    assert "corr_pct" not in m.readings and "clustered" not in m.flags


def test_the_composite_is_the_equal_weight_mean_of_the_days_returns():
    days = weekdays(3)
    c = Composite({"A": {days[1]: 0.02, days[2]: 0.01}, "B": {days[1]: -0.01}})
    assert c.returns == pytest.approx([0.005, 0.01])
    assert c.return_on(days[1]) == pytest.approx(0.005)
    assert c.return_on(days[0]) is None
    assert c.level == pytest.approx([1.005, 1.005 * 1.01])


def test_step_returns_drop_breaks_and_missing_prices():
    days = weekdays(5)
    r = step_returns(list(zip(days, [100.0, 101.0, 40.0, 0.0, 41.0])))
    assert r == {days[1]: pytest.approx(0.01)}


def test_moods_on_a_day_do_not_change_when_later_returns_arrive():
    rng = random.Random(5)
    days = weekdays(N + 60)
    returns = {f"L{k}": {d: rng.gauss(0.0005, 0.01) for d in days} for k in range(6)}
    cut = days[N - 1]
    early = Composite({s: {d: v for d, v in r.items() if d <= cut} for s, r in returns.items()})
    wild = {s: {d: (v if d <= cut else rng.choice((-0.4, 0.5, 0.9))) for d, v in r.items()} for s, r in returns.items()}
    late = Composite(wild)
    assert [late.mood(d) for d in days[:N]] == [early.mood(d) for d in days[:N]]
