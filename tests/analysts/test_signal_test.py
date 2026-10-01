import math
import random
from datetime import date, timedelta

import pytest

from oracle.analysts.predictive import newey_west_t, spearman
from oracle.analysts.signal_test import (
    INSIDE, Panel, excess, momentum_12_1, overall, power, reversal_1m, window_result,
)
from oracle.market.bars import Bar

D0 = date(2020, 1, 1)


def days(n: int) -> list[date]:
    return [D0 + timedelta(k) for k in range(n)]


def bars(symbol: str, closes, opens=None, adj=None) -> list[Bar]:
    out = []
    for k, c in enumerate(closes):
        if c is None:
            continue
        o = opens[k] if opens else c
        a = adj[k] if adj else c
        out.append(Bar(symbol, D0 + timedelta(k), o, o, o, c, a, 1, "USD", "yahoo"))
    return out


def panel(n: int, series: dict) -> Panel:
    p = Panel(days(n), [frozenset(series)] * n, {s: "X" for s in series})
    for s, closes in series.items():
        p.add(s, bars(s, closes))
    return p


def test_forward_is_open_to_open_and_refuses_gaps_and_split_sized_moves():
    p = panel(6, {"A": [10, 10, 11, 12, 13, 14], "B": [10, 10, None, 12, 13, 14], "C": [10, 10, 30, 30, 30, 30]})
    assert p.forward("A", 1, 3) == pytest.approx(13 / 10 - 1)
    assert p.forward("B", 1, 3) is None, "a missing bar between"
    assert p.forward("C", 1, 3) is None, "a tripling in a day is taken for an unadjusted split"
    assert p.forward("A", 3, 3) is None, "past the calendar"
    assert p.forward("A", 0, 2) is None, "needs the close before"


def test_adjusted_prices_carry_dividends_and_splits():
    p = Panel(days(3), [frozenset("A")] * 3, {})
    # A 2:1 split on day 2: raw prices halve, Yahoo's adjusted closes do not.
    p.add("A", bars("A", [100, 100, 50], adj=[50, 50, 50]))
    assert p.trailing("A", 0, 2) == pytest.approx(0.0)


def test_excess_is_over_the_other_same_sector_names_else_the_universe():
    rets = {"A": 0.10, "B": 0.0, "C": 0.02, "D": 0.04, "E": 0.5, "F": 0.3}
    sectors = {"A": "T", "B": "T", "C": "T", "D": "T", "E": "U", "F": None}
    out = excess(rets, sectors)
    assert out["A"] == pytest.approx(0.10 - 0.02)
    assert out["E"] == pytest.approx(0.5 - (sum(rets.values()) - 0.5) / 5), "one in its sector: the universe"
    assert out["F"] == pytest.approx(0.3 - (sum(rets.values()) - 0.3) / 5), "no sector: the universe"
    assert excess({"A": 0.1, "B": 0.2, "C": 0.3}, {}) == {}, "under MIN_SECTOR others: left out"


def test_momentum_skips_the_last_month_and_reversal_is_minus_the_last_month():
    n = 300
    up = [100 * 1.001 ** k for k in range(n)]
    p = panel(n, {"A": up})
    i = 290
    assert momentum_12_1(p, i, ["A"])["A"] == pytest.approx(up[i - 22] / up[i - 253] - 1)
    assert reversal_1m(p, i, ["A"])["A"] == pytest.approx(-(up[i - 1] / up[i - 22] - 1))
    assert momentum_12_1(p, 200, ["A"]) == {}, "under a year of closes"


def test_scores_read_nothing_from_the_decision_day_on():
    n = 300
    rng = random.Random(1)
    base = {s: [100 * math.exp(sum(rng.gauss(0, 0.01) for _ in range(k + 1))) for k in range(n)] for s in "ABCDEF"}
    i = 280
    before = panel(n, base)
    changed = {s: v[:i] + [x * rng.uniform(0.6, 1.6) for x in v[i:]] for s, v in base.items()}
    after = panel(n, changed)
    for score in (momentum_12_1, reversal_1m):
        assert score(before, i, list(base)) == score(after, i, list(base))


def test_power():
    # The spec's figure: 51 names over 4 years at 20 days needs an IC of about 0.04.
    assert power(51, 1000, 20) == pytest.approx(2 * math.sqrt(20 / 50000))
    assert power(1, 100, 5) is None and power(50, 0, 5) is None


