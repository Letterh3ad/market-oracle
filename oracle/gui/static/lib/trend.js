// The pre-registered trend overlay test (boring-plan 04): the 10-month moving average rule on the
// S&P 500 total-return index against buy and hold, choose 1988-2007 (reported only) and confirm
// 2008 on (the verdict). A card on the Plan tab; `changed` hears when a run finishes.
import { api } from "./api.js";
import { linesChart } from "./chart.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { pct } from "./fmt.js";
import { followJob } from "./ui.js";

const fixed1 = (x) => `${(x * 100).toFixed(1)}%`;
const whole = (x) => `${Math.round(x * 100)}%`;
const LINE_STYLE = { trend_overlay: { colour: "--accent" }, held: { colour: "--text-dim", dashed: true } };
const TITLES = { choose: "Choose 1988-2007 (reported only)", confirm: "Confirm 2008 on (judged)" };

function windowTable(w) {
  const rows = w.lines.map((l) => el("tr", null,
    el("td", null, l.name === "held" ? "Buy and hold" : "Trend overlay"),
    el("td", { class: "num" }, pct(l.return)), el("td", { class: "num" }, pct(l.cagr)),
    el("td", { class: "num down" }, fixed1(l.max_fall)), el("td", { class: "num" }, String(l.trades)),
    el("td", { class: "num" }, whole(l.cash_share))));
  return table([["Line"], ["Return", "return", 1], ["Yearly", null, 1], ["Worst fall", "max_fall", 1],
    ["Switches", null, 1], ["In cash", null, 1]], rows, "No sessions.");
}

function crashTable(crashes) {
  const rows = crashes.map((c) => el("tr", null, el("td", null, c.name), el("td", { class: "small" }, `${c.start} to ${c.end}`),
    el("td", { class: "num" }, pct(c.trend_return)), el("td", { class: "num" }, pct(c.held_return)),
    el("td", { class: "num down" }, fixed1(c.trend_fall)), el("td", { class: "num down" }, fixed1(c.held_fall)),
    el("td", { class: "num" }, whole(c.cash_share))));
  return table([["Crash"], ["Span"], ["Overlay", null, 1], ["Buy and hold", null, 1], ["Overlay fall", null, 1],
    ["B&H fall", null, 1], ["In cash", null, 1]], rows, "No crash span inside the confirm window yet.");
}

function windowPart(w, title, charts) {
  const holder = el("div", { class: "compare-chart" });
  charts.push(() => linesChart(holder, w.lines.filter((l) => l.points.length > 1).map((l) => ({
    ...LINE_STYLE[l.name], title: l.name === "held" ? "Buy and hold" : "Trend overlay", percent: true,
    points: l.points.map(([day, r]) => ({ time: day, value: r * 100 })) })), 220));
  return [el("h3", null, title), el("p", { class: "small" }, `${w.start} to ${w.end}: worst fall cut ${fixed1(w.fall_cut)} `
    + `(PASS needs 33.3%), yearly return given up ${(w.given_up * 100 >= 0 ? "+" : "")}${(w.given_up * 100).toFixed(2)} `
    + `points (PASS needs at most 1.00)${w.verdict ? `: ${w.verdict}` : ", reported only"}.`),
  el("div", { class: "row compare" }, holder, windowTable(w))];
}

function trendSection({ changed } = {}) {
  const note = el("div", { class: "form-note" });
  const body = el("div");
  let chartsDrawn = [];
  let started = false;
  let loads = 0;
  let loadFailed = false;

  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  // Only the newest of overlapping loads (a repaint and a finished job) draws, so no chart is orphaned.
  async function load() {
    const mine = ++loads;
    let t;
    try {
      t = (await api("/api/plan/trend")).result;
    } catch (err) {
      if (mine !== loads) return;
      loadFailed = true;
      say(`Could not load the trend overlay test: ${err.message}`);
      return;
    }
    if (mine !== loads) return;
    if (loadFailed) {
      loadFailed = false;
      say("", false);
    }
    chartsDrawn.forEach((c) => c?.remove());
    chartsDrawn = [];
    if (!t) {
      body.replaceChildren(el("p", { class: "small" }, "Not run yet: press Run trend test."));
      return;
    }
    const charts = [];
    body.replaceChildren(
      el("p", { class: "small" }, "Verdict on confirm: ",
        el("strong", { class: t.passed ? "up" : "down", "data-verdict": "" }, t.verdict),
        ` (${t.series}, ${t.label}; stored ${t.first_day} to ${t.last_day}; run ${String(t.run_at).slice(0, 16).replace("T", " ")} UTC)`),
      el("p", { class: "dim small" }, `Rule: ${t.rule}`),
      el("p", { class: "dim small" }, `Cash earns ${t.cash}. Switching cost ${(t.cost * 100).toFixed(1)}% per switch.`),
      ...windowPart(t.confirm, TITLES.confirm, charts),
      el("h3", null, "Crashes (confirm)"), crashTable(t.confirm.crashes),
      ...windowPart(t.choose, TITLES.choose, charts),
      ...t.beside.flatMap((w) => windowPart(w, `${w.lines[0].label} (shown, not judged)`, charts)),
      el("ul", { class: "dim small" }, ...t.notes.map((n) => el("li", null, n))));
    chartsDrawn = charts.map((draw) => draw());
  }

  const state = (job) => say(el("span", null, `The trend overlay test is ${job.state}`
    + `${job.progress ? `: ${job.progress}` : ""}. Follow it in the `, el("a", { href: "#activity" }, "Activity tab"), "."),
  false);

  function follow(job) {
    state(job);
    followJob(job, state).then(() => say("The trend overlay test finished.", false), (err) => say(err.message))
      .finally(() => { load(); changed?.(); });
  }

  const run = el("button", { type: "button", onclick: async () => {
    run.disabled = true;
    try {
      follow(await api("/api/jobs/plan-trend", { method: "POST" }));
    } catch (err) {
      say(err.message);
    } finally {
      run.disabled = false;
    }
  } }, "Run trend test");

  const node = el("div", { class: "card" },
    el("h2", null, "Trend overlay test"),
    el("p", { class: "dim small" }, "One pre-registered timing rule for the index you hold, never tuned: at each month "
      + "end, hold the S&P 500 total-return index when its close is at or above its 10-month average of month-end "
      + "closes, else cash from the next open. In an ISA a switch costs no tax. It passes only if, on 2008 to today, "
      + "it cuts buy and hold's worst fall by at least a third while giving up at most 1 point of yearly return. "
      + "Running fetches the newest index days into the research database."),
    el("div", { class: "row" }, run), note, body);

  function start() {
    if (started) return Promise.resolve();
    started = true;
    return load();
  }

  return { node, start, reload: load };
}

export { trendSection };
