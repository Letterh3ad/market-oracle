import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

# A minimal DOM: enough for compare.js to build its chips, table rows and resize handle, and
# for us to find them again and fire the same events a browser would.
PRELUDE = """
class Node {
  constructor(tag) {
    this.tag = tag; this.tagName = tag.toUpperCase();
    this.children = []; this.attrs = {}; this.style = {}; this.dataset = {};
    this._classes = new Set(); this._listeners = {};
  }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) { this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn); }
  dispatch(type, ev = {}) { for (const fn of (this._listeners[type] || []).slice()) fn(ev); }
  get classList() {
    const self = this;
    return {
      add: (c) => self._classes.add(c),
      remove: (c) => self._classes.delete(c),
      contains: (c) => self._classes.has(c),
      toggle: (c, force) => {
        const want = force === undefined ? !self._classes.has(c) : !!force;
        if (want) self._classes.add(c); else self._classes.delete(c);
        return want;
      },
    };
  }
  get className() { return [...this._classes].join(" "); }
  set className(v) { this._classes = new Set(String(v).split(" ").filter(Boolean)); }
  get clientWidth() { return 400; }
  get textContent() { return this.children.map((c) => (typeof c === "string" ? c : c.textContent)).join(""); }
  // depth-first: every descendant, self included, matching the predicate
  findAll(pred) {
    const out = [];
    if (pred(this)) out.push(this);
    for (const c of this.children) if (c.findAll) out.push(...c.findAll(pred));
    return out;
  }
}
globalThis.document = {
  createElement: (tag) => new Node(tag),
  createTextNode: (text) => ({ textContent: text }),
  _listeners: {},
  addEventListener(type, fn) { (this._listeners[type] ??= []).push(fn); },
  removeEventListener(type, fn) { this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn); },
  dispatch(type, ev = {}) { for (const fn of (this._listeners[type] || []).slice()) fn(ev); },
};
globalThis.window = {};

let store = {};
let failing = false;
globalThis.localStorage = {
  getItem(k) { if (failing) throw new Error("storage disabled"); return Object.hasOwn(store, k) ? store[k] : null; },
  setItem(k, v) { if (failing) throw new Error("storage disabled"); store[k] = v; },
};
const failStorage = () => { failing = true; };
const storeRaw = (k) => store[k];

const LINES = [
  { name: "strategy", return: 0.1, max_fall: -0.05, trades: 3, costs: 10, caption: "Strategy caption",
    points: [["2024-01-01", 0], ["2024-01-02", 0.1]] },
  { name: "manual", return: 0.05, max_fall: -0.02, trades: 1, costs: 2, caption: "Manual caption",
    points: [["2024-01-01", 0], ["2024-01-02", 0.05]] },
];

function chipFor(node, name) {
  return node.findAll((n) => n.tag === "button" && n.dataset.line === name)[0];
}
function allChip(node) {
  return node.findAll((n) => n.tag === "button" && !n.dataset.line)[0];
}
function rowFor(node, name) {
  return node.findAll((n) => n.tag === "tr" && n.dataset.line === name)[0];
}
function handle(node) {
  return node.findAll((n) => n.className === "compare-chart-resize")[0];
}
"""


