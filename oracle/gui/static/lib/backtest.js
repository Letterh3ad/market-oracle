// The Ledgers tab's Backtest section: the strategy's fixed rules replayed over stored prices,
// run as a job, with the last few results kept to compare.
import { api } from "./api.js";
import { compareLines, lineInfos, table } from "./compare.js";
import { el } from "./dom.js";
import { money, pct, tone } from "./fmt.js";
import { repaint } from "./repaint.js";
import { tip } from "./tip.js";
import { busyButton, followJob, runJob } from "./ui.js";
import { fillPicker } from "./watchlists.js";

const ACTIVE = ["queued", "running"];

// `day` shifted by whole years, as the leans need a year of prices before they see the trend
// and the default window is capped so it does not run forever on a long-lived list.
function shiftYears(day, years) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCFullYear(d.getUTCFullYear() + years);
  return d.toISOString().slice(0, 10);
}

const DEFAULT_YEARS = 5;

// Runs kept before the momentum or regime lines have fewer lines, so each is found by name.
const RUN_LINES = ["index", "spy", "strategy", "buy_and_hold", "momentum", "regime", "regime_v2", "core_sat", "core_sat_laya", "regime_wf", "regime_ai"];

function runLabel(r) {
  const figures = RUN_LINES.map((name) => r.lines.find((l) => l.name === name)).filter((l) => l)
    .map((l) => `${l.name.replaceAll("_", " ")} ${pct(l.return)}`);
  return `${r.watchlist}, ${r.start} to ${r.end}: ${figures.join(", ")} (run ${String(r.ran_at).slice(0, 10)})`;
}

function trades(r) {
  const base = r.base_currency;
  return table([["Day"], ["Did"], ["Symbol"], ["Shares", null, 1], ["Price", null, 1], [`Cash (${base})`, null, 1],
    [`Costs (${base})`, "costs", 1], ["Why"]],
  r.trades.map((t) => el("tr", null, el("td", null, t.day),
    el("td", { class: t.side === "buy" ? "up" : "down" }, t.side), el("td", { class: "mono" }, t.symbol),
    el("td", { class: "num" }, String(t.shares)), el("td", { class: "num" }, `${money(t.price)} ${t.currency}`),
    el("td", { class: "num" }, money(t.cash)), el("td", { class: "num" }, money(t.costs)),
    el("td", { class: "small" }, t.reason))),
  "No trades: the rules never bought anything over these days.");
}

const ratio = (v) => (v == null ? "n/a" : v.toFixed(2));

// Each line per segment, then over all of them, and regime_wf's weights at each segment's start.
function walkForward(r) {
  const wf = r.walk_forward;
  const n = wf.segments.length;
  const info = lineInfos(r.lines);
  const name = (l) => info[l.name]?.label ?? l.name;
  const cell = (f) => el("td", { class: "num" }, el("span", { class: tone(f.ret) }, pct(f.ret)),
    f.excess == null ? null : el("div", { class: `small ${tone(f.excess)}` }, `${pct(f.excess)} over`));
  const perSegment = table([["Line"], ...wf.segments.map(([a, b]) => [`${a} to ${b}`, null, 1])],
    wf.lines.map((l) => el("tr", null, el("td", null, name(l)), ...l.segments.map(cell))), "No segments.");
  const overall = table([["Line"], ["Beat buy and hold", null, 1], ["Compounded", null, 1], ["Over buy and hold", null, 1],
    ["Worst segment fall", null, 1], ["Sharpe", null, 1], ["Compounded / worst segment fall", null, 1]],
  wf.lines.map((l) => el("tr", null, el("td", null, name(l)),
    el("td", { class: "num" }, l.beat == null ? "" : `${l.beat} of ${n}`),
    el("td", { class: `num ${tone(l.overall.ret)}` }, pct(l.overall.ret)),
    el("td", { class: `num ${tone(l.overall.excess)}` }, pct(l.overall.excess)),
    el("td", { class: "num" }, `${(l.overall.max_fall * 100).toFixed(1)}%`),
    el("td", { class: "num" }, ratio(l.overall.sharpe)), el("td", { class: "num" }, ratio(l.overall.per_fall)))),
  "No lines.");
  const fits = wf.fits.map(([day, presets]) => {
    const methods = Object.keys(Object.values(presets)[0] ?? {});
    return el("details", null, el("summary", null, `From ${day}`),
      table([["Mood"], ...methods.map((m) => [m, null, 1])], Object.entries(presets).map(([mood, w]) =>
        el("tr", null, el("td", null, mood), ...methods.map((m) => el("td", { class: "num" }, ratio(w[m]))))), ""));
  });
  return [
    el("h3", null, `Walk-forward: ${n} segment${n === 1 ? "" : "s"} of ${wf.months} months, each a fresh start`),
    el("p", { class: "dim small" }, "Every line starts again with the same cash and nothing held at each segment's "
      + "start, so a good early stretch never carries a later one. Under each return, how far it was over buy and "
      + "hold's in the same days. Buy and hold here re-buys equal amounts at each segment's start, so its "
      + "compounded figure is not the whole run's buy and hold; each traded line starts every segment in cash and "
      + "holds its cash floor too."),
    perSegment,
    el("h3", null, "Over all segments"),
    el("p", { class: "dim small" }, "Segment returns compounded; Sharpe from every segment's daily equity at a zero "
      + "rate, a year of 252 days; the worst segment's fall; return over that fall."),
    overall,
    el("h3", null, "Regime-wf weights fitted at each segment's start"),
    el("p", { class: "dim small" }, "Each mood's method weights, fitted from the two years of pick windows closed "
      + "before the segment began and held through it."),
    ...fits,
  ];
}

