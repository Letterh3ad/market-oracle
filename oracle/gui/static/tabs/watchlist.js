import { api } from "../lib/api.js";
import { el, showError, clearError, companyLink } from "../lib/dom.js";
import { tip } from "../lib/tip.js";

const MARK = { bullish: "+", bearish: "-", neutral: "=" };
const SHORT = { technicals: "tech", volatility: "vol", quality: "qual", growth: "grow" };
let list = "main";

async function load(pane) {
  let body, lists;
  try {
    [body, lists] = await Promise.all([api(`/api/watchlists/${encodeURIComponent(list)}`), api("/api/watchlists")]);
  } catch (err) {
    showError(err.message);
    return;
  }
  if (!pane.querySelector("#w-table")) return; // another tab owns the pane now
  const picker = pane.querySelector("#w-list");
  const names = lists.map((w) => w.name);
  if (!names.includes(list)) names.unshift(list);
  picker.replaceChildren(...names.map((n) => el("option", { value: n, selected: n === list ? "" : null }, n)));
  pane.querySelector("#w-table").replaceChildren(table(body, pane));
  pane.querySelector("#w-credits").replaceChildren(...body.credits.map((c) => el("p", { class: "dim small" }, c)));
}

function table(body, pane) {
  if (!body.items.length) {
    return el("p", { class: "dim" }, `${list} is empty: add listings from the Universe tab.`);
  }
  return el("table", { class: "results" },
    el("thead", null, el("tr", null,
      el("th", null, "Symbol"), el("th", null, "Name"),
      el("th", { class: "num" }, tip("Last close", "last_close")),
      el("th", { class: "num" }, tip(`In ${body.base_currency}`, "in_base")),
      el("th", { class: "num" }, tip("Day", "day_change")), el("th", null, "Date"),
      el("th", null, tip("Signals", "signals")), el("th", null, tip("Lean", "lean")), el("th", null, ""))),
    el("tbody", null, ...body.items.map((i) => row(i, pane))));
}

function row(i, pane) {
  const money = (v) => (v == null ? "" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const day = i.day_change == null ? "" : `${i.day_change >= 0 ? "+" : ""}${(i.day_change * 100).toFixed(2)}%`;
  const remove = el("button", { type: "button", "aria-label": `Stop watching ${i.symbol}`, onclick: async () => {
    try {
      await api(`/api/watchlists/${encodeURIComponent(list)}/items/${encodeURIComponent(i.symbol)}`, { method: "DELETE" });
      load(pane);
    } catch (err) { showError(err.message); }
  } }, "Remove");
  return el("tr", { class: i.missing || i.delisted ? "delisted" : null },
    el("td", null, companyLink(i.symbol), el("div", { class: "dim small" }, i.exchange ?? "")),
    el("td", { class: "name" }, i.name ?? "", i.missing ? " (gone from the universe)" : i.delisted ? " (delisted)" : null),
    el("td", { class: "num" }, i.last_close == null ? "" : `${money(i.last_close)} ${i.price_currency}`),
    el("td", { class: "num" }, money(i.close_base)),
    el("td", { class: `num ${i.day_change > 0 ? "up" : i.day_change < 0 ? "down" : ""}` }, day),
    el("td", null, i.last_date ?? "", i.stale ? el("span", { class: "badge warn" }, "stale ", tip("", "stale_price")) : null),
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
  const button = el("button", { type: "button", onclick: async () => {
    button.disabled = true;
    button.textContent = `${label}: running...`;
    const out = pane.querySelector("#w-result");
    try {
      const result = await api(path, { method: "POST" });
      clearError();
      out.replaceChildren(...summarise(result).map((line) => el("div", null, line)));
    } catch (err) {
      showError(err.message);
    } finally {
      button.disabled = false;
      button.textContent = label;
      if (pane.querySelector("#w-table")) load(pane);
    }
  } }, label);
  return button;
}

function pricesSummary(r) {
  if (!r.listings.length) return ["Nothing watched yet."];
  return [
    ...r.listings.map((o) => (o.error ? `${o.symbol}: FAILED (${o.error})` : `${o.symbol}: ${o.added} new days (from ${o.source})`)),
    ...r.fx.map((f) => (f.source == null ? `${f.currency}: no rates (${f.error})` : `${f.currency}: ${f.added} new rates from ${f.source}`)),
    ...r.fills.map((f) => `${f.symbol}: paper ${f.side} ${f.status}${f.note ? ` (${f.note})` : ""}`),
  ];
}

function analyseSummary(r) {
  if (!r.listings.length) return ["Nothing watched yet."];
  return r.listings.map((o) => (o.error ? `${o.symbol}: FAILED (${o.error})`
    : `${o.symbol}: ${o.lean ? `baseline lean ${o.lean.label} ${Math.round(o.lean.probability * 100)}%` : "no lean (needs a year of prices)"}`));
}

function render(pane) {
  const picker = el("select", { id: "w-list", "aria-label": "Watchlist", onchange: (e) => { list = e.target.value; load(pane); } });
  pane.replaceChildren(
    el("div", { class: "row" }, el("span", { class: "dim" }, "List"), picker,
      jobButton("Update prices", "/api/jobs/prices", pricesSummary, pane),
      jobButton("Analyse", "/api/jobs/analyse", analyseSummary, pane),
      el("span", { class: "dim small" }, "Both run for every watched listing, on all lists.")),
    el("div", { id: "w-result", class: "result" }),
    el("div", { id: "w-table" }), el("div", { id: "w-credits" }));
  load(pane);
}

export default { id: "watchlist", label: "Watchlist", render };
