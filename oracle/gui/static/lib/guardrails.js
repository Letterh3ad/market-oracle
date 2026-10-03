// Guardrails (boring-plan 08): the no-sell rule, the plan change waiting out its 7-day cool-off, and
// what the worst real falls would do to the pot and the monthly plan. A card on the Plan tab; `changed`
// hears when the rule or the pending change moves, `refresh` when another card saved a change.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { sendJson } from "./ui.js";

const gbp = (x) => `${x < 0 ? "-" : ""}£${Math.abs(Math.round(x)).toLocaleString("en-GB")}`;
const fixed1 = (x) => `${(x * 100).toFixed(1)}%`;
const when = (iso) => iso.slice(0, 16).replace("T", " ");
const shown = (v) => (v === "" ? "(blank)" : String(v));

function changeList(c) {
  return el("ul", { class: "small" }, ...Object.entries(c.changes).map(([key, [from, to]]) =>
    el("li", null, `${key}: ${shown(from)} → ${shown(to)}`)));
}

// What a plan save answered: saved now, waiting, or both.
function savedText(r) {
  const parts = [];
  if (r.applied?.length) parts.push(`Saved ${r.applied.join(", ")}.`);
  if (r.pending) parts.push(`Change pending until ${when(r.pending.due)} UTC (7-day cool-off); cancel it under Guardrails.`);
  return parts.join(" ") || "Nothing changed.";
}

function drawdownTable(p) {
  const rows = p.rows.map((r) => {
    if (!r.fall) return el("tr", null, el("td", null, r.crash.name), el("td", { colspan: "8", class: "small dim" }, r.note));
    const f = r.fall;
    const paying = r.paying;
    return el("tr", { "data-crash": r.crash.name },
      el("td", null, r.crash.name, el("div", { class: "small dim" }, r.crash.label)),
      el("td", { class: "small" }, r.fund_label + (r.stand_in ? " (stand-in)" : "")),
      el("td", { class: "small" }, `${f.peak_day} to ${f.trough_day}`),
      el("td", { class: "num down" }, fixed1(f.fall)),
      el("td", { class: "num" }, gbp(r.pot_low)),
      el("td", { class: "num down" }, gbp(r.pot_loss)),
      el("td", { class: "num" }, `${r.months_down} mo`),
      el("td", { class: "small" }, f.recovered ? `${f.recovered} (${r.months_back} mo after the low)` : "not yet"),
      el("td", { class: "small" }, !paying ? "" : `${gbp(paying.paid_at_trough)} paid in by the low, worth `
        + `${gbp(paying.value_at_trough)}; at worst ${gbp(-paying.worst_gap)} under; even `
        + (paying.even ? `${paying.even} (${r.months_even} mo after the low)` : "not yet")));
  });
  return table([["Crash"], ["Measured on"], ["Peak to low"], ["Fall", null, 1], ["Pot at the low", null, 1],
    ["Loss", null, 1], ["Down for", null, 1], ["Back at the peak"], ["Paying in monthly"]], rows, "No crashes.");
}

function guardrailsSection({ changed } = {}) {
  const note = el("div", { class: "form-note" });
  const ruleBody = el("div");
  const pendingBody = el("div");
  const drawBody = el("div");
  const rule = el("textarea", { name: "rule", rows: 3, cols: 60, "aria-label": "No-sell rule" });
  const pot = el("input", { name: "pot", type: "number", min: "0", step: "any", size: 9, "aria-label": "Pot (GBP)" });
  let started = false;
  let loads = 0;

  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  function fill(g) {
    rule.value = g.rule;
    ruleBody.replaceChildren(el("p", null, g.rule ? el("strong", null, g.rule) : el("span", { class: "warn-text" },
      "Not written yet. Write it now, while calm: it is what you read in a fall instead of selling.")));
    const p = g.pending;
    pendingBody.replaceChildren(p
      ? el("div", { "data-pending": "" }, el("p", null, `Takes effect ${when(p.due)} UTC unless cancelled `
        + `(asked ${when(p.made_at)} UTC):`), changeList(p), cancel)
      : el("p", { class: "small dim" }, "Nothing pending."),
    ...(g.history.length ? [el("details", null, el("summary", { class: "small" }, "Recent changes"),
      el("ul", { class: "small" }, ...g.history.map((c) => el("li", null, `${c.state} ${when(c.settled_at)}: `
        + Object.entries(c.changes).map(([k, [from, to]]) => `${k} ${shown(from)} → ${shown(to)}`).join("; ")))))] : []));
  }

  async function load() {
    const mine = ++loads;
    try {
      const g = await api("/api/plan/guardrails");
      if (mine === loads) fill(g);
    } catch (err) {
      if (mine === loads) say(`Could not load the guardrails: ${err.message}`);
    }
  }

  async function loadDrawdowns() {
    const value = Number(pot.value);
    try {
      const p = await api(`/api/plan/drawdowns${pot.value && value > 0 ? `?pot=${encodeURIComponent(value)}` : ""}`);
      drawBody.replaceChildren(
        el("p", { class: "small" }, `${p.fund_label} (${p.source}, ${p.kind}); pot ${gbp(p.pot)}`
          + (p.example ? " (an example: enter yours above)" : "") + (p.monthly ? `, ${gbp(p.monthly)} a month.` : ".")),
        drawdownTable(p), el("ul", { class: "small dim" }, ...p.notes.map((n) => el("li", null, n))));
    } catch (err) {
      drawBody.replaceChildren(el("p", { class: "form-note down" }, err.message));
    }
  }

  const act = (button, run) => async () => {
    button.disabled = true;
    try {
      say(await run(), false);
      await load();
      changed?.();
    } catch (err) {
      say(err.message);
    } finally {
      button.disabled = false;
    }
  };
  const saveRule = el("button", { type: "button" }, "Save rule");
  saveRule.addEventListener("click", act(saveRule, async () => savedText(await sendJson("/api/plan/rule", { rule: rule.value }, "PUT"))));
  const cancel = el("button", { type: "button" }, "Cancel pending change");
  cancel.addEventListener("click", act(cancel, async () => { await sendJson("/api/plan/pending", null, "DELETE"); return "Cancelled."; }));
  const show = el("button", { type: "button", onclick: loadDrawdowns }, "Show");

  const node = el("div", { class: "card", "data-guardrails": "" },
    el("h2", null, "Guardrails"),
    el("p", { class: "dim small" }, "Things that keep the plan going through a crash. A change to a plan value "
      + "already set (fund, monthly, platform, this rule) waits 7 days before it takes effect, so a bad week "
      + "cannot rewrite the plan."),
    el("h3", null, "Your no-sell rule"), ruleBody,
    el("div", { class: "row wrap" }, rule, saveRule),
    el("h3", null, "Pending change"), pendingBody, note,
    el("h3", null, "Drawdown preview"),
    el("p", { class: "dim small" }, "The worst real falls on your plan's fund, in GBP with dividends reinvested: what "
      + "each would do to your pot and to the monthly plan, and how long it took to get back."),
    el("div", { class: "row wrap" }, el("label", null, "Your pot (GBP, not saved) ", pot), show), drawBody);

  function start() {
    if (started) return Promise.resolve();
    started = true;
    return Promise.all([load(), loadDrawdowns()]);
  }

  return { node, start, refresh: () => Promise.all([load(), loadDrawdowns()]) };
}

export { guardrailsSection, savedText };
