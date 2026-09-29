import json
import shutil
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

# Just enough DOM for el() and repaint(), plus a fetch that records each request and answers
# from ANSWERS by method and path (the part before "?"); a {status, error} answer fails.
PRELUDE = """
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.dataset = {};
    this.style = {}; this.offsetHeight = 0; this.disabled = false; this.isConnected = true; this._value = "";
    this._text = null; this.className = ""; }
  // A select's value is its selected option's, as in a browser, until one is set.
  get value() {
    if (this.tag !== "select" || this._value) return this._value;
    return (this.children.find((o) => o.attrs.selected != null) ?? this.children[0])?.attrs.value ?? "";
  }
  set value(v) { this._value = v; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === "value") this.value = v; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  fire(type) { for (const fn of this.listeners[type] ?? []) fn({ target: this }); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = null; }
  contains(n) { return n === this || this.children.some((c) => c.contains?.(n)); }
  set textContent(t) { this._text = t; this.children = []; }
  get textContent() { return this._text ?? this.children.map((c) => typeof c === "string" ? c : c.textContent).join(""); }
  all(pred) { return [...(pred(this) ? [this] : []), ...this.children.flatMap((c) => c.all ? c.all(pred) : [])]; }
}
globalThis.document = { createElement: (tag) => new Node(tag), createTextNode: (text) => ({ textContent: text }),
  activeElement: null };
globalThis.window = { scrollX: 0, scrollY: 0, scrollTo() {} };
const asked = [];
const RUN = { id: 7, ran_at: "2026-09-24T20:00:00Z", base_currency: "GBP", watchlist: "main", start: "2025-03-03",
  end: "2026-09-22", slippage_bps: 10, commission: 0, listings: ["AAPL", "MSFT"],
  lines: [
    { name: "strategy", caption: "Strategy: the fixed rules on technicals-only leans.", return: 0.05, max_fall: -0.1,
      trades: 2, costs: 3.5, points: [["2025-03-03", 0], ["2026-09-22", 0.05]] },
    { name: "buy_and_hold", caption: "Buy and hold: equal amounts of the 2 listings.", return: 0.2, max_fall: -0.2,
      trades: 0, costs: 0, points: [["2025-03-03", 0], ["2026-09-22", 0.2]] }],
  trades: [{ day: "2025-03-04", symbol: "AAPL", side: "buy", shares: 3, price: 150, currency: "USD", cash: -360,
    costs: 0.4, reason: "buy lean at 62%" }],
  notes: ["The main list is today's (survivorship bias).", "Each lean is at most 62.5%."] };
const MOMENTUM = { name: "momentum", label: "Momentum", caption: "Momentum: the top 10.", return: 0.1, max_fall: -0.1,
  trades: 4, costs: 2 };
const REGIME = { name: "regime", label: "Regime", caption: "Regime: trend, 12-1 momentum; no AI verdicts.",
  return: -0.03, max_fall: -0.15, trades: 9, costs: 4, points: [["2024-01-02", 0], ["2026-09-22", -0.03]] };
const OLDER = { ...RUN, id: 6, start: "2024-01-02",
  lines: [...RUN.lines, MOMENTUM, REGIME].map((l) => ({ ...l, points: undefined })) };
let ANSWERS = {
  "GET /api/watchlists": [{ name: "main" }, { name: "tech" }],
  "GET /api/backtests/span": { watchlist: "main", first: "2023-03-01", last: "2026-09-22", base_currency: "GBP" },
  "GET /api/backtests": { runs: [RUN, OLDER], keep: 10 },
  "GET /api/backtests/7": RUN,
  "GET /api/backtests/6": { ...RUN, id: 6, start: "2024-01-02", lines: [...RUN.lines, { ...MOMENTUM,
    points: [["2024-01-02", 0], ["2026-09-22", 0.1]] }, REGIME] },
  "GET /api/activity": { jobs: [], elsewhere: [] },
  "POST /api/jobs/backtest": { id: 1, kind: "backtest", state: "done", result: RUN },
};
globalThis.fetch = async (url, options) => {
  const key = `${options?.method ?? "GET"} ${url.split("?")[0]}`;
  asked.push(`${options?.method ?? "GET"} ${url}`);
  const body = ANSWERS[key] ?? {};
  if (body.status) return { ok: false, status: body.status, statusText: "", json: async () => ({ error: body.error }) };
  return { ok: true, status: 200, json: async () => body };
};
const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));
const find = (root, pred) => root.all(pred)[0];
const labelled = (root, label) => find(root, (n) => n.attrs?.["aria-label"] === label);
const button = (root, text) => find(root, (n) => n.tag === "button" && n.textContent === text);
"""


