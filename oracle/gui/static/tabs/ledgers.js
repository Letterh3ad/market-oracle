import { api } from "../lib/api.js";
import { backtestSection } from "../lib/backtest.js";
import { lineChart } from "../lib/chart.js";
import { compareLines, table } from "../lib/compare.js";
import { el, showError, clearError, companyLink } from "../lib/dom.js";
import { money, pct, signed, tone } from "../lib/fmt.js";
import { researchLive } from "../lib/ledger_research.js";
import { repaint } from "../lib/repaint.js";
import { unvalidatedBadge } from "../lib/research.js";
import { tip, tipText } from "../lib/tip.js";
import { busyButton, sendJson } from "../lib/ui.js";

let charts = [];
let pane = null;
let root = null; // this tab's container: a late answer repaints only while it is on screen
let loads = 0; // only the newest load paints
let live = null; // this render's research buttons and their poll
let backtest = null; // this render's Backtest section: one node kept across repaints, so its form keeps what was typed
let timer = null;
let lastResult = []; // lines from the last button press, shown once on the reload it triggers
let resultBox = null;
// Research presses change only their buttons, so their lines go straight into the result box.
const say = (lines) => resultBox?.replaceChildren(...lines.map((line) => el("div", null, line)));

function metrics(a, base) {
  const row = (label, key, value, cls = "") => el("tr", null,
    el("td", null, tip(label, key)), el("td", { class: `num ${cls}` }, value));
  return el("table", { class: "results metrics" }, el("tbody", null,
    row(`Equity (${base})`, "equity", money(a.equity)),
    row("Return since start", "return", pct(a.return), tone(a.return)),
    row("Cash", "cash", money(a.cash)),
    row("Realised", "realised", signed(a.realised), tone(a.realised)),
    row("Unrealised", "unrealised", signed(a.unrealised), tone(a.unrealised)),
    row("Costs paid", "costs", money(a.costs)),
    row("Largest fall", "max_fall", pct(a.max_fall), a.max_fall < 0 ? "down" : "")));
}

function strategyRules(r) {
  const p = (v) => `${Math.round(v * 100)}%`;
  return `Trades by itself on the leans, under fixed rules: buy on a buy lean at ${p(r.min_probability)} or more, ` +
    `at most ${r.max_positions} positions of up to ${p(r.max_position)} each, ${p(r.cash_floor)} always kept in cash, ` +
    `sell on a sell lean or ${p(r.stop_loss)} below cost. Its record is the honest test. Change the rules in oracle.toml.`;
}

function aiRules(r, ai) {
  const p = (v) => `${Math.round(v * 100)}%`;
  return "Trades by itself on each watched listing's latest research verdict, under the strategy's rules and limits: " +
    `buy on a buy verdict at ${p(r.min_probability)} confidence or more, sell on a sell verdict or ${p(r.stop_loss)} below cost. ` +
    `A verdict older than ${ai.max_verdict_age_days} days is never traded. While it is on, each tick first researches ` +
    "the listings whose report is missing, too old or behind a price move, then runs the AI step. " +
    "Unvalidated: its record is how we find out whether the research adds anything.";
}

// How ticks keep the research fresh: saved to oracle.toml, used from the next tick.
function aiResearch(ai) {
  const note = el("div", { class: "form-note" });
  const field = (label, value, attrs) => el("input", { type: "number", step: "1", value: String(value),
    style: "width: 60px", "aria-label": label, ...attrs });
  const age = field("Research again after days", ai.max_verdict_age_days, { min: "1", max: "90" });
  const move = field("Research again after a price move of percent", +(ai.move_threshold * 100).toFixed(2),
    { min: "0.1", max: "99", step: "any" });
  const budget = field("Research minutes per tick", ai.research_budget_min, { min: "1", max: "480" });
  const save = busyButton("Save", async () => {
    try {
      await sendJson("/api/ledgers/ai/research", { max_verdict_age_days: Number(age.value),
        move_threshold: Number(move.value) / 100, research_budget_min: Number(budget.value) }, "PUT");
      lastResult = ["Saved: the next tick researches by the new limits."];
      refresh();
    } catch (err) {
      note.className = "form-note down";
      note.textContent = err.message;
    }
  });
  return el("div", { class: "small" },
    el("div", { class: "row" }, "Research again after ", age, " days or a ", move, "% price move; up to ", budget,
      " min per tick ", save), note);
}

