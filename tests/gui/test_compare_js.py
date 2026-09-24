import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

# Just enough DOM for el() and tip(); no chart library, so draw() always returns null here.
PRELUDE = """
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.dataset = {};
    this.className = ""; this._text = null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = null; }
  set textContent(t) { this._text = t; this.children = []; }
  get textContent() { return this._text ?? this.children.map((c) => typeof c === "string" ? c : c.textContent).join(""); }
  all(pred) { return [...(pred(this) ? [this] : []), ...this.children.flatMap((c) => c.all ? c.all(pred) : [])]; }
}
globalThis.document = { createElement: (tag) => new Node(tag), createTextNode: (text) => ({ textContent: text }) };
globalThis.window = {};
const find = (root, pred) => root.all(pred).filter((n) => n.tag != null);
"""


def run(body: str):
    script = f"""
        {PRELUDE}
        const {{ compareLines }} = await import({json.dumps((STATIC_DIR / "lib" / "compare.js").as_uri())});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# The Ledgers comparison and the Backtest card both feed compareLines() the accounts,
# buy-and-hold, and any configured benchmark lines; a benchmark's name is not in the static
# LINES map, so this proves it gets a label and colour instead of throwing on a lookup miss.
LINES = [
    {"name": "strategy", "caption": "Strategy.", "return": 0.05, "max_fall": -0.1, "trades": 2, "costs": 3.5,
     "points": [["2025-01-01", 0], ["2025-06-01", 0.05]]},
    {"name": "buy_and_hold", "caption": "Buy and hold.", "return": 0.2, "max_fall": -0.2, "trades": 0, "costs": 0,
     "points": [["2025-01-01", 0], ["2025-06-01", 0.2]]},
    {"name": "VWRL.L", "label": "FTSE All-World", "caption": "VWRL.L: a world index, held not traded.",
     "return": 0.08, "max_fall": -0.15, "trades": 0, "costs": 0, "points": [["2025-01-01", 0], ["2025-06-01", 0.08]]},
    {"name": "VUSA.L", "label": "S&P 500", "caption": "VUSA.L: an S&P 500 index, held not traded.",
     "return": 0.1, "max_fall": -0.12, "trades": 0, "costs": 0, "points": [["2025-01-01", 0], ["2025-06-01", 0.1]]},
]


def test_benchmark_lines_get_a_label_and_colour_without_throwing():
    shown = run(f"""
        const lines = {json.dumps(LINES)};
        const c = compareLines(lines, "GBP", "no history yet");
        const swatches = find(c.node, (n) => n.className.includes("swatch"));
        console.log(JSON.stringify([c.node.textContent, swatches.map((s) => s.attrs.style)]));
    """)
    text, styles = shown
    assert "FTSE All-World" in text and "S&P 500" in text
    assert "+8.00%" in text and "+10.00%" in text
    # two accounts/buy-and-hold get the fixed colours, the two benchmarks the bench colours
    assert any("--bench1" in s for s in styles)
    assert any("--bench2" in s for s in styles)


def test_a_benchmark_with_no_label_falls_back_to_its_name():
    shown = run("""
        const lines = [{ name: "SOMEIDX", caption: "SOMEIDX.", return: 0.01, max_fall: 0, trades: 0, costs: 0,
          points: [] }];
        const c = compareLines(lines, "GBP", "no history yet");
        console.log(JSON.stringify(c.node.textContent));
    """)
    assert "SOMEIDX" in shown


def test_draw_returns_null_with_no_chart_library_loaded():
    shown = run(f"""
        const lines = {json.dumps(LINES)};
        const c = compareLines(lines, "GBP", "too few days");
        console.log(JSON.stringify(c.draw()));
    """)
    assert shown is None
