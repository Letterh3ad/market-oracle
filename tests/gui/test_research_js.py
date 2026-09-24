import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

# Just enough DOM for the Company page's research section, plus a fetch answering from ANSWERS by
# path; a list answer is a sequence, one item per request, the last repeating.
PRELUDE = """
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.dataset = {};
    this.disabled = false; this.isConnected = true; this._text = null; this.hidden = false; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  fire(type) { for (const fn of this.listeners[type] ?? []) fn({}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = null; }
  set textContent(t) { this._text = t; this.children = []; }
  get textContent() { return this._text ?? this.children.map((c) => typeof c === "string" ? c : c.textContent).join(""); }
  all(pred) { return [...(pred(this) ? [this] : []), ...this.children.flatMap((c) => c.all ? c.all(pred) : [])]; }
}
const banner = new Node("div");
globalThis.document = { createElement: (tag) => new Node(tag), createTextNode: (text) => ({ textContent: text }),
  getElementById: () => banner };
const asked = [];
let ANSWERS = {};
globalThis.fetch = async (url, options) => {
  asked.push(`${options?.method ?? "GET"} ${url}`);
  const found = ANSWERS[url.split("?")[0]] ?? {};
  const body = Array.isArray(found) && found.length > 1 ? found.shift() : Array.isArray(found) ? found[0] : found;
  return { ok: true, status: 200, json: async () => body };
};
const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const button = (root, text) => root.all((n) => n.tag === "button" && n.textContent.startsWith(text))[0];
const look = (b) => ({ label: b.textContent, disabled: b.disabled });
"""


def run(body: str):
    script = f"""
        {PRELUDE}
        const m = await import({json.dumps((STATIC_DIR / "lib" / "research.js").as_uri())});
        m.followEvery(100);
        ANSWERS = {{ "/api/companies/KO/research": {{ run: null, failed: null }},
          "/api/companies/KO/research/history": [[]], "/api/activity": {{ jobs: [], elsewhere: [] }} }};
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_pressing_research_shows_queued_then_researching_then_research_again():
    out = run("""
        ANSWERS["/api/jobs/research"] = { id: 5, subject: "KO", state: "queued" };
        ANSWERS["/api/activity/5"] = [{ id: 5, subject: "KO", state: "running", progress: "step 2" },
          { id: 5, subject: "KO", state: "done" }];
        const box = m.researchSection("KO");
        await settle();
        const run = button(box, "Research"), replay = button(box, "Replay");
        run.fire("click");
        const pressed = look(run);
        await settle(30);
        const queued = [look(run), look(replay)];
        await settle(120);
        const running = look(run);
        await settle(200);
        const posts = asked.filter((a) => a.startsWith("POST")).length;
        console.log(JSON.stringify({ pressed, queued, running, after: look(run), posts }));
    """)
    assert out["pressed"]["disabled"]
    assert out["queued"] == [{"label": "Queued", "disabled": True}, {"label": "Replay as of", "disabled": True}]
    assert out["running"] == {"label": "Researching...", "disabled": True}
    assert out["after"] == {"label": "Research", "disabled": False}
    assert out["posts"] == 1


def test_a_run_already_going_shows_on_the_button_when_the_page_opens():
    out = run("""
        ANSWERS["/api/activity"] = { jobs: [{ id: 9, kind: "research", subject: "KO", state: "queued" }], elsewhere: [] };
        ANSWERS["/api/activity/9"] = { id: 9, subject: "KO", state: "queued" };
        const box = m.researchSection("KO");
        await settle(5);
        const shown = look(button(box, "Queued"));
        box.isConnected = false;
        console.log(JSON.stringify(shown));
    """)
    assert out == {"label": "Queued", "disabled": True}


def test_the_picker_lists_the_profiles_and_a_picked_one_goes_with_the_run():
    out = run("""
        ANSWERS["/api/models"] = { profiles: [{ name: "default" }, { name: "big" }] };
        ANSWERS["/api/jobs/research"] = { id: 5, subject: "KO", state: "done" };
        const box = m.researchSection("KO");
        await settle();
        const [pick] = box.all((n) => n.tag === "select");
        const options = pick.children.map((o) => [o.attrs.value, o.textContent]);
        button(box, "Research").fire("click");
        await settle(10);
        pick.value = "big";
        button(box, "Research").fire("click");
        await settle(10);
        console.log(JSON.stringify({ options, posts: asked.filter((a) => a.startsWith("POST")) }));
    """)
    assert out["options"] == [["", "per slot"], ["default", "default"], ["big", "big"]]
    assert out["posts"] == ["POST /api/jobs/research?symbol=KO", "POST /api/jobs/research?symbol=KO&model=big"]