def run(body: str):
    script = f"""
        {PRELUDE}
        const {{ compareLines }} = await import({json.dumps((STATIC_DIR / "lib" / "compare.js").as_uri())});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


def test_click_hides_a_line_and_greys_its_table_row():
    out = run("""
        const c = compareLines(LINES, "USD", "empty", "k1");
        chipFor(c.node, "manual").dispatch("click");
        console.log(JSON.stringify({
          chipOff: chipFor(c.node, "manual").classList.contains("chip-off"),
          rowFaded: rowFor(c.node, "manual").classList.contains("faded"),
          otherChipOff: chipFor(c.node, "strategy").classList.contains("chip-off"),
        }));
    """)
    assert out == {"chipOff": True, "rowFaded": True, "otherChipOff": False}


def test_click_again_shows_the_line_again():
    out = run("""
        const c = compareLines(LINES, "USD", "empty", "k2");
        chipFor(c.node, "manual").dispatch("click");
        chipFor(c.node, "manual").dispatch("click");
        console.log(JSON.stringify({
          chipOff: chipFor(c.node, "manual").classList.contains("chip-off"),
          rowFaded: rowFor(c.node, "manual").classList.contains("faded"),
        }));
    """)
    assert out == {"chipOff": False, "rowFaded": False}


def test_double_click_solos_the_line():
    out = run("""
        const c = compareLines(LINES, "USD", "empty", "k3");
        chipFor(c.node, "manual").dispatch("dblclick");
        console.log(JSON.stringify({
          manualOff: chipFor(c.node, "manual").classList.contains("chip-off"),
          strategyOff: chipFor(c.node, "strategy").classList.contains("chip-off"),
          strategyRowFaded: rowFor(c.node, "strategy").classList.contains("faded"),
        }));
    """)
    assert out == {"manualOff": False, "strategyOff": True, "strategyRowFaded": True}


def test_all_restores_every_line():
    out = run("""
        const c = compareLines(LINES, "USD", "empty", "k4");
        chipFor(c.node, "manual").dispatch("dblclick");
        allChip(c.node).dispatch("click");
        console.log(JSON.stringify({
          manualOff: chipFor(c.node, "manual").classList.contains("chip-off"),
          strategyOff: chipFor(c.node, "strategy").classList.contains("chip-off"),
        }));
    """)
    assert out == {"manualOff": False, "strategyOff": False}


def test_hidden_set_and_height_are_remembered_per_chart():
    out = run("""
        const c = compareLines(LINES, "USD", "empty", "k5");
        chipFor(c.node, "manual").dispatch("click");
        handle(c.node).dispatch("pointerdown", { clientY: 100 });
        document.dispatch("pointermove", { clientY: 180 });
        document.dispatch("pointerup", {});
        // a second chart under the same key picks up what the first one left behind
        const c2 = compareLines(LINES, "USD", "empty", "k5");
        console.log(JSON.stringify({
          manualOffAgain: chipFor(c2.node, "manual").classList.contains("chip-off"),
          height: c2.node.findAll((n) => n.tag === "div" && n.className === "compare-chart")[0].style.height,
        }));
    """)
    assert out == {"manualOffAgain": True, "height": "440px"}


def test_resize_clamps_to_the_minimum_height():
    out = run("""
        const c = compareLines(LINES, "USD", "empty", "k6");
        handle(c.node).dispatch("pointerdown", { clientY: 500 });
        document.dispatch("pointermove", { clientY: -1000 }); // a huge drag up past the floor
        document.dispatch("pointerup", {});
        const box = c.node.findAll((n) => n.tag === "div" && n.className === "compare-chart")[0];
        console.log(JSON.stringify(box.style.height));
    """)
    assert out == "120px"


def test_a_storage_read_failure_falls_back_to_defaults_without_throwing():
    out = run("""
        failStorage();
        const c = compareLines(LINES, "USD", "empty", "k7");
        console.log(JSON.stringify({
          manualOff: chipFor(c.node, "manual").classList.contains("chip-off"),
          height: c.node.findAll((n) => n.tag === "div" && n.className === "compare-chart")[0].style.height,
        }));
    """)
    assert out == {"manualOff": False, "height": "360px"}


def test_a_storage_write_failure_during_a_click_does_not_throw():
    out = run("""
        failStorage();
        const c = compareLines(LINES, "USD", "empty", "k8");
        chipFor(c.node, "manual").dispatch("click"); // writeState throws internally; must not propagate
        console.log(JSON.stringify(chipFor(c.node, "manual").classList.contains("chip-off")));
    """)
    assert out is True


def test_nothing_stored_renders_every_line_visible():
    out = run("""
        const c = compareLines(LINES, "USD", "empty", "k9");
        console.log(JSON.stringify(LINES.map((l) => chipFor(c.node, l.name).classList.contains("chip-off"))));
    """)
    assert out == [False, False]


# The Ledgers comparison and the Backtest card both feed compareLines() the accounts,
# buy-and-hold, and any configured benchmark lines; a benchmark's name is not in the static
# LINES map, so this proves it gets a label and colour instead of throwing on a lookup miss.
BENCH_LINES = [
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
        const lines = {json.dumps(BENCH_LINES)};
        const c = compareLines(lines, "GBP", "no history yet", "bench-k1");
        const swatches = c.node.findAll((n) => n.className.includes("swatch"));
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
        const c = compareLines(lines, "GBP", "no history yet", "bench-k2");
        console.log(JSON.stringify(c.node.textContent));
    """)
    assert "SOMEIDX" in shown


def test_draw_and_destroy_do_not_throw_with_no_chart_library_loaded():
    shown = run(f"""
        const lines = {json.dumps(BENCH_LINES)};
        const c = compareLines(lines, "GBP", "too few days", "bench-k3");
        c.draw();
        c.destroy();
        console.log(JSON.stringify("ok"));
    """)
    assert shown == "ok"