// The AI account's on/off switch; off, its step does nothing.
function aiSwitch(ai) {
  const box = el("input", { type: "checkbox", "aria-label": "AI account on" });
  box.checked = ai.enabled;
  box.addEventListener("change", async () => {
    box.disabled = true;
    try {
      await sendJson("/api/ledgers/ai", { enabled: box.checked }, "PUT");
      clearError();
    } catch (err) {
      showError(`AI account switch: ${err.message}`);
    }
    refresh();
  });
  return el("label", { class: ai.enabled ? "small" : "small warn-text" }, box,
    ai.enabled ? " On: AI step trades the verdicts" : " Off: AI step does nothing");
}

// Buy and sell forms for the manual account. Refusals appear under the form that caused them.
function trading(base) {
  const note = el("div", { class: "form-note" });
  const say = (text) => { note.className = "form-note down"; note.textContent = text; };
  const buySymbol = el("input", { placeholder: "symbol", size: "10", "aria-label": "Buy symbol" });
  const amount = el("input", { type: "number", min: "0", step: "any", placeholder: `amount in ${base}`, style: "width: 130px",
    "aria-label": "Buy amount" });
  const sellSymbol = el("input", { id: "sell-symbol", placeholder: "symbol", size: "10", "aria-label": "Sell symbol" });
  const shares = el("input", { id: "sell-shares", type: "number", min: "1", step: "1", placeholder: "shares (blank: all)",
    style: "width: 140px", "aria-label": "Shares to sell" });
  const buy = busyButton("Buy", async () => {
    try {
      const o = await sendJson("/api/ledgers/manual/buy", { symbol: buySymbol.value.trim(), amount: Number(amount.value) });
      lastResult = [`Queued: buy ${o.symbol} for ${money(o.cash)} ${base}. It fills at the next opening price.`];
      refresh();
    } catch (err) { say(err.message); }
  });
  const sell = busyButton("Sell", async () => {
    try {
      const n = shares.value.trim() ? Number(shares.value) : null;
      const o = await sendJson("/api/ledgers/manual/sell", { symbol: sellSymbol.value.trim(), shares: n });
      lastResult = [`Queued: sell ${o.shares ?? "all"} ${o.symbol} shares. It fills at the next opening price.`];
      refresh();
    } catch (err) { say(err.message); }
  });
  return el("div", { class: "trade" },
    el("div", { class: "row" }, buySymbol, amount, buy),
    el("div", { class: "row" }, sellSymbol, shares, sell), note);
}

function sellButton(p) {
  return el("button", { type: "button", class: "small-btn", onclick: () => {
    root.querySelector("#sell-symbol").value = p.symbol;
    root.querySelector("#sell-shares").value = "";
    root.querySelector("#sell-shares").focus();
  } }, "Sell");
}

