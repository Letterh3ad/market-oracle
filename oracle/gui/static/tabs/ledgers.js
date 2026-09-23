import { api } from "../lib/api.js";
import { lineChart } from "../lib/chart.js";
import { el, showError, companyLink } from "../lib/dom.js";
import { tip } from "../lib/tip.js";

let charts = [];

const money = (v) => (v == null ? "n/a" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const signed = (v) => `${v >= 0 ? "+" : ""}${money(v)}`;
const pct = (v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");

function metrics(a, base) {
  const row = (label, key, value, cls = "") => el("tr", null,
    el("td", null, tip(label, key)), el("td", { class: `num ${cls}` }, value));
  return el("table", { class: "results metrics" }, el("tbody", null,
    row(`Equity (${base})`, "equity", money(a.equity)),
    row("Return since start", "equity", pct(a.return), tone(a.return)),
    row("Cash", "equity", money(a.cash)),
    row("Realised", "realised", signed(a.realised), tone(a.realised)),
    row("Unrealised", "unrealised", signed(a.unrealised), tone(a.unrealised)),
    row("Costs paid", "costs", money(a.costs)),
    row("Largest fall", "max_fall", pct(a.max_fall), a.max_fall < 0 ? "down" : "")));
}

function table(headers, rows, empty) {
  if (!rows.length) return el("p", { class: "dim small" }, empty);
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, ...headers.map(([h, num]) => el("th", { class: num ? "num" : null }, h)))),
    el("tbody", null, ...rows));
}

function column(a, base) {
  const unrealised = (p) => (p.value == null ? null : p.value - p.cost);
  const positions = table([["Symbol"], ["Shares", 1], [`Cost (${base})`, 1], [`Value (${base})`, 1], ["Gain", 1]],
    a.positions.map((p) => el("tr", null, el("td", null, companyLink(p.symbol)), el("td", { class: "num" }, String(p.shares)),
      el("td", { class: "num" }, money(p.cost)), el("td", { class: "num" }, p.value == null ? "no rate yet" : money(p.value)),
      el("td", { class: `num ${tone(unrealised(p))}` }, unrealised(p) == null ? "" : signed(unrealised(p))))),
    "No positions.");
  const pending = table([["Order"], ["Placed"], ["Note"]],
    a.pending.map((o) => el("tr", null,
      el("td", null, `${o.side} `, companyLink(o.symbol), o.cash != null ? ` for ${money(o.cash)} ${base}`
        : o.shares != null ? ` ${o.shares} shares` : " all shares"),
      el("td", null, o.placed_on), el("td", { class: "dim small" }, o.note ?? ""))),
    "No orders waiting.");
  const sold = table([["Sold"], ["Symbol"], ["Shares", 1], [`Proceeds (${base})`, 1], ["Realised", 1]],
    a.sells.map((s) => el("tr", null, el("td", null, s.date), el("td", null, companyLink(s.symbol)),
      el("td", { class: "num" }, String(s.shares)), el("td", { class: "num" }, money(s.proceeds)),
      el("td", { class: `num ${tone(s.realised)}` }, signed(s.realised)))),
    "Nothing sold yet.");
  const chart = el("div", { class: "equity-chart", dataset: { account: a.name } });
  return el("div", { class: "card ledger" },
    el("h2", null, tip(a.name === "strategy" ? "Strategy" : "Manual", `${a.name}_account`)),
    metrics(a, base), chart,
    el("h3", null, tip("Positions", "unrealised")), positions,
    el("h3", null, tip("Waiting", "pending")), pending,
    el("h3", null, tip("Sells", "realised")), sold);
}

function drawCharts(pane, accounts) {
  for (const c of charts) c.remove();
  charts = [];
  for (const a of accounts) {
    const box = pane.querySelector(`.equity-chart[data-account="${a.name}"]`);
    if (!box) continue;
    if (a.history.length < 2) {
      box.replaceChildren(el("p", { class: "dim small" },
        "The equity chart starts once fills have run on two different days (Update prices runs them)."));
      continue;
    }
    const chart = lineChart(box, a.history.map(([day, equity]) => ({ time: day, value: equity })), 160);
    if (chart) charts.push(chart);
  }
}

function decisions(rows) {
  return table([["Day"], ["Symbol"], ["Did"], ["Why"]],
    rows.map((d) => el("tr", null, el("td", null, d.day), el("td", null, companyLink(d.symbol)),
      el("td", { class: d.action === "buy" ? "up" : d.action === "sell" ? "down" : "dim" },
        d.action + (d.cash != null ? ` ${money(d.cash)}` : "")),
      el("td", { class: "small" }, d.reason))),
    "The strategy has not run yet. It runs with `oracle paper step` (the Strategy step button comes with trading).");
}

async function render(pane) {
  const holder = el("div", null, el("p", { class: "dim" }, "Loading..."));
  pane.replaceChildren(holder);
  let body, log;
  try {
    [body, log] = await Promise.all([api("/api/ledgers"), api("/api/ledgers/decisions")]);
  } catch (err) {
    showError(err.message);
    return;
  }
  if (!pane.contains(holder)) return; // another tab owns the pane now
  holder.replaceChildren(
    el("div", { class: "row ledgers" }, ...body.accounts.map((a) => column(a, body.base_currency))),
    el("h3", null, "Strategy decisions"), decisions(log));
  drawCharts(pane, body.accounts);
}

export default { id: "ledgers", label: "Ledgers", render };