def simulate(ic: float, names: int, n_days: int, h: int, seed: int) -> float:
    """The harness's t over one simulated panel: a held-still score whose rank correlation with
    the h-day forward return is `ic`."""
    rng = random.Random(seed)
    rho = 2 * math.sin(math.pi * ic / 6)  # Spearman to Pearson for normals
    z = [rng.gauss(0, 1) for _ in range(names)]
    e = [[rng.gauss(0, 1) for _ in range(names)] for _ in range(n_days + h)]
    ics, run = [], [sum(e[k][i] for k in range(h)) for i in range(names)]
    for d in range(n_days):
        if d:
            run = [run[i] - e[d - 1][i] + e[d + h - 1][i] for i in range(names)]
        y = [x / math.sqrt(h) for x in run]
        s = [rho * y[i] + math.sqrt(1 - rho * rho) * z[i] for i in range(names)]
        ics.append(spearman(s, y))
    return newey_west_t(ics, h)


def test_power_matches_a_simulation_with_a_known_ic():
    for names, n_days, h in ((50, 400, 5), (30, 1000, 20)):
        target = power(names, n_days, h)
        ts = [simulate(target, names, n_days, h, seed) for seed in range(30)]
        assert 1.8 < sum(ts) / len(ts) < 2.5, f"h {h}: mean t {sum(ts) / len(ts):.2f} at the power IC {target:.4f}"
        nulls = [simulate(0.0, names, n_days, h, 100 + seed) for seed in range(30)]
        assert abs(sum(nulls) / len(nulls)) < 0.5


def ics_on(start: date, n: int, ic: float, h: int = 1) -> dict:
    return {start + timedelta(k): (ic + (0.01 if k % 2 else -0.01), 100, start + timedelta(k + h)) for k in range(n)}


def test_a_window_counts_only_days_whose_exit_is_inside_it():
    spans = [(date(2020, 1, 1), date(2020, 1, 31)), (date(2020, 2, 1), date(2020, 2, 29))]
    ics = ics_on(date(2020, 1, 1), 60, 0.05, h=5)
    r = window_result("train", ics, spans, 5, 0.1)
    assert r.segments[-1].days == 29 - 5, "the last five days exit after the window"
    assert r.counted == 2 and r.verdict == "PASS" and r.missing_share == 0.1 and r.names == 100


def test_a_window_with_one_segment_too_short_and_a_negative_ic():
    spans = [(date(2020, 1, 1), date(2020, 1, 31)), (date(2020, 2, 1), date(2020, 2, 29)),
             (date(2020, 3, 1), date(2020, 3, 31))]
    ics = {**ics_on(date(2020, 1, 1), 31, -0.05), **ics_on(date(2020, 2, 1), 10, 0.05),
           **ics_on(date(2020, 3, 1), 31, -0.05)}
    r = window_result("confirm", ics, spans, 1, None)
    assert [s.counted for s in r.segments] == [True, False, True]
    assert r.segments[1].why == "10 IC days (needs 20)"
    assert r.verdict == "FAIL" and r.positive == 0 and r.ic < 0


def test_a_model_signal_counts_only_after_its_cutoff():
    spans = [(date(2020, 1, 1), date(2020, 1, 31)), (date(2020, 2, 1), date(2020, 3, 31)),
             (date(2020, 4, 1), date(2020, 5, 31))]
    ics = ics_on(date(2020, 1, 1), 152, 0.05)
    r = window_result("confirm", ics, spans, 1, None, cutoff=date(2020, 2, 29))
    assert r.segments[0].why == INSIDE and not r.segments[0].counted
    assert r.segments[1].days == 31 and r.segments[1].counted, "trimmed to its days after the cutoff"
    assert r.days == 31 + 61 - 1


def test_overall_needs_every_horizon_to_pass():
    assert overall(["PASS", "PASS"]) == "PASS"
    assert overall(["PASS", "FAIL"]) == "FAIL"
    assert overall(["PASS", "INCONCLUSIVE"]) == "INCONCLUSIVE"
    assert overall([]) == "INCONCLUSIVE"


def test_a_fail_with_a_positive_ic_under_the_observed_power_says_it_could_not_see_it():
    spans = [(date(2020, 1, 1), date(2020, 1, 31)), (date(2020, 2, 1), date(2020, 2, 29))]
    noisy = {date(2020, 1, 1) + timedelta(k): (0.004 + (0.2 if k % 2 else -0.2), 100, date(2020, 1, 1) + timedelta(k))
             for k in range(60)}
    w = window_result("confirm", noisy, spans, 1, None)
    assert w.verdict == "FAIL" and 0 < w.ic < w.observed_power
    assert w.verdict_text == f"FAIL (could not see an IC this small: needed {w.observed_power:.4f})"
    negative = {d: (-x, n, e) for d, (x, n, e) in noisy.items()}
    assert window_result("confirm", negative, spans, 1, None).verdict_text == "FAIL", "a negative IC is a plain FAIL"
    passing = window_result("confirm", ics_on(date(2020, 1, 1), 60, 0.05), spans, 1, None)
    assert passing.verdict_text == "PASS"
