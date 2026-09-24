import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

# A box whose height is its children's count x 100px, in a window that clamps its scroll to the
# page height, so blanking the box would drop the scroll the way a browser does.
PRELUDE = """
class Node {
  constructor(tag) { this.tag = tag; this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.style = {}; this.id = ""; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === "id") this.id = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener() {}
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; paints.push(nodes.map((n) => n.tag)); clamp(); }
  get offsetHeight() { return Math.max(this.children.length * 100, parseInt(this.style.minHeight || "0", 10)); }
  contains(n) { return n === this || this.children.some((c) => c.contains?.(n)); }
  querySelectorAll() { return this.children.flatMap((c) => (c.children ? [c, ...c.querySelectorAll()] : [])); }
  focus(opts) { document.activeElement = this; focusOpts = opts; }
}
const paints = [];
let focusOpts = null;
globalThis.document = { createElement: (tag) => new Node(tag), createTextNode: (text) => ({ textContent: text }),
  activeElement: null };
const box = new Node("div");
const clamp = () => { window.scrollY = Math.min(window.scrollY, Math.max(0, box.offsetHeight - 500)); };
globalThis.window = { scrollX: 0, scrollY: 0, scrollTo(x, y) { this.scrollX = x; this.scrollY = y; clamp(); } };
const rows = (n) => Array.from({ length: n }, () => new Node("p"));
"""


def run(body: str):
    script = f"""
        {PRELUDE}
        const {{ repaint }} = await import({json.dumps((STATIC_DIR / "lib" / "repaint.js").as_uri())});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_a_repaint_keeps_the_scroll_where_it_was():
    out = run("""
        box.replaceChildren(...rows(30)); window.scrollY = 1800;
        repaint(box, rows(30));
        console.log(JSON.stringify({ y: window.scrollY, count: box.children.length }));
    """)
    assert out == {"y": 1800, "count": 30}


def test_a_repaint_swaps_in_one_step_never_through_a_blank_box():
    out = run("""
        box.replaceChildren(...rows(3)); paints.length = 0;
        repaint(box, rows(4));
        console.log(JSON.stringify(paints));
    """)
    assert out == [["p", "p", "p", "p"]]


def test_what_after_draws_counts_before_the_scroll_is_put_back():
    out = run("""
        box.replaceChildren(...rows(30)); window.scrollY = 2400;
        repaint(box, rows(10), () => box.append(...rows(20)));
        console.log(JSON.stringify(window.scrollY));
    """)
    assert out == 2400


def test_the_old_height_holds_until_what_after_returns_settles():
    out = run("""
        box.replaceChildren(...rows(30)); window.scrollY = 2400;
        let finish;
        repaint(box, rows(2), () => new Promise((r) => { finish = r; }));
        const held = { y: window.scrollY, min: box.style.minHeight };
        finish(); await new Promise((r) => setTimeout(r, 0));
        console.log(JSON.stringify({ held, after: box.style.minHeight }));
    """)
    assert out == {"held": {"y": 2400, "min": "3000px"}, "after": ""}


def test_focus_moves_to_the_new_control_with_the_same_label():
    out = run("""
        const old = new Node("input"); old.setAttribute("aria-label", "Tag KO");
        box.replaceChildren(new Node("p"), old); old.focus();
        const fresh = new Node("input"); fresh.setAttribute("aria-label", "Tag KO");
        const other = new Node("input"); other.setAttribute("aria-label", "Tag AAPL");
        repaint(box, [other, fresh]);
        console.log(JSON.stringify({ same: document.activeElement === fresh, focusOpts }));
    """)
    assert out == {"same": True, "focusOpts": {"preventScroll": True}}