function column(a, base, rules, ai, stale) {
  const manual = a.name === "manual";
  const gain = (p) => (p.value == null ? null : p.value - p.cost);
  const heads = [["Symbol"], ["Shares", null, 1], [`Cost (${base})`, "position_cost", 1],
    [`Value (${base})`, "position_value", 1], ["Gain", "unrealised", 1]];
  const positions = table(manual ? [...heads, [""]] : heads,
    a.positions.map((p) => el("tr", null, el("td", null, companyLink(p.symbol)), el("td", { class: "num" }, String(p.shares)),
      el("td", { class: "num" }, money(p.cost)), el("td", { class: "num" }, p.value == null ? "no rate yet" : money(p.value)),
      el("td", { class: `num ${tone(gain(p))}` }, gain(p) == null ? "" : signed(gain(p))),
      manual ? el("td", null, sellButton(p)) : null)),
    "No positions.");
  const pending = table([["Order"], ["Placed"], ["Note"]],
    a.pending.map((o) => el("tr", null,
      el("td", null, `${o.side} `, companyLink(o.symbol), o.cash != null ? ` for ${money(o.cash)} ${base}`
        : o.shares != null ? ` ${o.shares} shares` : " all shares"),
      el("td", null, o.placed_on), el("td", { class: "dim small" }, o.note ?? ""))),
    "No orders waiting.");
  const sold = table([["Sold"], ["Symbol"], ["Shares", null, 1], [`Proceeds (${base})`, "proceeds", 1], ["Realised", "realised", 1]],
    a.sells.map((s) => el("tr", null, el("td", null, s.date), el("td", null, companyLink(s.symbol)),
      el("td", { class: "num" }, String(s.shares)), el("td", { class: "num" }, money(s.proceeds)),
      el("td", { class: `num ${tone(s.realised)}` }, signed(s.realised)))),
    "Nothing sold yet.");
  const title = manual ? tip("Manual", "manual_account")
    : a.name === "ai" ? tipText("AI", aiRules(rules, ai)) : tipText("Strategy", strategyRules(rules));
  const badge = a.name === "ai" ? unvalidatedBadge() : null;
  return el("div", { class: "card ledger" },
    el("h2", null, title, badge ? " " : null, badge), a.name === "ai" ? aiSwitch(ai) : null,
    a.name === "ai" ? aiResearch(ai) : null, a.name === "ai" ? live.staleButton(stale, say) : null,
    metrics(a, base), manual ? trading(base) : null,
    el("div", { class: "equity-chart", dataset: { account: a.name } }),
    el("h3", null, tip("Positions", "unrealised")), positions,
    el("h3", null, tip("Waiting", "pending")), pending,
    el("h3", null, tip("Sells", "realised")), sold);
}

// Every account and buy-and-hold, each as its return since the AI account started.
function comparison(c, base) {
  const lines = compareLines(c.lines, base,
    "The comparison chart starts once the AI account has equity on two different days (Update prices records it).");
  const card = el("div", { class: "card" },
    el("h2", null, "Since the AI account started"),
    ...c.notes.map((n) => el("p", { class: "dim small" }, n)),
    lines.node);
  return { card, draw: lines.draw };
}

function drawCharts(accounts) {
  for (const c of charts) c.remove();
  charts = [];
  for (const a of accounts) {
    const box = root.querySelector(`.equity-chart[data-account="${a.name}"]`);
    if (a.history.length < 2) {
      box.replaceChildren(el("p", { class: "dim small" },
        "The equity chart starts once fills have run on two different days (Update prices runs them)."));
      continue;
    }
    const chart = lineChart(box, a.history.map(([day, equity]) => ({ time: day, value: equity })), 160);
    if (chart) charts.push(chart);
  }
}

// `due`: listings still stale now, so an old line whose listing was researched since offers no button.
function decisions(rows, base, empty, due = new Set()) {
  const offer = (d) => d.needs_research && due.has(d.symbol);
  return table([["Day"], ["Symbol"], ["Did"], ["Why"]],
    rows.map((d) => el("tr", null, el("td", null, d.day), el("td", null, companyLink(d.symbol)),
      el("td", { class: d.action === "buy" ? "up" : d.action === "sell" ? "down" : "dim" },
        d.action + (d.cash != null ? ` ${money(d.cash)} ${base}` : "")),
      el("td", { class: "small" }, d.reason,
        d.report_id != null ? el("span", { class: "dim" }, ` (research run ${d.report_id})`) : null,
        offer(d) ? " " : null, offer(d) ? live.researchButton(d.symbol, say) : null))),
    empty);
}

