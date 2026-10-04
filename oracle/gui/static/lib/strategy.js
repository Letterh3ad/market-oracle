// The Research tab's strategy tests (research-lab 02): the UK retail cost model from [plan] with a calculator
// for one trade, each strategy test with Run and its newest kept result (train and confirm, after and before
// costs, against index buy-and-hold), and the trend overlay check (its kept result reproduced, never judged again).
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { pct } from "./fmt.js";
import { followJob } from "./ui.js";

const share = (x, d = 2) => `${(x * 100).toFixed(d)}%`;
const pounds = (x) => `£${x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const BANDS = ["", "Mega Cap", "Large Cap", "Mid Cap", "Small Cap", "Micro Cap", "Nano Cap"];

function modelText(m) {
  return `Platform: ${m.platform} (${m.percent_fee.toFixed(2)}% a year, cap ${pounds(m.percent_cap)}; flat `
    + `${pounds(m.flat_fee)} a month). Dealing ${pounds(m.dealing_fee)} a trade; FX ${m.fx_fee.toFixed(2)}% a trade on `
    + `non-GBP listings; stamp duty ${share(m.stamp_duty, 1)} on UK share buys. Half spread a trade: `
    + `${m.half_spreads.map(([b, x]) => `${b} ${share(x)}`).join(", ")}; no band ${share(m.default_half_spread)}. `
    + `A test trades a pot of ${pounds(m.pot)}.`;
}

function windowTable(w) {
  const rows = [w.strategy, w.gross, w.index, w.index_gross].map((x) => el("tr", null,
    el("td", null, x.label), el("td", { class: "num" }, pct(x.ret)), el("td", { class: "num" }, pct(x.cagr)),
    el("td", { class: "num down" }, share(x.max_fall, 1)),
    el("td", { class: "num" }, x.sharpe == null ? "-" : x.sharpe.toFixed(2)), el("td", { class: "num" }, String(x.trades)),
    el("td", { class: "num" }, share(x.turnover, 1)),
    el("td", { class: "num small" }, `dealing ${pounds(x.dealing)}, spread ${pounds(x.spread)}, stamp `
      + `${pounds(x.stamp)}, FX ${pounds(x.fx)}, platform ${pounds(x.platform)}`)));
  return el("div", { "data-window": w.window },
    el("p", { class: "small" }, `${w.window === "train" ? "Train" : "Confirm"} ${w.start} to ${w.end}`
      + `${w.verdict ? "" : " (reported only)"}: excess CAGR after costs ${pct(w.excess)} (before ${pct(w.gross_excess)}), `
      + `deflated Sharpe ${w.dsr == null ? "-" : w.dsr.toFixed(3)}${w.verdict ? `: ${w.verdict}` : ""}.`),
    table([["Line"], ["Return", null, 1], ["CAGR", null, 1], ["Worst fall", null, 1], ["Sharpe", null, 1],
      ["Trades", null, 1], ["Turnover a year", null, 1], ["Costs paid"]], rows, "No sessions."));
}

function resultBlock(t) {
  return el("details", { "data-result": t.key }, el("summary", null, `${t.key}: ${t.verdict} (${String(t.run_at)
    .slice(0, 16).replace("T", " ")} UTC)`),
  el("p", { class: "small" }, `Rule: ${t.rule}`), el("p", { class: "small" }, `PASS: ${t.bar_rule}`),
  windowTable(t.train), windowTable(t.confirm), ...t.notes.map((n) => el("p", { class: "small dim" }, n)));
}

function checkTable(c) {
  const fmt = (r, x) => (r.measure === "switches" ? String(x) : share(x, 4));
  return el("div", { "data-check": "" },
    el("p", { class: `small ${c.matches ? "" : "down"}` }, `Kept ${String(c.run_at).slice(0, 16).replace("T", " ")} UTC `
      + `(to ${c.last_day}), verdict ${c.recorded_verdict} as recorded. ${c.matches ? "Reproduced within rounding."
        : "NOT reproduced within rounding."} ${c.note}`),
    table([["Window"], ["Measure"], ["Recorded", null, 1], ["Reproduced", null, 1], ["Match"]],
      c.rows.map((r) => el("tr", null, el("td", null, r.window), el("td", null, r.measure),
        el("td", { class: "num" }, fmt(r, r.recorded)), el("td", { class: "num" }, fmt(r, r.reproduced)),
        el("td", { class: r.matches ? "" : "down" }, r.matches ? "yes" : "NO"))), "Nothing to compare."));
}

function strategySection() {
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const model = el("p", { class: "small" });
  const notes = el("div");
  const priced = el("p", { class: "small" });
  const list = el("div");
  const check = el("div");

  const value = el("input", { size: 10, "aria-label": "Trade value (GBP)", value: "10000" });
  const side = el("select", { "aria-label": "Side" }, ...["buy", "sell"].map((s) => el("option", { value: s }, s)));
  const currency = el("input", { size: 4, "aria-label": "Listing currency", value: "GBP" });
  const uk = el("input", { type: "checkbox", "aria-label": "UK shares (stamp duty on buys)" });
  const band = el("select", { "aria-label": "Size band" }, ...BANDS.map((b) => el("option", { value: b }, b || "no band")));
  const price = el("button", { type: "button", onclick: async () => {
    const q = new URLSearchParams({ value: value.value, side: side.value, currency: currency.value,
      uk_shares: String(uk.checked) });
    if (band.value) q.set("band", band.value);
    try {
      const t = (await api(`/api/lab/costs?${q}`)).trade;
      priced.replaceChildren(`A ${t.side} of ${pounds(t.value)}: dealing ${pounds(t.dealing)}, spread `
        + `${pounds(t.spread)}, stamp duty ${pounds(t.stamp)}, FX ${pounds(t.fx)}; total ${pounds(t.total)}.`);
    } catch (err) { say(err.message); }
  } }, "Price trade");

  async function loadCosts() {
    try {
      const m = (await api("/api/lab/costs")).model;
      model.replaceChildren(modelText(m));
      notes.replaceChildren(...m.notes.map((n) => el("p", { class: "small dim" }, n)));
    } catch (err) { say(`Could not load the cost model: ${err.message}`); }
  }

  async function run(key, button) {
    button.disabled = true;
    try {
      const job = await api(`/api/jobs/strategy-test?key=${encodeURIComponent(key)}`, { method: "POST" });
      say(`The ${key} strategy test is queued; follow it on the Activity tab.`, false);
      await followJob(job, () => {});
      say(`The ${key} strategy test finished.`, false);
    } catch (err) {
      say(err.message);
    } finally {
      button.disabled = false;
      loadStrategies();
    }
  }

  async function loadStrategies() {
    let body;
    try {
      body = await api("/api/lab/strategies");
    } catch (err) {
      say(`Could not load the strategy tests: ${err.message}`);
      return;
    }
    const strategies = body.strategies ?? [];
    const rows = strategies.map((s) => {
      const button = el("button", { type: "button", onclick: () => run(s.key, button) }, "Run test");
      return el("div", { class: "row small", "data-strategy": s.key }, el("span", { class: "mono" }, s.key), ` ${s.label}: `,
        s.result ? s.result.verdict : "not run", " ", button);
    });
    const kept = [...strategies.filter((s) => s.result).map((s) => s.result), ...(body.kept ?? [])];
    list.replaceChildren(...(rows.length ? rows : [el("p", { class: "small dim" }, "No strategy test has code yet: "
      + "register one on the scoreboard, commit it, then its rule is added.")]), ...kept.map(resultBlock));
  }

  const reproduce = el("button", { type: "button", onclick: async () => {
    try {
      check.replaceChildren(checkTable(await api("/api/lab/trend-check")));
    } catch (err) { say(err.message); }
  } }, "Check the trend overlay");

  const node = el("div", { class: "card" }, el("h2", null, "Strategy tests"),
    el("p", { class: "dim small" }, "A strategy test backtests a registered rule against index buy-and-hold on train "
      + "(2010-2019, reported) and confirm (2020 on, judged), after UK retail costs. PASS: beats the index after costs "
      + "on confirm and a deflated Sharpe probability of its daily excess over the index of at least 0.95, given the "
      + "variants its registration says were tried. Working only before costs is a FAIL."),
    el("h3", null, "Costs"), model, notes,
    el("div", { class: "row small" }, "Value ", value, " ", side, " currency ", currency, " ",
      el("label", null, uk, " UK shares"), " band ", band, " ", price), priced,
    el("h3", null, "Tests"), note, list,
    el("h3", null, "Trend overlay check"),
    el("p", { class: "dim small" }, "Re-runs the kept trend overlay result through the strategy engine with its "
      + "registered cost. A check on the engine: the overlay FAILed and is never judged again."), reproduce, check);
  return { node, start: () => Promise.all([loadCosts(), loadStrategies()]) };
}

export { strategySection };
