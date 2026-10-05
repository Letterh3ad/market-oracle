// The Research tab's results table (research-lab 06): every method in the literature index and every registered
// test once, with sources and quality, evidence, the stage reached, triage and power, each test line's train and
// confirm against its bar, turnover and break-even cost, the verdict, and links to the method's notes and the test's
// registration; deaths counted by stage. The same JSON as `oracle lab table --json`.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { tone, value } from "./lab.js";
import { stageCell } from "./triage.js";

const DEATHS = new Set(["fail", "could-not-see", "not-testable"]);
const verdictTone = (v) => (v === "pass" || v === "promising" ? "up" : DEATHS.has(v) ? "down" : "dim");
const figure = (measure, x) => (x == null ? "" : value({ measure, value: x }));
const pct = (x, places) => `${(x * 100).toFixed(places)}%`;

function picker(label, first) {
  return el("select", { "aria-label": label }, el("option", { value: "" }, first));
}

function fill(select, values, label = (v) => v) {
  if (select.dataset.filled) return;
  select.dataset.filled = "1";
  select.append(...values.map((v) => el("option", { value: v }, label(v))));
}

function sourcesCell(r) {
  return el("td", { class: "small" }, r.quality ? el("div", { "data-quality": "" }, r.quality) : null,
    ...r.sources.map((s) => el("div", { class: "dim" },
      `${[s.authors, s.year].filter(Boolean).join(" ") || s.title} (${s.quality})`)));
}

function evidenceCell(r) {
  const text = [r.evidence, r.result ? `Result: ${r.result}` : ""].filter(Boolean).join(" ");
  if (!text) return el("td", { class: "small dim" }, "");
  return el("td", { class: "small" }, text.length <= 120 ? text
    : el("details", null, el("summary", null, `${text.slice(0, 100)}...`), text));
}

function tradingText(line) {
  const parts = [line.turnover == null ? "" : `turnover ${pct(line.turnover, 1)}`,
    line.break_even == null ? "" : `break-even ${pct(line.break_even, 2)}`,
    line.dsr == null ? "" : `deflated Sharpe ${line.dsr.toFixed(3)}`];
  return parts.filter(Boolean).join(", ");
}

// One table row per test line; the method's own cells span them all.
function methodRows(r, links) {
  const lines = r.tests.flatMap((t) => t.lines.map((line, i) => ({ t, line, first: i === 0 })));
  const span = String(Math.max(1, lines.length));
  const notes = r.ref ? el("button", { type: "button", class: "small", onclick: () => links.openMethod(r.ref) }, "Notes")
    : null;
  const pick = r.ref && links.pick ? el("button", { type: "button", class: "small", onclick: () => links.pick(r.ref) },
    "Pick") : null;
  const lead = [
    el("td", null, r.name, el("div", { class: "small mono dim" }, r.ref ?? "a test no method names"),
      r.family ? el("div", { class: "small dim" }, r.family) : null, notes, " ", pick,
      ...r.notes.map((n) => el("div", { class: "small", "data-note": "" },
        `${n.at.slice(0, 10)} ${n.mark} by ${n.by}${n.note ? `: ${n.note}` : ""}`))),
    sourcesCell(r), evidenceCell(r),
    el("td", { class: "small", "data-stage-reached": "" }, r.stage,
      r.osap ? el("div", { class: "dim mono" }, `OSAP ${r.osap}`) : null),
    stageCell(r.triage), stageCell(r.power)];
  const tail = el("td", { class: `small ${verdictTone(r.verdict)}`, "data-verdict": r.verdict }, r.verdict,
    r.died_at ? el("div", { class: "dim" }, `stopped at ${r.died_at}`) : null,
    ...r.tested_under.map((u) => el("div", { class: "dim", "data-tested-under": u.ref },
      `${u.key}: tested under ${u.ref} (${u.stage}, ${u.verdict})`)),
    ...r.missing_tests.map((k) => el("div", { class: "dim" }, `${k}: not in the registry`)));
  for (const cell of [...lead, tail]) cell.setAttribute("rowspan", span);
  if (!lines.length) {
    return [el("tr", { "data-row": r.ref ?? "" }, ...lead, ...Array.from({ length: 6 }, () => el("td", null, "")), tail)];
  }
  return lines.map(({ t, line, first }, i) => el("tr", { "data-row": r.ref ?? t.key },
    ...(i === 0 ? lead : []),
    el("td", { class: "small" }, first ? el("span", { class: "mono" }, t.key) : null,
      first ? el("button", { type: "button", class: "small", onclick: () => links.openTest(t.key) }, "Registration")
        : null,
      el("div", { class: "dim" }, [line.horizon == null ? "" : `h${line.horizon}`, line.measure].filter(Boolean).join(" ")),
      el("div", { class: tone(line.verdict), "data-line-verdict": "" }, line.verdict)),
    el("td", { class: "num" }, figure(line.measure, line.train)),
    el("td", { class: "num" }, figure(line.measure, line.confirm)),
    el("td", { class: "num" }, line.t == null ? "" : line.t.toFixed(2),
      line.power == null ? null : el("div", { class: "small dim" }, `power ${line.power.toFixed(4)}`)),
    el("td", { class: "small" }, t.bar),
    el("td", { class: "small" }, tradingText(line)),
    ...(i === 0 ? [tail] : [])));
}

