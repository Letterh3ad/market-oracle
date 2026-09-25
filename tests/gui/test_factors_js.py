import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")


def run(expr: str):
    script = f"""
        const m = await import({json.dumps((STATIC_DIR / "lib" / "factors.js").as_uri())});
        console.log(JSON.stringify({expr}));
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_sorting_puts_unranked_listings_last_either_way():
    items = json.dumps([{"symbol": s} for s in ("A", "B", "C", "D")])
    ranks = json.dumps({"A": {"momentum_pct": 0.2}, "B": {"momentum_pct": None}, "C": {"momentum_pct": 0.9}})
    down = run(f"m.sortByRank({items}, {ranks}, 'momentum_pct').map((i) => i.symbol)")
    up = run(f"m.sortByRank({items}, {ranks}, 'momentum_pct', false).map((i) => i.symbol)")
    assert down == ["C", "A", "B", "D"] and up == ["A", "C", "B", "D"]


def test_the_sentence_reads_ranks_and_says_when_outside_the_set():
    row = {"momentum": 0.234, "momentum_pct": 0.8, "volatility": 0.25, "low_vol_pct": 0.4, "as_of": "2026-09-25",
           "note": None, "in_set": False}
    assert run(f"m.factorSentence({json.dumps(row)}, 12)") == (
        "12-1 momentum +23%, stronger than 80% of the set; 60-day volatility 25% a year, calmer than 40%; "
        "as of 2026-09-25, against 12 watched, held and discovered listings (this one is not among them).")
    young = {"momentum": None, "momentum_pct": None, "volatility": 0.1, "low_vol_pct": 0.5, "as_of": "2026-09-25",
             "note": "less than a year of prices", "in_set": True}
    assert run(f"m.factorSentence({json.dumps(young)}, 1)").endswith(
        "against 1 watched, held and discovered listing. Missing: less than a year of prices.")
