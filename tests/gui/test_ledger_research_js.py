import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

# Just enough DOM for el(), plus a fetch that records each request and answers from ANSWERS by
# path (the part before "?"); `shown` collects what a button hands the page to show.
PRELUDE = """
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.dataset = {};
    this.disabled = false; this.isConnected = true; this._text = null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  fire(type) { for (const fn of this.listeners[type] ?? []) fn({}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = null; }
  set textContent(t) { this._text = t; this.children = []; }
  get textContent() { return this._text ?? this.children.map((c) => typeof c === "string" ? c : c.textContent).join(""); }
  all(pred) { return [...(pred(this) ? [this] : []), ...this.children.flatMap((c) => c.all ? c.all(pred) : [])]; }
}
globalThis.document = { createElement: (tag) => new Node(tag), createTextNode: (text) => ({ textContent: text }) };
const asked = [];
let ANSWERS = {};
globalThis.fetch = async (url, options) => {
  asked.push(`${options?.method ?? "GET"} ${url}`);
  const body = ANSWERS[url.split("?")[0]] ?? {};
  return { ok: true, status: 200, json: async () => body };
};
const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const text = (x) => (typeof x === "string" ? x : x.textContent);
let shown = null;
const done = (lines) => { shown = lines.map(text); };
const buttonOf = (node) => node.all((n) => n.tag === "button")[0] ?? null;
const look = (node) => { const b = buttonOf(node);
  return { label: b?.textContent ?? null, disabled: b?.disabled ?? null, text: node.textContent }; };
const activity = (...jobs) => ({ jobs: jobs.map(([subject, state, progress]) =>
  ({ id: 1, kind: "research", subject, state, progress: progress ?? null })), elsewhere: [] });
"""


def run(body: str):
    script = f"""
        {PRELUDE}
        const m = await import({json.dumps((STATIC_DIR / "lib" / "ledger_research.js").as_uri())});
        const live = m.researchLive();
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def stale_button(stale: dict, answer: dict | None = None, press: bool = False) -> dict:
    return run(f"""
        ANSWERS = {{ "/api/ledgers/ai/research-stale": {json.dumps(answer or {})} }};
        const row = live.staleButton({json.dumps(stale)}, done);
        const button = buttonOf(row);
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


def test_pressing_it_flips_the_queued_lines_and_itself_in_place():
    out = run("""
        ANSWERS = { "/api/ledgers/ai/research-stale": { queued: [{ subject: "AAPL", state: "queued" }], waiting: [],
          refused: [] } };
        const row = live.staleButton({ due: ["AAPL"], waiting: [] }, done);
        const line = live.researchButton("AAPL", done);
        buttonOf(row).fire("click"); await settle();
        console.log(JSON.stringify({ stale: look(row), line: look(line), asked }));
    """)
    assert out["stale"]["label"] == "Research all stale (0)" and out["stale"]["disabled"]
    assert (out["line"]["label"], out["line"]["disabled"]) == ("Queued", True)
    assert out["asked"] == ["POST /api/ledgers/ai/research-stale"]


def test_a_decision_line_button_queues_that_listing_and_flips_without_a_reload():
    out = run("""
        ANSWERS = { "/api/jobs/research": { id: 7, subject: "VOD.L", state: "queued" } };
        const line = live.researchButton("VOD.L", done);
        buttonOf(line).fire("click"); await settle();
        console.log(JSON.stringify({ asked, shown, cls: buttonOf(line).className, ...look(line) }));
    """)
    assert out["asked"] == ["POST /api/jobs/research?symbol=VOD.L"]
    assert out["shown"] == ["Research on VOD.L is queued: follow it in the Activity tab."]
    assert (out["label"], out["disabled"], out["cls"]) == ("Queued", True, "small-btn")


def test_the_poll_shows_queued_and_running_research_with_its_progress():
    out = run("""
        ANSWERS = { "/api/activity": activity(["KO", "queued"], ["VOD.L", "running", "reading 3 of 8"]) };
        const ko = live.researchButton("KO", done), vod = live.researchButton("VOD.L", done);
        const idle = live.researchButton("MSFT", done);
        await live.poll();
        console.log(JSON.stringify({ ko: look(ko), vod: look(vod), idle: look(idle), asked }));
    """)
    assert out["ko"] == {"label": "Queued", "disabled": True, "text": "Queued"}
    assert out["vod"]["label"] == "Researching..." and out["vod"]["disabled"]
    assert "reading 3 of 8" in out["vod"]["text"]
    assert out["idle"] == {"label": "Research", "disabled": False, "text": "Research"}
    assert out["asked"] == ["GET /api/activity"]


def test_a_line_made_after_the_poll_starts_in_the_live_state():
    out = run("""
        ANSWERS = { "/api/activity": activity(["KO", "running"]) };
        await live.poll();
        console.log(JSON.stringify(look(live.researchButton("KO", done))));
    """)
    assert out["label"] == "Researching..." and out["disabled"]


def test_finished_research_no_longer_due_drops_the_button_and_the_count():
    out = run("""
        ANSWERS = { "/api/activity": activity(["KO", "running"], ["VOD.L", "queued"]) };
        const row = live.staleButton({ due: ["KO", "VOD.L", "MSFT"], waiting: ["KO", "VOD.L"] }, done);
        const ko = live.researchButton("KO", done), vod = live.researchButton("VOD.L", done);
        await live.poll();
        ANSWERS = { "/api/activity": activity(), "/api/ledgers/ai/stale": { due: ["VOD.L", "MSFT"], waiting: [] } };
        asked.length = 0;
        await live.poll();
        console.log(JSON.stringify({ ko: look(ko), vod: look(vod), stale: look(row), asked }));
    """)
    assert out["ko"]["label"] is None and "Researched" in out["ko"]["text"]
    assert (out["vod"]["label"], out["vod"]["disabled"]) == ("Research", False)
    assert out["stale"]["label"] == "Research all stale (2)"
    assert out["asked"] == ["GET /api/activity", "GET /api/ledgers/ai/stale"]


def test_a_press_the_poll_did_not_see_yet_stays_queued():
    out = run("""
        let answerPoll;
        const slow = globalThis.fetch;
        globalThis.fetch = (url, options) => (url === "/api/activity"
          ? new Promise((r) => { answerPoll = () => r({ ok: true, status: 200, json: async () => activity() }); })
          : slow(url, options));
        ANSWERS = { "/api/jobs/research": { id: 7, subject: "KO", state: "queued" } };
        const ko = live.researchButton("KO", done);
        const polling = live.poll();
        buttonOf(ko).fire("click"); await settle();
        answerPoll(); await polling;
        console.log(JSON.stringify(look(ko)));
    """)
    assert (out["label"], out["disabled"]) == ("Queued", True)


def test_the_poll_stops_once_the_tab_is_gone():
    out = run("""
        ANSWERS = { "/api/activity": activity() };
        const tab = new Node("div");
        live.start(tab, 10);
        await settle(35);
        const polls = () => asked.filter((a) => a === "GET /api/activity").length;
        const running = polls();
        tab.isConnected = false;
        await settle(25);
        const stopped = polls();
        await settle(40);
        console.log(JSON.stringify({ running, stopped, later: polls() }));
    """)
    assert out["running"] >= 2
    assert out["later"] == out["stopped"]
