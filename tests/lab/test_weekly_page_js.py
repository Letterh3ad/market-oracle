"""The weekly page's script (research-lab 08) run in node against a fake page and a fake artifact runtime: it
renders read-only without the database, enables its controls on the first snapshot, writes one mark at a time
per candidate only on a click or a changed note, shows only what the store confirmed (a refused write reverts
with a "Not saved" line), retries `unavailable` once, and turns read-only when the store refuses it."""

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
let snap = null, snapErr = null, subs = 0, refusals = [], canWrite = true;
const db = { collection: (name) => ({
  onSnapshot: (next, error) => { subs += 1; snap = next; snapErr = error; return () => {}; },
  doc: (id) => ({ set: async (body) => {
    events.push(["start", name, id, body]);
    await new Promise((r) => setTimeout(r, 5));
    events.push(["end", id]);
    const refusal = refusals.shift();
    if (refusal) throw refusal;
  } }) }) };
const user = { id: async () => "u_1", can: async () => canWrite };
const claude = { use: async (n) => n === "db" ? db : n === "user" ? user : null };
const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const disabled = () => cards.flatMap((c) => [...c.buttons, c.ta]).map((x) => x.disabled);
const doc = (id, body) => ({ id, exists: true, data: () => body });
const SAVED = { id: "C-07", mark: "go", note: "<b>keen</b>", by: "u_1", at: "2026-10-05T07:30:00.000Z",
  mark_at: "2026-10-05T07:30:00.000Z" };
const start = async () => { await weeklyMarks(root, claude); snap({ docs: [doc("C-07", SAVED)] }); };
const pressed = (c) => c.buttons.map((b) => b.attrs["aria-pressed"]);
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


def test_controls_wait_for_the_first_snapshot():
    before, after, events = run("""
        await weeklyMarks(root, claude);
        const before = disabled();
        cards[0].buttons[0].fire("click");
        await settle(20);
        snap({ docs: [] });
        console.log(JSON.stringify([before, disabled(), events]));
    """)
    assert all(before) and not any(after) and events == [], "a click before the saved note loads would erase it"


def test_saved_marks_show_on_their_card_from_one_subscription():
    got = run("""
        await weeklyMarks(root, claude);
        snap({ docs: [doc("C-07", SAVED), { id: "zzz", exists: false, data: () => undefined }] });
        const c = cards[0];
        console.log(JSON.stringify([subs, pressed(c), c.ta.value, c.saved.textContent, cards[1].saved.textContent]));
    """)
    subs, marks, note, saved, other = got
    assert subs == 1 and marks == ["true", "false", "false"] and note == "<b>keen</b>"
    assert saved == "Marked go 2026-10-05 07:30 UTC by you" and other == "Not marked yet."


def test_a_deleted_document_clears_its_card():
    got = run("""
        await start();
        snap({ docs: [] });
        console.log(JSON.stringify([pressed(cards[0]), cards[0].ta.value, cards[0].saved.textContent]));
    """)
    assert got == [["false", "false", "false"], "", "Not marked yet."]


def test_a_click_and_a_changed_note_write_one_document_at_a_time():
    events = run("""
        await start();
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
    assert {k: first[k] for k in ("id", "mark", "note", "by")} == {
        "id": "C-07", "mark": "drop", "note": "<b>keen</b>", "by": "u_1"}
    assert first["at"].endswith("Z") and first["mark_at"] == first["at"]
    assert {k: second[k] for k in ("mark", "note")} == {"mark": "drop", "note": "read the paper first"}
    assert second["mark_at"] == first["mark_at"] and second["at"] >= first["at"], "a note keeps the mark's time"


def test_a_note_alone_keeps_the_saved_marks_time():
    body = run("""
        await start();
        cards[0].ta.value = "more";
        cards[0].ta.fire("change");
        await settle(20);
        console.log(JSON.stringify(events[0][3]));
    """)
    assert body["mark"] == "go" and body["note"] == "more" and body["mark_at"] == "2026-10-05T07:30:00.000Z"


def test_a_failed_write_reverts_the_card_and_says_not_saved():
    pending, after, text = run("""
        refusals = [{ code: "quota_exceeded", message: "full" }];
        await start();
        cards[0].buttons[2].fire("click");
        const pending = [pressed(cards[0]), cards[0].saved.textContent];
        await settle(30);
        console.log(JSON.stringify([pending, [pressed(cards[0]), cards[0].saved.textContent], state.textContent]))
    """)
    assert pending[0] == ["false", "false", "true"] and pending[1].startswith("Saving: Marked dig ")
    assert after == [["true", "false", "false"], "Not saved: the page's database is full."]
    assert text == "Not saved: the page's database is full."


def test_unavailable_is_retried_once():
    got = run("""
        refusals = [{ code: "unavailable", message: "blip" }];
        await start();
        cards[1].buttons[0].fire("click");
        await settle(1000);
        console.log(JSON.stringify([events.filter((e) => e[0] === "start").length, pressed(cards[1]),
          cards[1].saved.textContent.startsWith("Marked go")]));
    """)
    assert got == [2, ["true", "false", "false"], True]


def test_a_viewer_who_cannot_write_gets_no_controls():
    off, text, events = run("""
        canWrite = false;
        await start();
        cards[0].buttons[0].fire("click");
        await settle(20);
        console.log(JSON.stringify([disabled(), state.textContent, events]));
    """)
    assert all(off) and text.startswith("Read-only for you") and events == []


def test_a_refused_write_turns_the_page_read_only():
    off, text, saved = run("""
        refusals = [{ code: "invalid_argument", message: "no" }];
        await start();
        cards[1].buttons[2].fire("click");
        await settle(30);
        console.log(JSON.stringify([disabled(), state.textContent, cards[1].saved.textContent]));
    """)
    assert all(off) and text == "Read-only: this page could not save your mark."
    assert saved == "Not saved: this page cannot save marks for you."


@pytest.mark.parametrize("code", ["revoked", "invalid_argument"])
def test_a_dead_subscription_turns_the_page_read_only(code):
    off, text = run(f"""
        await start();
        snapErr({{ code: "{code}", message: "gone" }});
        console.log(JSON.stringify([disabled(), state.textContent]));
    """)
    assert all(off) and text.startswith("Read-only")
