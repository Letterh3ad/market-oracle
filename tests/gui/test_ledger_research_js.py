import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

# Just enough DOM for el() and busyButton, plus a fetch that records each request and answers
# with ANSWER; `shown` collects what a button hands the page to show.
PRELUDE = """
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.dataset = {};
    this.disabled = false; this._text = null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  fire(type) { for (const fn of this.listeners[type] ?? []) fn({}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  set textContent(t) { this._text = t; this.children = []; }
  get textContent() { return this._text ?? this.children.map((c) => typeof c === "string" ? c : c.textContent).join(""); }
  all(pred) { return [...(pred(this) ? [this] : []), ...this.children.flatMap((c) => c.all ? c.all(pred) : [])]; }
}
globalThis.document = { createElement: (tag) => new Node(tag), createTextNode: (text) => ({ textContent: text }) };
const asked = [];
let ANSWER = {};
globalThis.fetch = async (url, options) => {
  asked.push(`${options?.method ?? "GET"} ${url}`);
  return { ok: true, status: 200, json: async () => ANSWER };
};
const settle = () => new Promise((r) => setTimeout(r, 0));
const text = (x) => (typeof x === "string" ? x : x.textContent);
let shown = null;
const done = (lines) => { shown = lines.map(text); };
"""


def run(body: str):
    script = f"""
        {PRELUDE}
        const m = await import({json.dumps((STATIC_DIR / "lib" / "ledger_research.js").as_uri())});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def stale_button(stale: dict, answer: dict | None = None, press: bool = False) -> dict:
    return run(f"""
        ANSWER = {json.dumps(answer or {})};
        const row = m.staleButton({json.dumps(stale)}, done);
        const [button] = row.all((n) => n.tag === "button");
        const before = {{ label: button.textContent, disabled: button.disabled, hint: row.children[1].textContent }};
        if ({json.dumps(press)}) {{ button.fire("click"); await settle(); }}
        console.log(JSON.stringify({{ ...before, asked, shown }}));
    """)


def test_the_button_counts_what_it_would_queue_and_says_so():
    out = stale_button({"due": ["AAPL", "KO", "MSFT"], "waiting": ["KO"]})
    assert (out["label"], out["disabled"]) == ("Research all stale (2)", False)
    assert out["hint"].startswith("2 watched listings have research that is missing, too old or behind a price move")


def test_nothing_stale_disables_the_button_with_a_hint():
    out = stale_button({"due": [], "waiting": []})
    assert (out["label"], out["disabled"]) == ("Research all stale (0)", True)
    assert out["hint"] == "Nothing to research: every watched listing's research is up to date."


def test_everything_stale_already_queued_disables_it_and_points_to_activity():
    out = stale_button({"due": ["AAPL"], "waiting": ["AAPL"]})
    assert out["disabled"] and out["hint"] == "The stale listing is already queued: see the Activity tab."
    out = stale_button({"due": ["AAPL", "KO"], "waiting": ["AAPL", "KO"]})
    assert out["hint"] == "All 2 stale listings are already queued: see the Activity tab."


def test_pressing_it_queues_them_and_says_how_many_pointing_to_activity():
    answer = {"queued": [{"subject": "AAPL"}, {"subject": "MSFT"}], "waiting": ["KO"], "refused": [["OLD.F", "gone"]]}
    out = stale_button({"due": ["AAPL", "KO", "MSFT"], "waiting": ["KO"]}, answer, press=True)
    assert out["asked"] == ["POST /api/ledgers/ai/research-stale"]
    assert out["shown"] == [
        "Queued research on 2 listings (AAPL, MSFT). They run one after another: follow or cancel them in the "
        "Activity tab.",
        "Already queued, so not added again: KO.",
        "Could not queue OLD.F: gone",
    ]


def test_a_decision_line_button_queues_that_listing():
    out = run("""
        ANSWER = { id: 7, state: "queued" };
        const button = m.researchButton("VOD.L", done);
        button.fire("click"); await settle();
        console.log(JSON.stringify({ asked, shown, cls: button.className }));
    """)
    assert out["asked"] == ["POST /api/jobs/research?symbol=VOD.L"]
    assert out["shown"] == ["Research on VOD.L is queued: follow it in the Activity tab."]
    assert out["cls"] == "small-btn"
