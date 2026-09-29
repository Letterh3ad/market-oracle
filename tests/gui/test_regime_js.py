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
