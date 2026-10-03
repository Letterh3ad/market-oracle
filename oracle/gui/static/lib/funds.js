// The fund comparison (boring-plan 02): all-world, developed world, S&P 500 and a UK home-bias mix as
// sterling accumulating funds after their TER, every line a proxy, and each real UCITS fund tracked
// against its proxy. Self-contained card on the Plan tab; `changed` hears when a run finishes.
import { api } from "./api.js";
import { linesChart } from "./chart.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { pct } from "./fmt.js";
import { followJob } from "./ui.js";

const fixed1 = (x) => `${(x * 100).toFixed(1)}%`;
const fixed2 = (x) => `${(x * 100).toFixed(2)}%`;
const points = (x) => `${x * 100 >= 0 ? "+" : ""}${(x * 100).toFixed(2)}`;
const COLOURS = { all_world: "--bench1", developed: "--accent", sp500: "--warn", uk: "--text-dim", uk_mix: "--regime" };

function fundTable(rows) {
  const trs = rows.map((r) => el("tr", { class: r.compared ? null : "dim" },
    el("td", null, r.label, el("div", { class: "dim small" }, `${r.source}, ${r.kind}`)),
    el("td", { class: "num" }, pct(r.return)), el("td", { class: "num" }, pct(r.cagr)),
    el("td", { class: "num down" }, fixed1(r.max_fall)), el("td", { class: "num" }, fixed2(r.ter)),
    el("td", { class: "num" }, points(r.ter_drag)), el("td", { class: "num" }, `${Math.round(r.us_share * 100)}%`),
    el("td", { class: "num small" }, `${pct(r.own_cagr)} since ${r.own_start}`)));
  return table([["Fund"], ["Return", "return", 1], ["Yearly", null, 1], ["Worst fall", "max_fall", 1], ["TER", null, 1],
    ["TER drag (points)", null, 1], ["US share", null, 1], ["Own history, yearly", null, 1]], trs, "No funds.");
}

function trackTable(tracks) {
  const trs = tracks.map((t) => el("tr", null, el("td", null, `${t.real_label} (${t.real})`), el("td", null, t.proxy),
    ...(t.start == null ? [el("td", { colspan: "5", class: "dim small" }, "No stored days inside the proxy's.")] : [
      el("td", { class: "small" }, `${t.start} to ${t.end}`), el("td", { class: "num" }, pct(t.real_return)),
      el("td", { class: "num" }, pct(t.model_return)), el("td", { class: "num" }, points(t.difference)),
      el("td", { class: "num" }, points(t.yearly_difference))])));
  return table([["Real fund"], ["Proxy"], ["Period"], ["Real", null, 1], ["Proxy model", null, 1],
    ["Difference (points)", null, 1], ["Per year (points)", null, 1]], trs, "No real fund stored.");
}

function fundsSection({ changed } = {}) {
  const note = el("div", { class: "form-note" });
  const body = el("div");
  let chart = null;
  let started = false;
  let loads = 0;
  let loadFailed = false;

  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  // Only the newest of overlapping loads (a repaint and a finished job) draws, so no chart is orphaned.
  async function load() {
    const mine = ++loads;
    let c;
    try {
      c = (await api("/api/plan/funds")).result;
    } catch (err) {
      if (mine !== loads) return;
      loadFailed = true;
      say(`Could not load the fund comparison: ${err.message}`);
      return;
    }
    if (mine !== loads) return;
    if (loadFailed) {
      loadFailed = false;
      say("", false);
    }
    chart?.remove();
    chart = null;
    if (!c) {
      body.replaceChildren(el("p", { class: "small" }, "Not run yet: press Compare funds."));
      return;
    }
    const holder = el("div", { class: "compare-chart" });
    const fx = c.fx;
    body.replaceChildren(
      el("p", { class: "small" }, `In ${c.base}, accumulating, after each fund's TER, over the history every proxy `
        + `shares: ${c.start} to ${c.end} (run ${String(c.run_at).slice(0, 16).replace("T", " ")} UTC). `,
      el("strong", null, "Every line is a proxy.")),
      holder, fundTable(c.rows),
      el("h3", null, "Tracking: each real fund against its proxy (from 90 days after its launch)"), trackTable(c.tracking),
      ...(fx ? [el("p", { class: "dim small" }, `FX check: ${fx.symbol} closed ${fx.close.toFixed(2)} ${fx.currency} on `
        + `${fx.day}, at ${fx.rate.toFixed(4)} ${fx.currency} per ${c.base} = ${fx.close_base.toFixed(2)} ${c.base}.`)] : []),
      el("p", { class: "dim small" }, `TERs, proxy fees and US shares from factsheets on ${c.checked}.`),
      el("ul", { class: "dim small" }, ...c.notes.map((n) => el("li", null, n))));
    chart = linesChart(holder, c.rows.filter((r) => r.points.length > 1).map((r) => ({
      colour: COLOURS[r.name] ?? "--text", dashed: !r.compared, title: r.label, percent: true,
      points: r.points.map(([day, v]) => ({ time: day, value: v * 100 })) })), 260);
  }

  const state = (job) => say(el("span", null, `The fund comparison is ${job.state}`
    + `${job.progress ? `: ${job.progress}` : ""}. Follow it in the `, el("a", { href: "#activity" }, "Activity tab"), "."),
  false);

  function follow(job) {
    state(job);
    followJob(job, state).then(() => say("The fund comparison finished.", false), (err) => say(err.message))
      .finally(() => { load(); changed?.(); });
  }

  const run = el("button", { type: "button", onclick: async () => {
    run.disabled = true;
    try {
      follow(await api("/api/jobs/plan-funds", { method: "POST" }));
    } catch (err) {
      say(err.message);
    } finally {
      run.disabled = false;
    }
  } }, "Compare funds");

  const node = el("div", { class: "card" },
    el("h2", null, "Fund comparison"),
    el("p", { class: "dim small" }, "Which index fund to hold: all-world, developed world, S&P 500 or a UK home-bias "
      + "mix, as sterling accumulating funds after their yearly charge. The long histories come from proxies "
      + "(US-listed ETFs with dividends reinvested, or the S&P 500 total-return index) converted at each day's "
      + "GBPUSD; the real UCITS fund is checked against its proxy where it exists. Running fetches the newest days "
      + "into the research database."),
    el("div", { class: "row" }, run), note, body);

  function start() {
    if (started) return Promise.resolve();
    started = true;
    return load();
  }

  return { node, start, reload: load };
}

export { fundsSection };
