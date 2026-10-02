// Fees and contributions (boring-plan 03): the user's platform ([plan] settings), the fee calculator
// and the contribution tests. Lives on the Ledgers tab until the Plan tab exists.
import { api } from "./api.js";
import { linesChart } from "./chart.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { money, pct } from "./fmt.js";
import { sendJson } from "./ui.js";

const PLATFORM = [
  ["platform", "You pay", "select"],
  ["percent_fee", "Percentage fee (% a year)"],
  ["percent_cap", "Cap on it (GBP a year, 0 none)"],
  ["flat_fee", "Flat fee (GBP a month)"],
  ["dealing_fee", "Dealing (GBP per trade)"],
  ["fx_fee", "FX (% of each buy)"],
];
const gbp = (v) => `£${money(v, 0)}`;
const fixed1 = (v) => `${(v * 100).toFixed(1)}%`;

function field(name, label, value, attrs = {}) {
  const input = el("input", { name, value: String(value), size: 8, ...attrs });
  return [el("label", null, label, " ", input), input];
}

function spreadRow(name, s) {
  return el("tr", null, el("td", null, name), el("td", { class: "num" }, String(s.count)),
    el("td", { class: "num" }, `${Math.round(s.wins * 100)}%`), el("td", { class: "num" }, pct(s.median)),
    el("td", { class: "num" }, pct(s.p10)), el("td", { class: "num" }, pct(s.p90)),
    el("td", { class: "num" }, pct(s.worst)), el("td", { class: "num" }, pct(s.best)));
}

function feeTable(d) {
  const plain = d.lines[0].value;
  const rows = d.lines.map((l) => el("tr", { class: l.name === d.using ? "picked" : null },
    el("td", null, l.label + (l.name === d.using ? " (yours)" : "")), el("td", { class: "num" }, gbp(l.value)),
    el("td", { class: "num" }, gbp(l.fees)), el("td", { class: "num down" }, gbp(l.drag)),
    el("td", { class: "num" }, plain ? fixed1(l.drag / plain) : "n/a")));
  return table([["Platform"], ["Pot", null, 1], ["Fees paid", null, 1], ["Fee drag", null, 1], ["Drag", null, 1]],
    rows, "No lines.");
}

function feeResult(d) {
  const cross = d.crossover == null ? "Flat never beats percentage at these fees."
    : `Flat beats percentage once the pot is above ${gbp(d.crossover)}`
      + (d.crossover_year ? ` (the percentage pot passes it in year ${d.crossover_year}).` : ".");
  return [el("p", { class: "small" }, `${gbp(d.monthly)} a month for ${d.years} years at ${(d.growth * 100).toFixed(1)}% `
    + `a year (assumed), from ${gbp(d.start)}: ${gbp(d.contributed)} paid in.`),
  el("p", { "data-crossover": "" }, el("strong", null, cross)), feeTable(d),
  el("ul", { class: "dim small" }, ...d.notes.map((n) => el("li", null, n)))];
}

function mixTable(t) {
  const rows = t.mixes.map((m) => el("tr", null, el("td", null, m.rule), el("td", { class: "num" }, gbp(m.result.value)),
    el("td", { class: "num" }, gbp(m.result.contributed)), el("td", { class: "num" }, pct(m.result.growth - 1)),
    el("td", { class: "num down" }, fixed1(m.result.worst_fall)), el("td", { class: "num" }, gbp(m.result.fees)),
    el("td", { class: "num" }, String(m.result.trades)), el("td", { class: "num" }, fixed1(m.result.weight))));
  return table([["Rule"], ["Pot", null, 1], ["Paid in", null, 1], ["Growth", null, 1], ["Worst fall", null, 1],
    ["Fees", null, 1], ["Trades", null, 1], ["End weight", null, 1]], rows, "No second fund stored.");
}

function contributionResult(t, charts) {
  const pts = t.series.points;
  const holder = el("div", { class: "compare-chart" });
  charts.push(() => linesChart(holder, [{ colour: "--accent", title: "Lump ahead of drip", percent: true,
    points: t.starts.map(([day, lead]) => ({ time: day, value: lead * 100 })) }], 200));
  const c = t.cash;
  const parts = [
    el("p", { class: "small" }, `On ${t.series.label}, month ends ${pts[0][0]} to ${pts[pts.length - 1][0]}; fees: ${t.fees}.`),
    table([["Test"], ["Starts", null, 1], ["Above 0", null, 1], ["Median", null, 1], ["P10", null, 1], ["P90", null, 1],
      ["Worst", null, 1], ["Best", null, 1]], [
      spreadRow(`Lump ${gbp(t.amount)} vs 12-month drip`, t.lump),
      spreadRow(`${gbp(t.monthly)} a month, ${t.years}-year gain`, t.windows)], "No starts."),
    el("p", { class: "dim small" }, "First row: how far the lump ends ahead of the drip at the drip's end, over every "
      + "start month. Second: the pot over the money paid in, over every window."),
    el("h3", null, "Lump ahead of drip, by start month"), holder,
  ];
  if (t.mixes.length) {
    parts.push(el("h3", null, `Rebalancing ${Math.round(t.weight * 100)}% ${t.series.symbol} / `
      + `${Math.round((1 - t.weight) * 100)}% ${t.second.label}, ${t.mix_span[0]} to ${t.mix_span[1]}`), mixTable(t));
  }
  parts.push(el("p", { class: "small", "data-cash": "" }, `Cash drag: ${Math.round(c.share * 100)}% of the pot in cash `
    + `earning ${(c.cash_rate * 100).toFixed(1)}% gives up ${(c.given_up * 100).toFixed(2)} points a year.`),
  el("ul", { class: "dim small" }, ...t.notes.map((n) => el("li", null, n))));
  return parts;
}

