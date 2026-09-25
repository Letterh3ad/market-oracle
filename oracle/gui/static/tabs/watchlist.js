import { api } from "../lib/api.js";
import { el, showError, clearError, companyLink } from "../lib/dom.js";
import { money, tone } from "../lib/fmt.js";
import { tip } from "../lib/tip.js";
import { busyButton, runJob, sendJson } from "../lib/ui.js";
import { deadNote, swapSummary } from "../lib/siblings.js";
import { rank, sortByRank } from "../lib/factors.js";
import { repaint } from "../lib/repaint.js";
import { fillPicker } from "../lib/watchlists.js";
import { discoverySection } from "../lib/discovery.js";

const MARK = { bullish: "+", bearish: "-", neutral: "=" };
const SHORT = { technicals: "tech", volatility: "vol", quality: "qual", growth: "grow" };
let list = "main";
let seq = 0;
let asOf = ""; // the factors' day; blank is today
let sortKey = null; // a factor rank column, or null for the list's own order
let descending = true;
let shown = null; // the last load's { body, factors }, re-sorted without refetching

async function load(pane) {
  const mine = ++seq;
  let body, lists, factors;
  const factorsPath = `/api/factors?list=${encodeURIComponent(list)}${asOf ? `&as_of=${asOf}` : ""}`;
  try {
    [body, lists, factors] = await Promise.all([api(`/api/watchlists/${encodeURIComponent(list)}`), api("/api/watchlists"),
      api(factorsPath).catch((err) => ({ error: err.message, rows: [] }))]);
  } catch (err) {
    showError(err.message);
    return;
  }
  // A newer load (another list picked) answered already, or another tab owns the pane.
  if (mine !== seq || !pane.querySelector("#w-table")) return;
  fillPicker(pane.querySelector("#w-list"), lists, list);
  shown = { body, factors };
  repaint(pane.querySelector("#w-table"), [table(body, pane, factors)]);
  pane.querySelector("#w-credits").replaceChildren(...body.credits.map((c) => el("p", { class: "dim small" }, c)));
}

function sortHeader(label, key, glossary, pane) {
  const mark = sortKey === key ? (descending ? " v" : " ^") : "";
  return el("th", { class: "num" }, el("button", { type: "button", "aria-label": `Sort by ${label}`,
    onclick: () => {
      descending = sortKey === key ? !descending : true;
      sortKey = key;
      repaint(pane.querySelector("#w-table"), [table(shown.body, pane, shown.factors)]);
    } }, `${label}${mark}`), tip("", glossary));
}

function factorCaption(f) {
  if (f.error) return el("p", { class: "dim small" }, `Price factors: ${f.error}`);
  return el("p", { class: "dim small" }, `Momentum and calm ranked across ${f.set_size} watched, held and discovered ` +
    `listings as of ${f.as_of} (${f.ranked} with a year of prices; an earlier day still ranks today's set). ` +
    "For reading only: research and the verdicts do not use them.");
}

function table(body, pane, factors) {
  if (!body.items.length) {
    return el("p", { class: "dim" }, `${list} is empty: add listings from the Universe tab.`);
  }
  const bySymbol = Object.fromEntries(factors.rows.map((r) => [r.symbol, r]));
  const items = sortKey ? sortByRank(body.items, bySymbol, sortKey, descending) : body.items;
  return el("div", null, el("table", { class: "results" },
    el("thead", null, el("tr", null,
      el("th", null, "Symbol"), el("th", null, "Name"),
      el("th", { class: "num" }, tip("Last close", "last_close")),
      el("th", { class: "num" }, tip(`In ${body.base_currency}`, "in_base")),
      el("th", { class: "num" }, tip("Day", "day_change")), el("th", null, "Date"),
      sortHeader("Momentum", "momentum_pct", "momentum_rank", pane), sortHeader("Calm", "low_vol_pct", "calm_rank", pane),
      el("th", null, tip("Signals", "signals")), el("th", null, tip("Lean", "lean")), el("th", null, ""))),
    el("tbody", null, ...items.map((i) => row(i, pane, bySymbol[i.symbol])))), factorCaption(factors));
}

function factorCell(f, key, figure) {
  if (!f) return el("td", null, "");
  return el("td", { class: "num", title: f.note ?? figure(f) }, f[key] == null ? "-" : rank(f[key]));
}

