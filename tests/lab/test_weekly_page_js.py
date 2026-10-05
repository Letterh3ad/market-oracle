"""The weekly page's script (research-lab 08) run in node against a fake page and a fake artifact runtime: it
renders read-only without the database, subscribes once, writes one mark at a time per candidate only on a click
or a changed note, and turns read-only when the store refuses a write."""

import json
import subprocess

import pytest

from oracle.lab.weekly_page import SCRIPT
from tests.gui.test_backtest_js import NODE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's script")

FAKES = """
class El {
  constructor() { this.attrs = {}; this.dataset = {}; this.disabled = true; this.listeners = {}; this.value = "";
    this.textContent = ""; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(t, f) { (this.listeners[t] ??= []).push(f); }
  fire(t) { for (const f of this.listeners[t] ?? []) f({ target: this }); }
}
function card(key) {
  const c = new El();
  c.dataset.key = key;
  c.buttons = ["go", "drop", "dig"].map((m) => { const b = new El(); b.dataset.mark = m; return b; });
  c.ta = new El();
  c.saved = new El();
  c.querySelectorAll = () => c.buttons;
  c.querySelector = (sel) => sel === "textarea" ? c.ta : c.saved;
  return c;
}
const cards = [card("C-07"), card("C-03")];
const state = new El();
const root = { getElementById: () => state, querySelectorAll: () => cards, activeElement: null };
const events = [];
let snap = null, subs = 0, refuse = null, canWrite = true;
const db = { collection: (name) => ({
  onSnapshot: (next, error) => { subs += 1; snap = next; return () => {}; },
  doc: (id) => ({ set: async (body) => {
    events.push(["start", name, id, body]);
    await new Promise((r) => setTimeout(r, 5));
    events.push(["end", id]);
    if (refuse) throw refuse;
  } }) }) };
const user = { id: async () => "u_1", can: async () => canWrite };
const claude = { use: async (n) => n === "db" ? db : n === "user" ? user : null };
const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const disabled = () => cards.flatMap((c) => [...c.buttons, c.ta]).map((x) => x.disabled);
"""


def run(body: str):
    script = SCRIPT.split("weeklyMarks(document, window.claude);")[0]
    out = subprocess.run([NODE, "--input-type=module", "-e", FAKES + script + body], capture_output=True, text=True,
                         encoding="utf-8", check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("runtime", ["undefined", "{ use: async () => null }", "{ use: async () => { throw 1; } }"])
def test_without_the_database_the_page_is_read_only(runtime):
    off, text = run(f"""
        await weeklyMarks(root, {runtime});
        console.log(JSON.stringify([disabled(), state.textContent]));
    """)
    assert all(off) and text.startswith("Read-only here")


def test_saved_marks_show_on_their_card_from_one_subscription():
    got = run("""
        await weeklyMarks(root, claude);
        snap({ docs: [{ id: "C-07", exists: true, data: () => ({ id: "C-07", mark: "go", note: "<b>keen</b>",
          by: "u_1", at: "2026-10-05T07:30:00.000Z" }) }, { id: "zzz", exists: false, data: () => undefined }] });
        const c = cards[0];
        console.log(JSON.stringify([subs, disabled(), c.buttons.map((b) => b.attrs["aria-pressed"]), c.ta.value,
          c.saved.textContent, cards[1].saved.textContent]));
    """)
    subs, off, pressed, note, saved, other = got
    assert subs == 1 and not any(off)
    assert pressed == ["true", "false", "false"] and note == "<b>keen</b>"
    assert saved == "Marked go 2026-10-05 07:30 UTC by you" and other == "Not marked yet."


def test_a_click_and_a_changed_note_write_one_document_at_a_time():
    events = run("""
        await weeklyMarks(root, claude);
        const c = cards[0];
        c.buttons[1].fire("click");
        c.ta.value = "read the paper first";
        c.ta.fire("change");
        c.ta.fire("change");
        await settle(40);
        console.log(JSON.stringify(events));
    """)
    assert [e[0] for e in events] == ["start", "end", "start", "end"], "the second write waits for the first"
    first, second = events[0][3], events[2][3]
    assert events[0][1:3] == ["marks", "C-07"]
    assert {k: first[k] for k in ("id", "mark", "note", "by")} == {"id": "C-07", "mark": "drop", "note": "", "by": "u_1"}
    assert {k: second[k] for k in ("mark", "note")} == {"mark": "drop", "note": "read the paper first"}
    assert first["at"].endswith("Z") and "T" in first["at"]


def test_a_viewer_who_cannot_write_gets_no_controls():
    off, text, events = run("""
        canWrite = false;
        await weeklyMarks(root, claude);
        cards[0].buttons[0].fire("click");
        await settle(20);
        console.log(JSON.stringify([disabled(), state.textContent, events]));
    """)
    assert all(off) and text.startswith("Read-only for you") and events == []


def test_a_refused_write_turns_the_page_read_only():
    off, text = run("""
        refuse = { code: "invalid_argument", message: "no" };
        await weeklyMarks(root, claude);
        cards[1].buttons[2].fire("click");
        await settle(30);
        console.log(JSON.stringify([disabled(), state.textContent]));
    """)
    assert all(off) and text == "Read-only: this page could not save your mark."
