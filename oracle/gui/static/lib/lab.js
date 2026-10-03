// The Research tab's scoreboard (research-lab 01): every pre-registered test, one row per horizon,
// with the bar it was judged by, its verdict and power; a test's full registration with Commit and
// Drop; and the form that registers a new test from the spec-8 template (its bar frozen on save).
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { sendJson } from "./ui.js";

const num = (x, places) => (x == null ? "" : `${x >= 0 ? "+" : ""}${x.toFixed(places)}`);
const value = (r) => (r.value == null ? "" : r.measure === "IC" ? num(r.value, 4) : `${num(r.value * 100, 2)}%`);
const tone = (v) => (v.startsWith("PASS") ? "up" : v.startsWith("FAIL") ? "down" : "dim");

const FIELDS = [
  ["key", "Key (lower case, e.g. sue_v2)"], ["label", "Name"], ["rule", "Exact rule", true],
  ["universe", "Universe"], ["expected_size", "Expected size"], ["source", "Source of the sign and size"],
  ["power", "Power estimate: could the testbed see that size?", true], ["costs", "Costs"],
  ["kill_stage", "Kill stage"], ["parent", "Near a registered test (its key), if any"],
  ["different_because", "Different because (needed with a parent)", true]];

function scoreTable(board, pick) {
  return table([["Test"], ["Type"], ["Registered"], ["h", null, 1], ["Confirm", null, 1], ["t", null, 1], ["Bar"],
    ["Verdict"], ["Power", null, 1]],
  board.rows.map((r) => el("tr", { "data-test": r.key },
    el("td", null, r.label, " ", el("button", { type: "button", class: "small", onclick: () => pick(r.key) },
      "Registration"), el("div", { class: "small mono dim" }, r.key)),
    el("td", { class: "small" }, r.kind),
    el("td", { class: "small" }, r.registered_on, r.legacy ? el("div", { class: "dim" }, "before the lab") : null),
    el("td", { class: "num" }, r.horizon == null ? "" : String(r.horizon)),
    el("td", { class: "num" }, value(r), r.measure ? el("div", { class: "small dim" }, r.measure) : null),
    el("td", { class: "num" }, num(r.t, 2)),
    el("td", { class: "small" }, r.bar),
    el("td", { class: `small ${tone(r.verdict)}`, "data-verdict": "" }, r.verdict,
      r.note ? el("div", { class: "dim" }, r.note) : null),
    el("td", { class: "num" }, r.power == null ? "" : r.power.toFixed(4)))),
  "No test is registered yet.");
}