function row(i, pane, f) {
  const day = i.day_change == null ? "" : `${i.day_change >= 0 ? "+" : ""}${(i.day_change * 100).toFixed(2)}%`;
  const remove = el("button", { type: "button", "aria-label": `Stop watching ${i.symbol}`, onclick: async () => {
    try {
      await api(`/api/watchlists/${encodeURIComponent(list)}/items/${encodeURIComponent(i.symbol)}`, { method: "DELETE" });
      load(pane);
    } catch (err) { showError(err.message); }
  } }, "Remove");
  return el("tr", { class: i.missing || i.delisted ? "delisted" : null },
    el("td", null, companyLink(i.symbol), el("div", { class: "dim small" }, i.exchange ?? "")),
    el("td", { class: "name" }, i.name ?? "", i.missing ? " (gone from the universe)" : i.delisted ? " (delisted)" : null,
      deadNote(i.symbol, i.dead_since, i.siblings, true, (r) => {
        pane.querySelector("#w-result")?.replaceChildren(el("div", null, swapSummary(r)));
        load(pane);
      })),
    el("td", { class: "num" }, i.last_close == null ? "" : `${money(i.last_close)} ${i.price_currency}`),
    el("td", { class: "num" }, i.close_base == null ? "" : money(i.close_base)),
    el("td", { class: `num ${tone(i.day_change)}` }, day),
    el("td", null, i.last_date ?? "", i.stale ? el("span", { class: "badge warn" }, "stale ", tip("", "stale_price")) : null),
    factorCell(f, "momentum_pct", (x) => `12-1 return ${(x.momentum * 100).toFixed(1)}%`),
    factorCell(f, "low_vol_pct", (x) => `60-day volatility ${(x.volatility * 100).toFixed(1)}% a year`),
    el("td", { class: "mono sigs" }, ...i.signals.map((s) =>
      el("span", { class: `sig ${s.direction}${s.stale ? " faded" : ""}`, title: `${s.analyst}: ${s.direction}${s.stale ? ", out of date" : ""}` },
        `${SHORT[s.analyst] ?? s.analyst}${MARK[s.direction]}`))),
    el("td", null, lean(i.lean), i.next ? el("div", { class: "dim small" }, `needs: ${i.next}`) : null),
    el("td", null, remove));
}

function lean(l) {
  if (!l) return "";
  const cls = l.label.includes("buy") ? "up" : l.label.includes("sell") ? "down" : "";
  return el("span", { class: l.stale ? "faded" : null },
    el("span", { class: cls }, `${l.label} ${Math.round(l.probability * 100)}%`),
    el("span", { class: "badge" }, l.source),
    l.stale ? el("span", { class: "badge warn" }, "out of date ", tip("", "lean_stale")) : null);
}

function jobButton(label, path, summarise, pane) {
  return busyButton(label, async (onState) => {
    try {
      const result = await runJob(path, onState);
      clearError();
      pane.querySelector("#w-result")?.replaceChildren(...summarise(result).map((line) => el("div", null, line)));
    } catch (err) {
      showError(err.message);
    }
    if (pane.querySelector("#w-table")) load(pane);
  });
}

function pricesSummary(r) {
  if (!r.listings.length) return ["Nothing watched yet."];
  return [
    ...r.listings.map((o) => (o.skipped ? `${o.symbol}: skipped, ${o.skipped} (Check again on its page)`
      : o.error ? `${o.symbol}: FAILED (${o.error})` : `${o.symbol}: ${o.added} new days (from ${o.source})`)),
    ...r.fx.map((f) => (f.source == null ? `${f.currency}: no rates (${f.error})` : `${f.currency}: ${f.added} new rates from ${f.source}`)),
    ...r.fills.map((f) => `${f.symbol}: paper ${f.side} ${f.status}${f.note ? ` (${f.note})` : ""}`),
  ];
}

function analyseSummary(r) {
  if (!r.listings.length) return ["Nothing watched yet."];
  return r.listings.map((o) => (o.error ? `${o.symbol}: FAILED (${o.error})`
    : `${o.symbol}: ${o.lean ? `baseline lean ${o.lean.label} ${Math.round(o.lean.probability * 100)}%` : "no lean (needs a year of prices)"}`));
}

// Creates the list (or finds it, named without case) and switches to it.
function newList(pane) {
  const name = el("input", { placeholder: "new list", size: "12", "aria-label": "New watchlist name", maxlength: "40" });
  const create = async () => {
    try {
      clearError();
      list = (await sendJson("/api/watchlists", { name: name.value })).name;
      name.value = "";
      load(pane);
    } catch (err) { showError(err.message); }
  };
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") create(); });
  return [name, el("button", { type: "button", onclick: create }, "Create")];
}

function render(pane) {
  const picker = el("select", { id: "w-list", "aria-label": "Watchlist", onchange: (e) => { list = e.target.value; load(pane); } });
  pane.replaceChildren(
    el("div", { class: "row" }, el("span", { class: "dim" }, "List"), picker, ...newList(pane),
      jobButton("Update prices", "/api/jobs/prices", pricesSummary, pane),
      jobButton("Analyse", "/api/jobs/analyse", analyseSummary, pane),
      el("span", { class: "dim small" }, "Both run for every watched listing, on all lists."),
      el("span", { class: "dim" }, "Factors as of"),
      el("input", { type: "date", value: asOf, max: new Date().toLocaleDateString("en-CA"), "aria-label": "Factors as of", onchange: (e) => { asOf = e.target.value; load(pane); } })),
    el("div", { id: "w-result", class: "result" }),
    el("div", { id: "w-table" }), el("div", { id: "w-credits" }),
    discoverySection(() => { if (pane.querySelector("#w-table")) load(pane); }));
  load(pane);
}

export default { id: "watchlist", label: "Watchlist", render };
