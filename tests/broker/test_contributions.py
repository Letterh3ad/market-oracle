from datetime import date

import pytest

from oracle.broker.contributions import (
    BANDS, NONE, YEARLY, Rebalance, align, cash_drag, lump_vs_drip, mix, rolling_monthly, spread,
)
from oracle.broker.projection import FeeModel


def months(*closes: float) -> list[tuple[date, float]]:
    return [(date(2000 + i // 12, i % 12 + 1, 28), c) for i, c in enumerate(closes)]


def rising(n: int, rate: float = 0.01) -> list[tuple[date, float]]:
    return months(*[(1 + rate) ** i for i in range(n + 1)])


def test_lump_vs_drip_runs_from_every_start_month_with_a_full_drip_after_it():
    starts = lump_vs_drip(rising(30), 1200.0)
    assert len(starts) == 30 - 12 + 1
    assert starts[0].start == date(2000, 1, 28)
    assert starts[-1].end == date(2002, 7, 28)


def test_lump_wins_on_a_rising_series_and_drip_on_a_falling_one():
    assert all(s.lump > s.drip for s in lump_vs_drip(rising(24), 1200.0))
    assert all(s.lump < s.drip for s in lump_vs_drip(rising(24, -0.01), 1200.0))


def test_lump_and_drip_by_hand_over_a_jump_in_the_last_month():
    # Flat for 11 months then doubles: the lump doubles; every tranche doubles too.
    series = months(*([1.0] * 12 + [2.0]))
    [s] = lump_vs_drip(series, 1200.0)
    assert s.lump == pytest.approx(2400.0) and s.drip == pytest.approx(2400.0)
    # Doubles in the first month then flat: the lump doubles, only the first tranche does.
    [s] = lump_vs_drip(months(*([1.0] + [2.0] * 12)), 1200.0)
    assert s.lump == pytest.approx(2400.0) and s.drip == pytest.approx(1300.0)


def test_cash_waiting_in_a_drip_earns_the_cash_rate():
    flat = months(*([1.0] * 13))
    [s] = lump_vs_drip(flat, 1200.0, cash_rate=0.05)
    assert s.drip > s.lump == pytest.approx(1200.0)


def test_drip_pays_dealing_on_every_tranche():
    [s] = lump_vs_drip(months(*([1.0] * 13)), 1200.0, FeeModel(dealing=1.0))
    assert s.lump == pytest.approx(1199.0) and s.drip == pytest.approx(1188.0)


def test_spread_summarises_lump_over_drip():
    sp = spread([0.1, -0.05, 0.02, 0.03])
    assert sp.count == 4 and sp.wins == pytest.approx(0.75)
    assert sp.worst == pytest.approx(-0.05) and sp.best == pytest.approx(0.1)
    assert sp.median == pytest.approx(0.025)


def test_rolling_monthly_runs_every_window():
    windows = rolling_monthly(rising(36), 100.0, 2)
    assert len(windows) == 36 - 24 + 1
    assert windows[0].contributed == pytest.approx(2400.0)
    assert windows[0].value > 2400.0


def two_funds():
    a = months(*([100.0, 200.0] + [200.0] * 23))
    b = months(*([100.0] * 25))
    return a, b


def test_without_rebalancing_the_mix_drifts():
    a, b = two_funds()
    r = mix(a, b, 0.5, Rebalance(NONE), start=100.0)
    assert r.value == pytest.approx(150.0)
    assert r.weight == pytest.approx(2 / 3)
    assert r.trades == 0


def test_yearly_rebalancing_resets_the_weights_each_twelfth_month():
    a, b = two_funds()
    r = mix(a, b, 0.5, Rebalance(YEARLY), start=100.0)
    assert r.weight == pytest.approx(0.5)
    assert r.rebalanced == (date(2001, 1, 28),)
    assert r.trades == 2


def test_bands_rebalance_as_soon_as_the_drift_passes_the_points():
    a, b = two_funds()
    r = mix(a, b, 0.5, Rebalance(BANDS, 0.05), start=100.0)
    assert r.rebalanced == (date(2000, 2, 28),)
    assert r.weight == pytest.approx(0.5)


def test_bands_hold_inside_the_band():
    a = months(*([100.0, 104.0] + [104.0] * 12))
    b = months(*([100.0] * 14))
    assert mix(a, b, 0.5, Rebalance(BANDS, 0.05), start=100.0).rebalanced == ()


def test_rebalancing_pays_dealing_on_both_trades():
    a, b = two_funds()
    r = mix(a, b, 0.5, Rebalance(BANDS, 0.05), FeeModel(dealing=1.0), start=100.0)
    assert r.fees == pytest.approx(2.0)
    assert r.value == pytest.approx(148.0)


def test_contributions_split_by_the_target_weights():
    a, b = months(100.0, 100.0, 100.0), months(100.0, 100.0, 100.0)
    r = mix(a, b, 0.75, Rebalance(NONE), contributions=[100.0, 100.0])
    assert r.value == pytest.approx(200.0) and r.weight == pytest.approx(0.75)
    assert r.contributed == pytest.approx(200.0)


def test_mix_reports_its_worst_fall():
    a = months(100.0, 50.0, 100.0)
    b = months(100.0, 100.0, 100.0)
    assert mix(a, b, 1.0, Rebalance(NONE), start=100.0).worst_fall == pytest.approx(0.5)


def test_align_keeps_the_months_both_have():
    a = [(date(2000, 1, 31), 1.0), (date(2000, 2, 29), 2.0)]
    b = [(date(2000, 2, 28), 5.0), (date(2000, 3, 31), 6.0)]
    assert align(a, b) == ([(date(2000, 2, 29), 2.0)], [(date(2000, 2, 28), 5.0)])


def test_cash_drag_by_hand():
    up = months(100.0, 110.0)
    d = cash_drag(up, 0.1)
    assert d.invested == pytest.approx(1.10)
    assert d.held == pytest.approx(1.09)
    assert d.given_up == pytest.approx(1.10 ** 12 - 1.09 ** 12)
