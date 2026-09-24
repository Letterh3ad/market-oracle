import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

# Just enough DOM for el() and the Finished section, plus a fetch that answers every
# finished request with 50 matches (3 pages) unless the query is "zzz", and records the URLs.
PRELUDE = """
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.dataset = {};
    this.isConnected = true; this.value = ""; this.open = false; this._text = null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  fire(type) { for (const fn of this.listeners[type] ?? []) fn({}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  set textContent(t) { this._text = t; this.children = []; }
  get textContent() { return this._text ?? this.children.map((c) => c.textContent).join(""); }
  all(pred) { return [...(pred(this) ? [this] : []), ...this.children.flatMap((c) => c.all ? c.all(pred) : [])]; }
}
globalThis.document = { createElement: (tag) => new Node(tag),
  createTextNode: (text) => ({ textContent: text }) };
const asked = [];
globalThis.fetch = async (url) => {
  asked.push(url);
  const q = new URL(url, "http://x").searchParams.get("q") ?? "";
  const page = Number(new URL(url, "http://x").searchParams.get("page") ?? 1);
  const none = q === "zzz";
  const body = url.includes("/count") ? { total: none ? 0 : 50 }
    : { items: none ? [] : [{ id: page, kind: "prices", label: `Job on page ${page}`, state: "done" }],
        page, pages: none ? 1 : 3, total: none ? 0 : 50 };
  return { ok: true, status: 200, json: async () => body };
};
const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const button = (root, text) => root.all((n) => n.tag === "button" && n.textContent === text)[0];
"""


def run(body: str):
    """Runs `body` against the Finished section (`section`) and returns what it logs as JSON."""
    script = f"""
        {PRELUDE}
        const {{ finishedSection }} = await import({json.dumps((STATIC_DIR / "tabs" / "activity.js").as_uri())});
        const section = finishedSection();
        const search = section.node.all((n) => n.tag === "input")[0];
        {body}
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def test_the_poll_leaves_a_later_page_alone():
    asked = run("""
        section.node.open = true;
        section.refresh(); await settle();
        button(section.node, "Next").fire("click"); await settle();
        asked.length = 0;
        section.refresh(); await settle();
        console.log(JSON.stringify(asked));
    """)
    assert not any("/finished?" in url for url in asked)


def test_the_poll_refreshes_the_first_page():
    asked = run("""
        section.node.open = true;
        section.refresh(); await settle();
        asked.length = 0;
        section.refresh(); await settle();
        console.log(JSON.stringify(asked));
    """)
    assert asked == ["/api/activity/finished?page=1&q="]


def test_the_poll_uses_the_last_committed_search_not_the_half_typed_one():
    asked = run("""
        section.node.open = true;
        search.value = "msf"; search.fire("input");
        section.refresh(); await settle();
        const early = [...asked];
        await settle(300);
        console.log(JSON.stringify([early, asked.at(-1)]));
    """)
    early, committed = asked
    assert early == ["/api/activity/finished?page=1&q="]
    assert committed == "/api/activity/finished?page=1&q=msf"


def test_a_closed_section_still_shows_its_count():
    shown = run("""
        section.node.open = false;
        section.refresh(); await settle();
        console.log(JSON.stringify([asked, section.node.textContent]));
    """)
    asked, text = shown
    assert asked == ["/api/activity/finished/count?q="] and "(50)" in text


def test_a_search_matching_nothing_says_so():
    text = run("""
        section.node.open = true;
        search.value = " zzz "; search.fire("input");
        await settle(300);
        console.log(JSON.stringify(section.node.textContent));
    """)
    assert 'No finished job matches "zzz".' in text