function planMoneySection() {
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const platformForm = el("div", { class: "row wrap" });
  const feeBody = el("div");
  const contributionBody = el("div");
  let drawn = [];
  let started = false;

  async function loadPlatform() {
    const rows = (await api("/api/settings")).settings.filter((s) => s.section === "plan");
    const value = (key) => rows.find((r) => r.key === key)?.value;
    const inputs = PLATFORM.map(([key, label, kind]) => {
      if (kind === "select") {
        const select = el("select", { name: key }, ...["percentage", "flat"].map((p) =>
          el("option", { value: p, selected: value(key) === p ? "" : null }, p)));
        return [el("label", null, label, " ", select), select, key];
      }
      return [...field(key, label, value(key) ?? 0, { type: "number", step: "any", min: "0" }), key];
    });
    const save = el("button", { type: "button", onclick: async () => {
      save.disabled = true;
      try {
        for (const [, input, key] of inputs) {
          const v = key === "platform" ? input.value : Number(input.value);
          await sendJson("/api/settings", { section: "plan", key, value: v }, "PUT");
        }
        say("Platform saved.", false);
      } catch (err) {
        say(err.message);
      } finally {
        save.disabled = false;
      }
    } }, "Save platform");
    platformForm.replaceChildren(...inputs.map(([label]) => label), save);
  }

  const [feeMonthlyLabel, feeMonthly] = field("monthly", "GBP a month", 500, { type: "number", min: "0", step: "any" });
  const [yearsLabel, years] = field("years", "Years", 25, { type: "number", min: "1", max: "60" });
  const [growthLabel, growth] = field("growth", "Growth (% a year, assumed)", 5, { type: "number", step: "any" });
  const [startLabel, start] = field("start", "Pot now (GBP)", 0, { type: "number", min: "0", step: "any" });
  const feeButton = el("button", { type: "button", onclick: async () => {
    try {
      const q = new URLSearchParams({ monthly: feeMonthly.value, years: years.value, growth: growth.value, start: start.value });
      feeBody.replaceChildren(...feeResult(await api(`/api/plan/fees?${q}`)));
    } catch (err) {
      say(err.message);
    }
  } }, "Work out fees");
  const feeForm = el("div", { class: "row wrap" }, feeMonthlyLabel, yearsLabel, growthLabel, startLabel, feeButton);

  const inputs = {
    monthly: field("monthly", "GBP a month", 500, { type: "number", min: "0", step: "any" }),
    lump: field("lump", "Lump (GBP, blank 12 months')", "", { type: "number", min: "0", step: "any" }),
    years: field("years", "Window (years)", 10, { type: "number", min: "1", max: "40" }),
    second: field("second", "Second fund", "ISF.L"),
    weight: field("weight", "% in ACWI", 75, { type: "number", min: "1", max: "99" }),
    band: field("band", "Band (points)", 5, { type: "number", min: "1", max: "49" }),
    cash: field("cash", "% left in cash", 5, { type: "number", min: "0", max: "99" }),
    cash_rate: field("cash_rate", "Cash rate (% a year)", 0, { type: "number", step: "any" }),
  };
  const run = el("button", { type: "button", onclick: async () => {
    run.disabled = true;
    try {
      const q = new URLSearchParams();
      for (const [key, [, input]] of Object.entries(inputs)) if (input.value !== "") q.set(key, input.value);
      const t = await api(`/api/plan/contributions?${q}`);
      drawn.forEach((c) => c?.remove());
      const charts = [];
      contributionBody.replaceChildren(...contributionResult(t, charts));
      drawn = charts.map((draw) => draw());
    } catch (err) {
      say(err.message);
    } finally {
      run.disabled = false;
    }
  } }, "Run contribution tests");
  const contributionForm = el("div", { class: "row wrap" }, ...Object.values(inputs).map(([label]) => label), run);

  const node = el("div", { class: "card" },
    el("h2", null, "Fees and contributions"),
    el("p", { class: "dim small" }, "Your platform's prices (no presets: copy them from its price list), what fees "
      + "take over a horizon, and contribution habits tested on every start month of ACWI's total return in GBP "
      + "(a proxy for an all-world fund), after your fees. Amounts are never stored."),
    el("h3", null, "Your platform"), platformForm, note,
    el("h3", null, "Fee calculator"), feeForm, feeBody,
    el("h3", null, "Contribution tests"), contributionForm, contributionBody);

  function startSection() {
    if (started) return Promise.resolve();
    started = true;
    return loadPlatform().catch((err) => say(`Could not load the platform settings: ${err.message}`));
  }

  return { node, start: startSection };
}

export { planMoneySection };
