"""The deflated Sharpe beside each backtest and walk-forward line, with its variants tried."""

import random
from dataclasses import replace
from datetime import date, timedelta

import pytest

from oracle.analysts.lab import deflated_sharpe, moments
from oracle.broker.walk_forward import judge
from oracle.services.backtest_service import TRADED_VARIANTS, Backtest, Line, WalkForward, deflated


def _line(name: str, daily: list[float]) -> Line:
    value, points = 1.0, [(date(2020, 1, 1), 0.0)]
    for k, r in enumerate(daily, 1):
        value *= 1 + r
        points.append((date(2020, 1, 1) + timedelta(k), value - 1))
    return Line(name, name, tuple(points), 0.0, 0, 0.0)


def _series(values: list[float]) -> list[tuple[date, float]]:
    return [(date(2020, 1, 1) + timedelta(k), v) for k, v in enumerate(values)]


def test_each_line_is_deflated_by_its_variants_tried():
    rng = random.Random(5)
    daily = {n: [rng.gauss(0.0004, 0.01) for _ in range(500)] for n in ("index", "buy_and_hold", "strategy", "regime")}
    b = Backtest("main", date(2020, 1, 1), date(2021, 6, 1), 10, 0, (), tuple(_line(n, d) for n, d in daily.items()),
                 (), (), ())
    whole, walked = deflated(b)
    got = {d.name: d for d in whole}
    assert {n: d.variants for n, d in got.items()} == {"index": 1, "buy_and_hold": 1, "strategy": TRADED_VARIANTS,
                                                      "regime": TRADED_VARIANTS}
    m = moments(daily["strategy"])
    assert got["strategy"].dsr == pytest.approx(deflated_sharpe(m, TRADED_VARIANTS), abs=1e-9)
    assert got["strategy"].sharpe == pytest.approx(m.sharpe * 252 ** 0.5, abs=1e-6)
    assert got["index"].dsr == pytest.approx(deflated_sharpe(moments(daily["index"]), 1), abs=1e-9)
    assert walked == (), "no walk-forward, no walk-forward figures"


def test_walk_forward_lines_are_deflated_from_their_pooled_segments():
    seg = _series([1.0, 1.01, 1.0, 1.02, 1.03])
    equities = {"buy_and_hold": [seg, seg], "strategy": [seg, _series([1.0, 0.99, 1.01, 1.0, 1.02])]}
    lines = judge(equities, "buy_and_hold")
    assert lines[1].overall.moments.n == 8, "both segments' daily returns pooled"
    b = Backtest("main", date(2020, 1, 1), date(2021, 6, 1), 10, 0, (), (), (), (), (),
                 WalkForward(6, ((date(2020, 1, 1), date(2020, 6, 30)),) * 2, lines, ()))
    _, walked = deflated(b)
    assert [(d.name, d.variants) for d in walked] == [("buy_and_hold", 1), ("strategy", TRADED_VARIANTS)]
    assert walked[1].dsr == pytest.approx(deflated_sharpe(lines[1].overall.moments, TRADED_VARIANTS))
    old = replace(b, walk_forward=replace(b.walk_forward, lines=tuple(
        replace(x, overall=replace(x.overall, moments=None)) for x in lines)))
    assert deflated(old)[1] == (), "a walk-forward kept before the moments were recorded shows none"
