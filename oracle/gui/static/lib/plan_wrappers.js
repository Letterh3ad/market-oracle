// Where the next pound goes (boring-plan 09): workplace pension, Stocks and Shares ISA and, when switched
// on, the Lifetime ISA, compared per £100 a month and put in order. Your numbers ([wrappers]) are saved
// from this card; the Lifetime ISA switch saves as soon as it is ticked. `oracle plan wrappers` twins it.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { money } from "./fmt.js";
import { sendJson } from "./ui.js";

const NUMBERS = [
  ["salary", "Salary (GBP a year)"],
  ["employee_pct", "Your pension %"],
  ["employer_pct", "Employer %"],
  ["born", "Year of birth"],
  ["needed_before", "Needed before pension age (GBP a month)"],
  ["retire_tax", "Tax when drawn (%)"],
  ["pension_fee", "Pension charge (% a year)"],
  ["pension_charge", "Charge on each payment (%)"],
];
const SWITCHES = [["on_salary", "% of whole salary (not qualifying earnings)"], ["sacrifice", "Salary sacrifice"]];
const gbp = (v) => `£${money(v, 0)}`;
const unset = (key, v) => (key === "needed_before" ? v < 0 : ["salary", "born"].includes(key) && !v);

function rowsTable(c) {
  const at60 = c.rows.some((r) => r.at_60 != null);
  const head = [["Wrapper"], ["Paid in", null, 1], ["Added", null, 1], ["Fees", null, 1], ["Pot", null, 1],
    [`Spendable at ${c.access_age}`, null, 1], ...(at60 ? [["At 60", null, 1]] : []), ["Per £1", null, 1], ["Access"]];
  const rows = c.rows.map((r) => el("tr", { "data-wrapper": r.key }, el("td", null, r.label),
    el("td", { class: "num" }, gbp(r.paid_in)), el("td", { class: "num" }, gbp(r.added)),
    el("td", { class: "num" }, gbp(r.fees)), el("td", { class: "num" }, gbp(r.pot)),
    el("td", { class: "num" }, gbp(r.spendable)), ...(at60 ? [el("td", { class: "num" }, gbp(r.at_60))] : []),
    el("td", { class: "num" }, `£${r.per_pound.toFixed(2)}`), el("td", { class: "small" }, r.access)));
  return table(head, rows, "Nothing to compare yet.");
}

function result(c) {
  const parts = [];
  if (c.age != null) {
    parts.push(el("p", { class: "small" }, `Age ${c.age}, pension from ${c.access_age} (${c.years} years). Monthly amount `
      + (c.monthly ? `${gbp(c.monthly)} (Your plan card).` : "not set.")));
  }
  if (c.steps.length) {
    parts.push(el("h3", null, "Order"), el("ol", { "data-steps": "" }, ...c.steps.map((s) => el("li", null, s.text))));
  }
  if (c.rows.length) {
    parts.push(el("h3", null, `Per £100 a month of take-home pay for ${c.paid_years} years at `
      + `${(c.growth * 100).toFixed(1)}% a year (assumed)`), rowsTable(c));
  }
  if (c.isa_yearly) {
    parts.push(el("p", { class: "small" }, `ISA use: ${gbp(c.isa_yearly)} a year of the ${gbp(c.isa_allowance)} allowance.`));
  }
  if (c.notes.length) parts.push(el("ul", { class: "dim small" }, ...c.notes.map((n) => el("li", null, n))));
  if (c.missing.length) {
    parts.push(el("h3", null, "Missing"),
      el("ul", { class: "small", "data-missing": "" }, ...c.missing.map((m) => el("li", null, m.text))));
  }
  parts.push(el("details", { class: "small" }, el("summary", null, `Rates ${c.tax_year}, checked ${c.checked}: sources`),
    el("ul", null, ...c.sources.map(([label, url]) => el("li", null, `${label}: `,
      el("a", { href: url, target: "_blank", rel: "noopener" }, url))))));
  return parts;
}

function wrappersSection() {
  const note = el("div", { class: "form-note" });
  const body = el("div");
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const lisa = el("input", { type: "checkbox", "aria-label": "Show the Lifetime ISA" });
  const growth = el("input", { name: "growth", type: "number", step: "any", value: "5", size: 4,
    "aria-label": "Growth (% a year)" });
  const inputs = Object.fromEntries(NUMBERS.map(([key, label]) => [key, el("input", { name: key, type: "number",
    step: "any", size: 8, "aria-label": label })]));
  const boxes = Object.fromEntries(SWITCHES.map(([key, label]) => [key, el("input", { type: "checkbox",
    "aria-label": label })]));
  let started = false;
  let loads = 0;

  function fill(s) {
    lisa.checked = s.lisa;
    for (const [key] of NUMBERS) inputs[key].value = unset(key, s[key]) ? "" : String(s[key]);
    for (const [key] of SWITCHES) boxes[key].checked = s[key];
  }

  async function load() {
    const mine = ++loads;
    try {
      const c = await api(`/api/plan/wrappers?growth=${encodeURIComponent(growth.value || "5")}`);
      if (mine !== loads) return;
      fill(c.settings);
      body.replaceChildren(...result(c));
    } catch (err) {
      if (mine === loads) say(`Could not compare: ${err.message}`);
    }
  }

  async function save(values, done) {
    try {
      await sendJson("/api/plan/wrappers", values, "PUT");
      say(done, false);
      await load();
    } catch (err) {
      say(err.message);
    }
  }

  lisa.addEventListener("change", () => save({ lisa: !!lisa.checked },
    lisa.checked ? "Lifetime ISA shown." : "Lifetime ISA hidden."));

  const saveButton = el("button", { type: "button", onclick: async () => {
    saveButton.disabled = true;
    const values = {};
    for (const [key] of NUMBERS) {
      const raw = inputs[key].value.trim();
      if (raw === "") {
        if (key === "needed_before") values[key] = -1;
        else if (key === "salary" || key === "born") values[key] = 0;
        continue;
      }
      values[key] = key === "born" ? Math.trunc(Number(raw)) : Number(raw);
    }
    for (const [key] of SWITCHES) values[key] = !!boxes[key].checked;
    await save(values, "Saved.");
    saveButton.disabled = false;
  } }, "Save");
  const compare = el("button", { type: "button", onclick: () => load() }, "Compare");

  const node = el("div", { class: "card", "data-wrappers": "" },
    el("h2", null, "Where the next pound goes"),
    el("p", { class: "dim small" }, "Workplace pension (tax relief, employer money), Stocks and Shares ISA and, if you "
      + "switch it on, the Lifetime ISA, compared on your own numbers. Nothing about you is assumed: blank is not "
      + "entered. Rates are gov.uk's for the tax year shown."),
    el("div", { class: "row wrap" }, ...NUMBERS.map(([key, label]) => el("label", null, `${label} `, inputs[key])),
      ...SWITCHES.map(([key, label]) => el("label", null, boxes[key], ` ${label}`)), saveButton),
    el("div", { class: "row wrap" }, el("label", null, lisa, " Show the Lifetime ISA (off by default)"),
      el("label", null, "Growth (% a year) ", growth), compare),
    note, body);

  function start() {
    if (started) return Promise.resolve();
    started = true;
    return load();
  }

  return { node, start, reload: load };
}

export { wrappersSection };