function scoreboardSection() {
  const note = el("div", { class: "form-note" });
  const head = el("p", { class: "small" });
  const body = el("div");
  const detail = el("div");
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  async function load() {
    let board;
    try {
      board = await api("/api/lab/scoreboard");
    } catch (err) {
      say(`Could not load the scoreboard: ${err.message}`);
      return;
    }
    head.replaceChildren(`${board.n} hypotheses registered (each horizon counts once). A one-horizon test `
      + `registered now needs t >= ${board.next_bar.toFixed(2)}: max(3, the two-sided Bonferroni 5% bar over every `
      + "registered test). Tests registered before the lab keep the bar they were judged by.");
    body.replaceChildren(scoreTable(board, show));
  }

  async function show(key) {
    let r;
    try {
      r = await api(`/api/lab/tests/${encodeURIComponent(key)}`);
    } catch (err) {
      say(err.message);
      return;
    }
    const hash = el("input", { size: 12, "aria-label": "Registration commit" });
    const commit = el("button", { type: "button", onclick: async () => {
      try {
        await sendJson(`/api/lab/tests/${encodeURIComponent(key)}/commit`, { hash: hash.value }, "PUT");
        say(`${key} committed: its data runs may start.`, false);
        await Promise.all([load(), show(key)]);
      } catch (err) { say(err.message); }
    } }, "Record commit");
    const drop = el("button", { type: "button", onclick: async () => {
      try {
        await api(`/api/lab/tests/${encodeURIComponent(key)}`, { method: "DELETE" });
        say(`Dropped ${key}.`, false);
        detail.replaceChildren();
        await load();
      } catch (err) { say(err.message); }
    } }, "Drop");
    const rows = [["Registered", r.registered_on], ["Type", r.kind], ["Horizons", r.horizons.join(", ") || "none"],
      ["Rule", r.rule], ["Universe", r.universe], ["Expected", `${r.expected_sign}, ${r.expected_size} (${r.source})`],
      ["Power", r.power], ["Costs", r.costs], ["Bar", r.bar_text], ["Pass rule", r.bar_rule],
      ["Kill stage", r.kill_stage], ["Parent", r.parent], ["Different because", r.different_because],
      ["Commit", r.commit_hash], ["Note", r.note]].filter(([, v]) => v);
    detail.replaceChildren(el("div", { class: "card", "data-registration": key },
      el("h3", null, `${r.label} (${r.key})`),
      el("table", { class: "small" }, el("tbody", null, ...rows.map(([k, v]) =>
        el("tr", null, el("th", null, k), el("td", null, v))))),
      r.commit_hash ? null : el("div", { class: "row" },
        el("span", { class: "small" }, "Not committed: data runs are refused. Commit the DECISIONS entry naming `"
          + `${key}\`, then record its hash `), hash, commit, drop)));
  }

  const inputs = Object.fromEntries(FIELDS.map(([name, label, long]) => [name, long
    ? el("textarea", { rows: 2, cols: 60, "aria-label": label })
    : el("input", { size: 40, "aria-label": label })]));
  const kind = el("select", { "aria-label": "Test type" },
    ...["signal", "event", "strategy"].map((k) => el("option", { value: k }, k)));
  const sign = el("select", { "aria-label": "Expected sign" },
    ...["positive", "negative"].map((k) => el("option", { value: k }, k)));
  const horizons = el("input", { size: 10, "aria-label": "Horizons (trading days, comma separated)" });
  const save = el("button", { type: "button", onclick: async () => {
    const text = (n) => inputs[n].value.trim();
    const body = { ...Object.fromEntries(FIELDS.map(([n]) => [n, text(n)])), kind: kind.value,
      expected_sign: sign.value, parent: text("parent") || null, different_because: text("different_because") || null,
      horizons: horizons.value.split(",").map((h) => h.trim()).filter(Boolean).map(Number) };
    try {
      const r = await sendJson("/api/lab/tests", body);
      say(`Registered ${r.key}: ${r.bar_rule}. Commit it to DECISIONS before any data run.`, false);
      await Promise.all([load(), show(r.key)]);
    } catch (err) { say(err.message); }
  } }, "Register");
  const form = el("details", null, el("summary", null, "Register a test (the pre-registration template)"),
    el("p", { class: "small dim" }, "Its bar is frozen on save. Up to 2 horizons for a signal or event (each counts "
      + "toward N); a strategy has none. No data run until its DECISIONS entry is committed and recorded here."),
    el("div", { class: "row small" }, "Type ", kind, " expected sign ", sign, " horizons ", horizons),
    ...FIELDS.map(([name, label]) => el("div", { class: "row small" }, el("label", null, `${label} `, inputs[name]))),
    save);

  const node = el("div", { class: "card" }, el("h2", null, "Scoreboard"),
    el("p", { class: "dim small" }, "Every pre-registered test, one row per horizon: its confirm IC (signals), mean "
      + "excess return (events) or CAGR over buy and hold (strategies), t, the bar it is judged by, the verdict as "
      + "judged and power (the smallest IC its confirm window could see at that bar). The test runs themselves stay "
      + "on the Ledgers tab's Signal tests card."),
    head, note, body, detail, form);
  return { node, start: load };
}

export { scoreboardSection };