function resultsSection(links = {}) {
  const note = el("div", { class: "form-note" });
  const family = picker("Family", "every family");
  const stage = picker("Stage", "every stage");
  stage.append(el("option", { value: "funnel", selected: "" }, "in the funnel (not a candidate)"));
  const verdict = picker("Verdict", "every verdict");
  const counts = el("p", { class: "small", "data-deaths": "" });
  const body = el("div");

  async function load() {
    const q = new URLSearchParams();
    for (const [k, s] of [["family", family], ["stage", stage], ["verdict", verdict]]) if (s.value) q.set(k, s.value);
    try {
      const t = await api(`/api/lab/table${q.toString() ? `?${q}` : ""}`);
      fill(family, t.families);
      fill(stage, t.stages);
      fill(verdict, t.verdict_words);
      note.replaceChildren();
      counts.replaceChildren(`${t.rows.length} rows. Deaths by stage: `
        + `${Object.entries(t.deaths).map(([k, n]) => `${k} ${n}`).join(", ")}. By verdict: `
        + `${Object.entries(t.verdicts).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(", ") || "none"}.`);
      body.replaceChildren(table([["Method"], ["Sources"], ["Evidence"], ["Stage"], ["Triage"], ["Power"], ["Test"],
        ["Train", null, 1], ["Confirm", null, 1], ["t", null, 1], ["Bar"], ["Turnover, break-even"], ["Verdict"]],
      t.rows.flatMap((r) => methodRows(r, links)), "No method or test matches."));
    } catch (err) {
      note.className = "form-note down";
      note.replaceChildren(`Could not load the results table: ${err.message}`);
    }
  }
  for (const s of [family, stage, verdict]) s.addEventListener("change", load);

  const node = el("div", { class: "card", "data-results": "" }, el("h2", null, "Results table"),
    el("p", { class: "dim small" }, "Every method in the literature index and every registered test, once each. Stage "
      + "is the furthest the method reached (candidate: not yet in the funnel; unrecorded: a verdict from before the "
      + "funnel recorded where it stopped). A test line shows train and confirm (IC, mean excess or CAGR over the "
      + "index), t against the bar it was registered with, the power (the smallest IC the confirm window could "
      + "see), and the turnover and break-even cost. Notes opens the method in the literature index; Registration "
      + "opens the test's pre-registration on the scoreboard. "
      + "oracle lab table --json gives the same table."),
    el("div", { class: "row small" }, family, " ", stage, " ", verdict), counts, note, body);
  return { node, start: load };
}

export { resultsSection };
