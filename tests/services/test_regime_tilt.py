"""The regime-ai line's daily tilt: the prompt, the validation of the model's answer, and one
stored answer (or failure) per day that a restart and every later book reuse."""

import json
import logging
from datetime import date, datetime, timezone

import httpx
import pytest

from oracle.broker.regime import METHODS, RegimeState
from oracle.config import Config
from oracle.research.llm import ModelError
from oracle.services.context import open_context
from oracle.services import regime_tilt
from oracle.services.regime_tilt import (
    BadTilt, DailyTilt, MAX_REASON, SYSTEM, tilt_messages, tilt_schema, validated,
)
from oracle.store.regime import TiltRepo
from tests.fakes.clock import FakeClock
from tests.fakes.tilt_model import TiltModel

REAL_MODEL = regime_tilt.tilt_model
"""Taken before conftest swaps it out, for the tests that answer over a mock transport."""
NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
DAY, BEFORE = date(2026, 9, 28), date(2026, 9, 25)
ANSWER = {**dict.fromkeys(METHODS, 1.0), "momentum": 0.6, "low_vol": 1.3, "reason": "Momentum lags; calm market."}


def state(day=DAY, **kw):
    weights = {m: 1 / len(METHODS) for m in METHODS}
    return RegimeState(**{
        "day": day, "mood": "volatile_down", "flags": frozenset({"rebound"}),
        "readings": {"trend_gap": -0.041, "vol_pct": 0.91, "ret_12m": -0.12, "ret_1m": 0.09},
        "base_weights": weights, "weights": {**weights, "momentum": 0.05},
        "warnings": {"momentum": ("rebound",)}, "good": {"quality_value": ("rebound",)},
        "health": {**dict.fromkeys(METHODS, 1.0), "momentum": 0.5}, "tilt": {}, **kw})


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        yield c


def test_the_prompt_gives_the_mood_readings_and_each_methods_preset_rules_weight_health_and_signs():
    [system, user] = tilt_messages(state(), 0.5)
    assert system == {"role": "system", "content": SYSTEM.format(low=0.5, high=1.5)}
    text = user["content"]
    assert "2026-09-28" in text and "volatile_down" in text and "rebound" in text
    assert "trend_gap -0.041" in text and "vol_pct 0.910" in text
    line = next(x for x in text.splitlines() if x.startswith("- momentum"))
    assert "preset 0.125" in line and "rules weight 0.050" in line and "health 0.50" in line
    assert "warning rebound" in line and "Daniel" in line, "each sign's reason and source"
    assert "good sign rebound" in next(x for x in text.splitlines() if x.startswith("- quality_value"))
    assert tilt_messages(state(), 0.5) == [system, user], "deterministic"


def test_the_schema_asks_for_every_method_and_a_reason():
    schema = tilt_schema()
    assert set(schema["required"]) == {*METHODS, "reason"}
    assert all(schema["properties"][m] == {"type": "number"} for m in METHODS)


def test_an_answer_is_clipped_to_the_cap_and_keeps_only_known_methods_numbers():
    tilt, reason = validated({"trend": 9, "hold": 0.1, "momentum": 0.8, "astrology": 1.4, "low_vol": "high",
                              "reversion": True, "reason": "  Trend is strong.\n "}, 0.5)
    assert tilt == {"trend": 1.5, "hold": 0.5, "momentum": 0.8}
    assert reason == "Trend is strong."


def test_a_long_reason_is_cut_short():
    _, reason = validated({"trend": 1.1, "reason": "word " * 200}, 0.5)
    assert len(reason) <= MAX_REASON and reason.endswith("...")


@pytest.mark.parametrize(("reply", "why"), [
    ("not json at all", "not a JSON object"),
    ([1, 2], "not a JSON object"),
    ({"trend": 1.2}, "no reason"),
    ({"trend": 1.2, "reason": "  "}, "no reason"),
    ({"astrology": 1.2, "reason": "x"}, "no multiplier for any known method"),
])
def test_an_answer_without_a_number_for_a_known_method_or_a_reason_is_refused(reply, why):
    with pytest.raises(BadTilt, match=why):
        validated(reply, 0.5)