function jobs(base) {
  const out = el("div", { class: "result" }, ...lastResult.map((line) => el("div", null, line)));
  resultBox = out;
  lastResult = [];
  const run = (label, path, lines) => busyButton(label, async () => {
    try {
      lastResult = lines(await sendJson(path));
      clearError();
    } catch (err) {
      showError(err.message);
    }
    refresh();
  });
  const fillLines = (r) => (r.length ? r.map((o) => `${o.account}: ${o.side} ${o.symbol} ${o.status}${o.note ? ` (${o.note})` : ""}`)
    : ["No orders waiting."]);
  const stepLines = (account, r, empty) => (r.length
    ? r.map((d) => `${account}: ${d.action} ${d.symbol}${d.cash != null ? ` for ${money(d.cash)} ${base}` : ""} (${d.reason})`)
    : [empty]);
  const strategyLines = (r) => stepLines("strategy", r, "Nothing to decide: watch listings and analyse them first.");
  const aiLines = (r) => (r.off ? [r.off] : [r.badge[0].toUpperCase() + r.badge.slice(1),
    ...stepLines("ai", r.decisions, "Nothing to decide: watch listings and research them first.")]);
  return el("div", null,
    el("div", { class: "row" }, run("Fill now", "/api/ledgers/fill", fillLines),
      run("Strategy step", "/api/ledgers/step", strategyLines), run("AI step", "/api/ledgers/ai/step", aiLines),
      el("span", { class: "dim small" }, "Fill now runs waiting orders whose next opening price is stored. "
        + "Strategy step lets the strategy account act on its leans; AI step lets the AI account act on the research verdicts.")),
    out);
}

// Repaints the tab in place after an action, unless the user has moved to another tab meanwhile.
function refresh() {
  if (root && pane.contains(root)) load(root);
}

async function load(mine) {
  const asked = ++loads;
  let body, log, aiLog, stale;
  try {
    [body, log, aiLog, stale] = await Promise.all([api("/api/ledgers"), api("/api/ledgers/decisions"),
      api("/api/ledgers/decisions?account=ai"), api("/api/ledgers/ai/stale")]);
  } catch (err) {
    if (asked !== loads || !pane.contains(mine)) return;
    if (mine.dataset.painted) showError(`Could not reload the ledgers: ${err.message}`);
    else mine.replaceChildren(el("p", { class: "dim" }, `Could not load the ledgers: ${err.message}`));
    return;
  }
  if (asked !== loads || !pane.contains(mine)) return; // another tab, or a newer load, owns the pane now
  mine.dataset.painted = "yes";
  const base = body.base_currency;
  const compared = comparison(body.compare, base);
  repaint(mine, [
    jobs(base),
    compared.card,
    el("div", { class: "row ledgers" }, ...body.accounts.map((a) => column(a, base, body.rules, body.ai_account, stale))),
    ...body.credits.map((c) => el("p", { class: "dim small" }, c)),
    el("h3", null, "Strategy decisions"), decisions(log, base, "The strategy has not run yet: press Strategy step."),
    el("h3", null, "AI decisions ", unvalidatedBadge()), decisions(aiLog, base, body.ai_account.enabled
      ? "The AI account has not run yet: press AI step." : "The AI account is off: turn it on above its figures.",
      new Set(stale.due)),
    backtest.node,
  ], () => {
    drawCharts(body.accounts);
    const chart = compared.draw();
    if (chart) charts.push(chart);
    backtest.start();
  });
}

// The first load shows "Loading..."; the research buttons follow their jobs while the tab is on screen.
function render(target) {
  pane = target;
  root = el("div", null, el("p", { class: "dim" }, "Loading..."));
  pane.replaceChildren(root);
  live = researchLive();
  backtest = backtestSection();
  clearInterval(timer);
  timer = live.start(root);
  return load(root);
}

export default { id: "ledgers", label: "Ledgers", render };