function backtestSection() {
  let started = false;
  let chart = null;
  let shown = null; // id of the run on screen
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const list = el("select", { "aria-label": "Backtest watchlist", onchange: () => loadSpan(true) });
  const from = el("input", { type: "date", "aria-label": "Backtest from" });
  const to = el("input", { type: "date", "aria-label": "Backtest to" });
  const slippage = el("input", { type: "number", min: "0", step: "any", value: "10", style: "width: 70px",
    "aria-label": "Slippage in basis points" });
  const commission = el("input", { type: "number", min: "0", step: "any", value: "0", style: "width: 80px",
    "aria-label": "Commission per trade" });
  const withAi = el("input", { type: "checkbox", "aria-label": "With the regime AI line" });
  const walk = el("input", { type: "checkbox", "aria-label": "Walk-forward" });
  const currency = el("span");
  const span = el("div", { class: "dim small" });
  const kept = el("div", { class: "row" });
  const result = el("div");

  const run = busyButton("Run backtest", async (onState) => {
    if (!from.value || !to.value) {
      say("Pick the first and last day to replay.");
      return;
    }
    const q = new URLSearchParams({ watchlist: list.value, start: from.value, end: to.value,
      slippage_bps: slippage.value || "0", commission: commission.value || "0" });
    if (walk.checked) q.set("walk_forward", "true");
    if (withAi.checked) q.set("regime_ai", "true");
    say("", false);
    try {
      const summary = await runJob(`/api/jobs/backtest?${q}`, (job) => { onState(job); running(job); });
      say("", false);
      await loadRuns(summary.id);
    } catch (err) {
      say(err.message);
    }
  });

  function running(job) {
    const ai = job?.label?.endsWith("with regime AI");
    say(el("span", null, ai
      ? `A backtest with the regime AI line is running: it asks the model for every day, hours on a local model. ${
        job.progress ? `${job.progress}. ` : ""}Follow or cancel it in the `
      : "A backtest is running: a long list over several years takes up to a minute. Follow or cancel it in the ",
    el("a", { href: "#activity" }, "Activity tab"), "."), false);
  }

  async function loadSpan(reset) {
    let s;
    try {
      s = await api(`/api/backtests/span?${new URLSearchParams({ watchlist: list.value })}`);
    } catch (err) {
      span.replaceChildren(err.message);
      return;
    }
    currency.replaceChildren(s.base_currency);
    if (!s.first) {
      span.replaceChildren(`No prices are stored for the ${s.watchlist} list yet: press Update prices on the `
        + "Watchlist tab first.");
      return;
    }
    for (const box of [from, to]) {
      box.setAttribute("min", s.first);
      box.setAttribute("max", s.last);
    }
    if (reset || !from.value || !to.value) {
      const warmed = shiftYears(s.first, 1);
      const capped = shiftYears(s.last, -DEFAULT_YEARS);
      from.value = warmed >= s.last ? s.first : capped > warmed ? capped : warmed;
      to.value = s.last;
    }
    span.replaceChildren(`Prices are stored for the ${s.watchlist} list from ${s.first} to ${s.last}. `
      + "The form starts a year in, because each day's lean needs a year of earlier prices to see the trend, "
      + `and defaults to the last ${DEFAULT_YEARS} years at most: pick an earlier day to go back further.`);
  }

  async function loadRuns(pick) {
    let body;
    try {
      body = await api("/api/backtests");
    } catch (err) {
      kept.replaceChildren(el("span", { class: "down" }, `Could not load the kept backtests: ${err.message}`));
      return;
    }
    if (!body.runs.length) {
      kept.replaceChildren(el("span", { class: "dim small" }, "No backtest kept yet: pick a list and days, then "
        + "press Run backtest."));
      return;
    }
    const id = body.runs.some((r) => r.id === pick) ? pick : body.runs[0].id;
    const picker = el("select", { "aria-label": "Kept backtests", onchange: (e) => show(Number(e.target.value)) },
      ...body.runs.map((r) => el("option", { value: String(r.id), selected: r.id === id ? "" : null }, runLabel(r))));
    kept.replaceChildren(el("span", { class: "small" }, `Kept (the last ${body.keep}):`), picker);
    await show(id);
  }

  async function show(id) {
    shown = id;
    let r;
    try {
      r = await api(`/api/backtests/${id}`);
    } catch (err) {
      result.replaceChildren(el("p", { class: "down" }, `Could not load backtest ${id}: ${err.message}`));
      return;
    }
    if (shown !== id) return; // another run was picked meanwhile
    const base = r.base_currency;
    const n = r.listings.length;
    const lines = compareLines(r.lines, base, "Too few days to draw a chart: pick a longer stretch.");
    repaint(result, [
      el("h3", null, `The ${r.watchlist} list, ${r.start} to ${r.end}`),
      el("p", { class: "dim small" }, `${n} listing${n === 1 ? "" : "s"}. Fills ${r.slippage_bps} basis points worse `
        + `than the opening price, plus ${money(r.commission)} ${base} per trade. Run ${String(r.ran_at).slice(0, 10)}.`),
      lines.node,
      ...(r.walk_forward ? walkForward(r) : []),
      el("h3", null, "Read this before trusting the numbers"),
      el("ul", { class: "small" },
        el("li", null, "This is what the rules would have done on past prices, not a forecast: markets that "
          + "rewarded them then may not now."),
        ...r.notes.map((text) => el("li", null, text))),
      el("details", null, el("summary", null, `Trades (${r.trades.length})`), trades(r)),
    ], () => {
      chart?.remove();
      chart = lines.draw();
    });
  }

  // A backtest started before this page opened is followed to its result.
  async function follow() {
    let body;
    try {
      body = await api("/api/activity");
    } catch {
      return;
    }
    const job = body.jobs.find((j) => j.kind === "backtest" && ACTIVE.includes(j.state));
    if (!job) return;
    running(job);
    try {
      const summary = await followJob(job, running);
      say("", false);
      await loadRuns(summary.id);
    } catch (err) {
      say(err.message);
    }
  }

  // Once, after the section is first on screen, so its chart has a width to fill.
  async function start() {
    if (started) return;
    started = true;
    try {
      fillPicker(list, await api("/api/watchlists"), "main");
    } catch (err) {
      say(err.message);
    }
    await Promise.all([loadSpan(false), loadRuns(null)]);
    follow();
  }

  const node = el("div", { class: "card" },
    el("h2", null, tip("Backtest", "backtest")),
    el("p", { class: "dim small" }, "Replays the strategy account's fixed rules over prices already stored, day by "
      + "day as if it had been trading then, and sets that against holding ACWI and SPY (the honest \"do nothing\", "
      + "shown first), against simply buying the same companies and holding them (today's list, so survivorship "
      + "flatters it), against holding the ones with the strongest momentum, re-picked each month, and against the regime "
      + "lines: a blend of known methods weighted by the market's mood, and regime-v2, the same blend without the "
      + "hold padding and with a rotation out of long-held, clearly weaker holdings, and core-sat: an equal-weight "
      + "buy and hold bought once and never rebalanced, its regime satellite at weight 0 until a signal earns it, "
      + "and core-sat-laya: the same with "
      + "Laya's daily news score in the satellite at the weight its kept predictive test gives (0 unless it passed, "
      + "so then it is core-sat exactly; each caption names the inputs "
      + "it had). Fake "
      + "money: nothing in your accounts changes."),
    el("div", { class: "row" }, "List ", list, " from ", from, " to ", to),
    el("div", { class: "row" }, tip("Slippage", "slippage"), slippage, " basis points, ",
      tip("commission", "commission"), commission, " ", currency, " per trade ", run),
    el("div", { class: "row" }, el("label", { class: "small",
      title: "Adds a few minutes: every line again over six-month segments, and the regime_wf line" }, walk,
    " Walk-forward too: judge every line over back-to-back six-month segments, each a fresh start, and add "
    + "regime-wf, the regime blend with its mood weights refitted at each segment's start")),
    el("div", { class: "row small" }, el("label", null, withAi, " Add the regime AI line: the regime line with the "
      + "live model tilt, asked for every day with no date in the prompt. One model call per day: hours on a local "
      + "model, so only when ticked. It waits while a tick, research, a question or discovery uses the model.")),
    note, span, kept, result);
  return { node, start };
}

export { backtestSection };