def test_the_step_day_asks_the_model_once_stores_it_and_a_restart_reuses_it(ctx):
    model = TiltModel(ANSWER)
    tilt = DailyTilt(ctx, "regime-ai", DAY, 0.5, model)
    assert tilt(state()) == {**dict.fromkeys(METHODS, 1.0), "momentum": 0.6, "low_vol": 1.3}
    assert tilt(state()) == tilt.today.tilt and len(model.asked) == 1
    stored = TiltRepo(ctx.conn).get("regime-ai", DAY)
    assert (stored.reason, stored.error, stored.model) == ("Momentum lags; calm market.", None, "fake tilt-1")
    assert stored.rules == state().weights
    again = TiltModel(ANSWER)
    restarted = DailyTilt(ctx, "regime-ai", DAY, 0.5, again)
    assert restarted(state()) == stored.tilt and again.asked == [] and restarted.today == stored


def test_past_days_answer_from_stored_tilts_and_never_ask_the_model(ctx):
    model = TiltModel(ANSWER)
    DailyTilt(ctx, "regime-ai", BEFORE, 0.5, model)(state(BEFORE))
    later = TiltModel(ANSWER)
    tilt = DailyTilt(ctx, "regime-ai", DAY, 0.5, later)
    assert tilt(state(BEFORE)) == TiltRepo(ctx.conn).get("regime-ai", BEFORE).tilt
    assert tilt(state(date(2026, 9, 24))) == {}, "a day never stepped has no tilt"
    assert later.asked == [] and tilt.today is None
    assert TiltRepo(ctx.conn).all("regime") == {}, "stored per account"


@pytest.mark.parametrize(("model", "why"), [
    (TiltModel("{broken"), "not a JSON object"),
    (TiltModel(error=ModelError("Ollama took longer than 120s to answer")), "longer than 120s"),
    (TiltModel(error=ModelError("Ollama is not reachable at http://127.0.0.1:11434")), "not reachable"),
    (TiltModel(error=RuntimeError("boom")), "boom"),
])
def test_a_failure_is_no_tilt_logged_and_stored_so_the_day_is_not_asked_again(ctx, caplog, model, why):
    with caplog.at_level(logging.WARNING):
        tilt = DailyTilt(ctx, "regime-ai", DAY, 0.5, model)
        assert tilt(state()) == {}
    assert why in tilt.today.error and tilt.today.tilt == {} and why in caplog.text
    assert why in TiltRepo(ctx.conn).get("regime-ai", DAY).error
    again = TiltModel(ANSWER)
    assert DailyTilt(ctx, "regime-ai", DAY, 0.5, again)(state()) == {} and again.asked == []


def test_an_unknown_model_profile_is_a_stored_failure_naming_it(ctx, monkeypatch):
    monkeypatch.setattr(regime_tilt, "tilt_model", REAL_MODEL)
    from dataclasses import replace

    ctx.config = replace(ctx.config, regime_ai_account=replace(ctx.config.regime_ai_account, model="nope"))
    tilt = DailyTilt(ctx, "regime-ai", DAY, 0.5)
    assert tilt(state()) == {} and "nope" in tilt.today.error


def test_the_default_model_is_the_research_slots_on_ollama_at_temperature_0(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_tilt, "tilt_model", REAL_MODEL)
    seen = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": json.dumps(ANSWER)}})

    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), httpx.MockTransport(answer)) as c:
        model, label = REAL_MODEL(c)
        assert label.startswith("default (ollama ")
        tilt = DailyTilt(c, "regime-ai", DAY, 0.5)
        assert tilt(state())["momentum"] == 0.6
    [payload] = seen
    assert payload["options"]["temperature"] == 0 and set(payload["format"]["required"]) == {*METHODS, "reason"}


def test_ollama_down_is_no_tilt_saying_so(tmp_path, monkeypatch):
    monkeypatch.setattr(regime_tilt, "tilt_model", REAL_MODEL)
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), httpx.MockTransport(down)) as c:
        tilt = DailyTilt(c, "regime-ai", DAY, 0.5)
        assert tilt(state()) == {} and "not reachable" in tilt.today.error


def test_without_a_fake_no_test_reaches_a_real_model(ctx):
    tilt = DailyTilt(ctx, "regime-ai", DAY, 0.5)
    assert tilt(state()) == {"hold": 1.0} and tilt.today.model == "stand-in"
