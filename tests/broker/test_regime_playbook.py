import pytest

from oracle.broker.regime import METHODS
from oracle.broker.regime.mood import Mood
from oracle.broker.regime.playbook import SIGNS, Day, firing, health, top_fifth

CALM = {"trend_gap": 0.02, "vol_pct": 0.4, "ret_12m": 0.08, "ret_1m": 0.01, "corr_pct": 0.5}


def day(mood="calm_up", flags=(), picks=None, sectors=None, **readings):
    return Day(Mood(mood, frozenset(flags), {**CALM, **readings}), picks or {}, sectors or {})


FIVE = ("A", "B", "C", "D", "E")
QUIET = day("volatile_up", ret_12m=0.05)
"""A day on which no sign but reversion's volatile_up fires."""

CASES = {
    ("trend", "choppy"): (day(flags={"choppy"}), day()),
    ("trend", "rebound"): (day(flags={"rebound"}), day()),
    ("trend", "big_year"): (day(ret_12m=-0.2), day(ret_12m=0.19)),
    ("momentum", "rebound"): (day(flags={"rebound"}), day()),
    ("momentum", "volatile_down"): (day("volatile_down"), day("calm_down")),
    ("momentum", "clustered"): (day(flags={"clustered"}), day()),
    ("momentum", "sector_crowded"): (
        day(picks={"momentum": FIVE}, sectors=dict.fromkeys("ABC", "Tech")),
        day(picks={"momentum": FIVE}, sectors={**dict.fromkeys("AB", "Tech"), "C": "Energy"})),
    ("reversion", "volatile_down"): (day("volatile_down"), day("volatile_up")),
    ("reversion", "volatile_up"): (day("volatile_up"), day("calm_up")),
    ("low_vol", "rebound"): (day(flags={"rebound"}), day(flags={"choppy"})),
    ("quality_value", "rebound"): (day(flags={"rebound"}), day()),
    ("quality_value", "volatile_down"): (day("volatile_down"), day("calm_down")),
}


def test_every_sign_has_a_case_a_reason_and_a_source():
    assert {(s.method, s.name) for s in SIGNS} == set(CASES)
    assert len(SIGNS) == len(CASES)
    for s in SIGNS:
        assert s.method in METHODS and s.reason.endswith(".") and s.source[-4:].isdigit()
        assert "\u2014" not in s.reason + s.source


@pytest.mark.parametrize(("method", "name"), sorted(CASES))
def test_each_sign_fires_on_its_case_and_not_on_its_near_miss(method, name):
    sign = next(s for s in SIGNS if (s.method, s.name) == (method, name))
    fires, misses = CASES[method, name]
    assert sign.fires(fires) and not sign.fires(misses)
    warnings, good = firing(fires)
    assert name in (warnings if sign.warning else good)[method]
    warnings, good = firing(misses)
    assert name not in (warnings if sign.warning else good).get(method, ())


def test_crowding_needs_enough_picks_and_counts_unsectored_ones():
    tech = dict.fromkeys("ABCD", "Tech")
    assert not firing(day(picks={"momentum": ("A", "B", "C")}, sectors=tech))[0]
    assert "sector_crowded" in firing(day(picks={"momentum": ("A", "B", "C", "D")}, sectors=tech))[0]["momentum"]
    assert not firing(day(picks={"momentum": ("A", "B", "X", "Y")}, sectors=tech))[0]


def test_firing_groups_the_signs_by_method_and_kind():
    warnings, good = firing(day("volatile_down", flags={"rebound", "clustered"}, ret_12m=-0.3))
    assert warnings == {"trend": ("rebound",), "momentum": ("rebound", "volatile_down", "clustered"),
                        "reversion": ("volatile_down",), "low_vol": ("rebound",)}
    assert good == {"trend": ("big_year",), "quality_value": ("rebound", "volatile_down")}
    assert firing(QUIET) == ({}, {"reversion": ("volatile_up",)})


def test_the_top_fifth_rounds_up_and_breaks_ties_by_symbol():
    scores = {s: v for s, v in zip("ABCDEFG", (0.1, 0.9, 0.9, -0.2, 0.5, 0.0, 0.3))}
    assert top_fifth(scores) == ("B", "C")
    assert top_fifth(dict(list(scores.items())[:4])) == ()
    assert top_fifth(dict.fromkeys("ABCDEF", 0.3)) == ()


def test_health_maps_the_record_into_its_range_and_is_neutral_without_one():
    assert health([0.05] * 19) == 1.0
    assert health([0.01] * 20) == pytest.approx(1.2)
    assert health([-0.01] * 30) == pytest.approx(0.8)
    assert health([0.2] * 20) == 1.5 and health([-0.2] * 20) == 0.5
    assert health([0.0] * 60) == 1.0
