import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import PRELUDE

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

CARD = {
    "enabled": True, "note": None, "day": "2026-09-28", "at": "2026-09-28T21:00:00Z",
    "state": {"day": "2026-09-28", "mood": "volatile_down", "flags": ["clustered", "rebound"],
              "readings": {"trend_gap": -0.041, "vol_pct": 0.86},
              "base_weights": {"trend": 0.2, "momentum": 0.0, "low_vol": 0.25},
              "weights": {"trend": 0.3, "momentum": 0.0, "low_vol": 0.2},
              "warnings": {"momentum": ["rebound", "clustered"]}, "good": {"trend": ["long trend"]},
              "health": {"trend": 1.25, "momentum": 0.5, "low_vol": 1.0}, "tilt": {},
              "unscored": ["earnings_drift"], "note": None,
              "signs": {"momentum": [{"name": "rebound", "warning": True, "reason": "Momentum crashes on a snap back.",
                                      "source": "Daniel-Moskowitz 2016"}],
                        "trend": [{"name": "big_year", "warning": False, "reason": "Trend earns most in extremes.",
                                   "source": "Moskowitz-Ooi-Pedersen 2012"}]}},
    "decisions": [{"day": "2026-09-28", "symbol": "KO", "action": "buy", "cash": 5000.0, "reason": "buy lean at 71%",
                   "explanation": {"mood": "volatile_down", "parts": {"low_vol": 0.21, "trend": -0.08,
                                                                      "hold": 0.02, "reversion": 0.01}}}],
}


def run(body: str, card: dict = CARD):
    script = f"""
        {PRELUDE}
        const banner = {{ hidden: true }}, message = {{ textContent: "" }};
        document.getElementById = (id) => (id === "error-banner" ? banner : message);
        const {{ regimeCard, contributions }} = await import({json.dumps((STATIC_DIR / "lib" / "regime.js").as_uri())});
        let repainted = 0;
        const node = regimeCard({json.dumps(card)}, "GBP", () => {{ repainted += 1; }});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_the_mood_flags_weights_warnings_health_and_each_decisions_parts():
    text = run("console.log(JSON.stringify(node.textContent));")
    for piece in ("Mood on 2026-09-28: volatile and falling", "(volatile_down)", "stocks moving together",
                  "sharp rebound after a falling year", "trend gap -0.041", "Low volatility", "25%", "20%",
                  "rebound, clustered", "long trend", "x1.25", "x0.50", "buy lean at 71%",
                  "Low volatility +0.21, Trend -0.08, Buy and hold +0.02",
                  "No listing had a score from Earnings drift on this step", "fetched only when",
                  "Momentum warning: rebound. Momentum crashes on a snap back. (Daniel-Moskowitz 2016)",
                  "Trend good sign: big_year. Trend earns most in extremes. (Moskowitz-Ooi-Pedersen 2012)"):
        assert piece in text, piece
    assert "Mean reversion +0.01" not in text, "only the three largest parts"


def test_a_note_from_the_book_shows_above_the_reading():
    card = {**CARD, "note": "every method's weight is 0 after its warnings, record and tilt",
            "state": {**CARD["state"], "note": "every method's weight is 0 after its warnings, record and tilt"}}
    text = run("console.log(JSON.stringify(node.textContent));", card)
    assert "every method's weight is 0" in text


def test_off_the_card_says_so_and_the_switch_turns_it_back_on():
    off = {**CARD, "enabled": False, "note": "the regime account is off: nothing was decided.", "state": None,
           "decisions": []}
    shown = run("""
        const box = labelled(node, "Regime account on");
        const before = [box.checked, node.textContent];
        box.checked = true; box.fire("change"); await settle();
        console.log(JSON.stringify([...before, asked, repainted]));
    """, off)
    checked, text, asked, repainted = shown
    assert checked is False and "the regime account is off" in text and "Off: the regime step decides nothing" in text
    assert "has not decided anything yet" in text
    assert asked == ["PUT /api/ledgers/regime"] and repainted == 1


AI_CARD = {**CARD, "account": "regime-ai", "model": "", "research_profile": "default", "lag": None,
           "state": {**CARD["state"], "tilt": {"trend": 1.4, "momentum": 0.6}},
           "tilt": {"day": "2026-09-28", "tilt": {"trend": 1.4, "momentum": 0.6, "low_vol": 1.0},
                    "rules": {"trend": 0.25, "momentum": 0.0, "low_vol": 0.3}, "reason": "Momentum crashes on rebounds.",
                    "error": None, "model": "default (ollama qwen3:4b)"}}


def test_the_regime_ai_card_shows_the_rules_weight_and_tilt_beside_the_final_and_the_reason():
    text = run("console.log(JSON.stringify(node.textContent));", AI_CARD)
    for piece in ("Regime-AI reading", "Model tilt on 2026-09-28 (default (ollama qwen3:4b)): Momentum crashes on rebounds.",
                  "x1.40", "x0.60", "Rules", "Tilt", "Regime-AI decisions", "research slot's (default)",
                  "On: each tick's regime-ai step trades the blend"):
        assert piece in text, piece
    assert "Rules only: no model" not in text


def test_a_method_without_a_health_record_says_so_instead_of_a_neutral_1():
    card = {**CARD, "state": {**CARD["state"], "recorded": ["trend"]}}
    text = run("console.log(JSON.stringify(node.textContent));", card)
    assert "x1.25" in text and "no record yet" in text and "x0.50" not in text and "x1.00" not in text


def test_a_failed_tilt_says_the_rules_weights_were_traded_and_a_lag_is_warned():
    card = {**AI_CARD, "lag": "the step read 2026-09-18, 5 weekdays behind yesterday",
            "tilt": {**AI_CARD["tilt"], "tilt": {}, "reason": None, "error": "Ollama is not reachable"}}
    text = run("console.log(JSON.stringify(node.textContent));", card)
    assert "No model tilt on 2026-09-28: Ollama is not reachable. The rules weights were traded" in text
    assert "Warning: the step read 2026-09-18, 5 weekdays behind yesterday." in text


def test_the_regime_ai_switch_and_model_pick_save_to_its_own_route():
    shown = run("""
        ANSWERS["GET /api/models"] = { profiles: [{ name: "default" }, { name: "small" }] };
        const again = regimeCard(%s, "GBP", () => { repainted += 1; });
        await settle();
        const pick = labelled(again, "Regime-AI tilt model");
        const options = pick.children.map((o) => o.attrs.value);
        pick.value = "small"; pick.fire("change"); await settle();
        const box = labelled(again, "Regime-AI account on");
        box.checked = false; box.fire("change"); await settle();
        console.log(JSON.stringify([options, asked.filter((a) => a.startsWith("PUT")), repainted]));
    """ % json.dumps(AI_CARD), AI_CARD)
    options, puts, repainted = shown
    assert options == ["", "default", "small"]
    assert puts == ["PUT /api/ledgers/regime-ai", "PUT /api/ledgers/regime-ai"] and repainted == 2


def test_the_ledgers_tab_module_loads():
    script = f"""
        {PRELUDE}
        document.getElementById = () => new Node("div");
        await import({json.dumps((STATIC_DIR / "tabs" / "ledgers.js").as_uri())});
        console.log(JSON.stringify("ok"));
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