def run(body: str):
    script = f"""
        {PRELUDE}
        const {{ backtestSection }} = await import({json.dumps((STATIC_DIR / "lib" / "backtest.js").as_uri())});
        const section = backtestSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_form_starts_on_the_stored_days_leaving_a_year_to_warm_up():
    shown = run("""
        section.start(); await settle();
        console.log(JSON.stringify([labelled(node, "Backtest from").value, labelled(node, "Backtest to").value,
          labelled(node, "Backtest to").attrs.max, node.textContent]));
    """)
    start, end, latest, text = shown
    assert (start, end, latest) == ("2024-03-01", "2026-09-22", "2026-09-22")
    assert "Prices are stored for the main list from 2023-03-01 to 2026-09-22" in text
    assert "defaults to the last 5 years at most" in text


def test_the_default_window_is_capped_at_five_years_on_a_long_lived_list():
    shown = run("""
        ANSWERS["GET /api/backtests/span"] = { watchlist: "main", first: "2015-01-01", last: "2026-09-22",
          base_currency: "GBP" };
        section.start(); await settle();
        console.log(JSON.stringify([labelled(node, "Backtest from").value, labelled(node, "Backtest to").value]));
    """)
    start, end = shown
    assert (start, end) == ("2021-09-22", "2026-09-22")


def test_the_newest_kept_run_shows_with_its_table_notes_and_trades():
    text = run("""
        section.start(); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "The main list, 2025-03-03 to 2026-09-22" in text
    assert "+5.00%" in text and "+20.00%" in text and "Buy and hold" in text
    assert "survivorship bias" in text and "62.5%" in text
    assert "Trades (1)" in text and "buy lean at 62%" in text
    assert "not a forecast" in text


def test_run_queues_the_job_with_the_form_and_shows_its_result():
    shown = run("""
        section.start(); await settle();
        labelled(node, "Backtest from").value = "2025-03-03";
        labelled(node, "Slippage in basis points").value = "5";
        labelled(node, "Commission per trade").value = "1.5";
        asked.length = 0;
        button(node, "Run backtest").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    asked, text = shown
    assert asked[0] == ("POST /api/jobs/backtest?watchlist=main&start=2025-03-03&end=2026-09-22"
                        "&slippage_bps=5&commission=1.5")
    assert "GET /api/backtests/7" in asked
    assert "The main list, 2025-03-03 to 2026-09-22" in text


def test_the_regime_ai_line_is_asked_for_only_when_ticked_and_its_progress_shows():
    shown = run("""
        ANSWERS["POST /api/jobs/backtest"] = { id: 1, kind: "backtest", state: "running",
          label: "Backtest main, 2025-03-03 to 2026-09-22, with regime AI",
          progress: "Regime AI tilt at 2025-06-02 (2025-03-03 to 2026-09-22): 60 model calls, 0 from cache, 0 failed" };
        ANSWERS["GET /api/activity/1"] = { id: 1, kind: "backtest", state: "done", result: RUN };
        section.start(); await settle();
        labelled(node, "Backtest from").value = "2025-03-03";
        labelled(node, "With the regime AI line").checked = true;
        asked.length = 0;
        button(node, "Run backtest").fire("click"); await settle();
        const during = node.textContent;
        await settle(1600);
        console.log(JSON.stringify([asked, during]));
    """)
    asked, during = shown
    assert asked[0] == ("POST /api/jobs/backtest?watchlist=main&start=2025-03-03&end=2026-09-22"
                        "&slippage_bps=10&commission=0&regime_ai=true")
    assert "asks the model for every day" in during and "Regime AI tilt at 2025-06-02" in during
    assert "Add the regime AI line" in during


def test_a_refusal_shows_under_the_form():
    text = run("""
        ANSWERS["POST /api/jobs/backtest"] = { status: 400, error: "prices for the main list are stored from X" };
        section.start(); await settle();
        button(node, "Run backtest").fire("click"); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "prices for the main list are stored from X" in text


def test_missing_days_are_asked_for_before_anything_is_sent():
    shown = run("""
        section.start(); await settle();
        labelled(node, "Backtest from").value = "";
        asked.length = 0;
        button(node, "Run backtest").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    asked, text = shown
    assert asked == [] and "Pick the first and last day" in text


def test_an_earlier_run_can_be_picked_to_compare():
    shown = run("""
        section.start(); await settle();
        const picker = labelled(node, "Kept backtests");
        const options = picker.children.map((o) => o.textContent);
        picker.value = "6"; picker.fire("change"); await settle();
        console.log(JSON.stringify([options, asked.at(-1), node.textContent]));
    """)
    options, last, text = shown
    assert options[0].startswith("main, 2025-03-03 to 2026-09-22: strategy +5.00%, buy and hold +20.00%")
    assert options[1].startswith("main, 2024-01-02 to 2026-09-22: strategy +5.00%, buy and hold +20.00%, momentum "
                                 "+10.00%, regime -3.00%")
    assert len(options) == 2 and last == "GET /api/backtests/6"
    assert "The main list, 2024-01-02 to 2026-09-22" in text
    assert "Regime" in text and "-3.00%" in text and "no AI verdicts" in text


def test_a_backtest_already_running_is_followed_to_its_result():
    shown = run("""
        ANSWERS["GET /api/backtests"] = { runs: [], keep: 10 };
        ANSWERS["GET /api/activity"] = { jobs: [{ id: 3, kind: "backtest", state: "running" }], elsewhere: [] };
        ANSWERS["GET /api/activity/3"] = { id: 3, kind: "backtest", state: "done", result: RUN };
        section.start(); await settle();
        const during = node.textContent;
        ANSWERS["GET /api/backtests"] = { runs: [RUN], keep: 10 };
        await settle(1600);
        console.log(JSON.stringify([during, node.textContent]));
    """)
    during, after = shown
    assert "A backtest is running" in during and "No backtest kept yet" in during
    assert "The main list, 2025-03-03 to 2026-09-22" in after
