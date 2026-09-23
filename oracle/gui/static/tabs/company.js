import { api } from "../lib/api.js";
import { lineChart } from "../lib/chart.js";
import { el, showError, clearError, companyLink, safeUrl } from "../lib/dom.js";
import { big, money } from "../lib/fmt.js";
import { researchSection } from "../lib/research.js";
import { tip, tipText } from "../lib/tip.js";
import { busyButton, runJob, sendJson } from "../lib/ui.js";

const LABELS = {
  technicals: "Technicals (combined)", trend: "Trend", rsi: "RSI", macd: "MACD", volatility: "Volatility",
  risk: "Risk", value: "Value", quality: "Quality", growth: "Growth",
};
let current = null; // the symbol shown last, reopened when the tab is picked without one
let chart = null;
let seq = 0;

function figure(value, kind, currency) {
  if (value == null) return "n/a";
  if (kind === "pct") return `${(value * 100).toFixed(2)}%`;
  if (kind === "money") return big(value, currency);
  return value.toFixed(2);
}

function job(label, path, pane, symbol) {
  return busyButton(label, async (onState) => {
    try {
      const r = await runJob(`${path}?symbol=${encodeURIComponent(symbol)}`, onState);
      const failed = r.listings.find((o) => o.error);
      if (failed) showError(`${failed.symbol}: ${failed.error}`);
      else clearError();
    } catch (err) {
      showError(err.message);
    }
    // Only if this company is still the one on screen.
    if (current === symbol && pane.querySelector("#c-body")) load(pane, symbol);
  });
}

function header(v, pane, symbol) {
  const p = v.profile;
  const place = [p.country, p.exchange, p.sector ?? p.category, p.industry ?? p.family].filter(Boolean).join(", ");
  const main = p.primary_symbol !== p.symbol ? `; main listing ${p.primary_symbol}` : "";
  const site = safeUrl(p.website);
  return el("div", { class: "card" },
    el("h2", null, `${p.name} `, el("span", { class: "mono dim" }, p.symbol)),
    el("div", { class: "dim" }, place),
    el("div", { class: "dim small" }, `${p.listings} listing${p.listings === 1 ? "" : "s"}${main}`,
      p.tags.length ? `; tags: ${p.tags.join(", ")}` : "",
      site ? el("span", null, "; ", el("a", { href: site, target: "_blank", rel: "noopener noreferrer" }, p.website)) : null),
    v.dead_since ? el("div", { class: "warn-text small" },
      `No price source has had data for this listing since ${v.dead_since}, so updates skip it. `,
      "Check again tries it now.") : null,
    el("div", { class: "row", style: "margin-top: 8px" },
      job(v.dead_since ? "Check again" : "Update prices", "/api/jobs/prices", pane, symbol),
      job("Analyse", "/api/jobs/analyse", pane, symbol),
      el("button", { type: "button", onclick: () => load(pane, symbol, true) }, "Refresh fundamentals")));
}

function summary(v) {
  const price = v.price;
  const lean = v.lean;
  const cls = lean ? (lean.label.includes("buy") ? "up" : lean.label.includes("sell") ? "down" : "") : "";
  const day = v.day_change == null ? "" : ` ${v.day_change >= 0 ? "+" : ""}${(v.day_change * 100).toFixed(2)}% on the day`;
  return el("div", { class: "row cards" },
    el("div", { class: "card" }, el("div", { class: "dim small" }, tip("Last close", "last_close")),
      price ? el("div", { class: "big" }, `${money(price.close)} ${price.currency}`) : el("div", { class: "dim" }, "no price stored yet"),
      price ? el("div", { class: "dim small" }, `${money(price.close_base)} ${v.base_currency}, ${price.date}${day}`) : null),
    el("div", { class: "card" }, el("div", { class: "dim small" }, tip("Lean", "lean")),
      lean ? el("div", { class: `big ${cls}${lean.stale ? " faded" : ""}` }, `${lean.label} ${Math.round(lean.probability * 100)}%`)
        : el("div", { class: "dim" }, "none yet"),
      lean ? el("div", { class: "dim small" }, el("span", { class: "badge" }, lean.source), ` as of ${lean.as_of}`,
        lean.stale ? el("span", { class: "badge warn" }, "out of date ", tip("", "lean_stale")) : null) : null,
      v.next ? el("div", { class: "dim small" }, `needs: ${v.next}`) : null));
}

function signals(v) {
  if (!v.signals.length) return el("p", { class: "dim" }, "No analyst signals yet: press Analyse.");
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "Analyst"), el("th", null, tip("Says", "signals")),
      el("th", { class: "num" }, tip("Score", "score")), el("th", { class: "num" }, tip("Sure", "confidence")),
      el("th", null, "Why"))),
    el("tbody", null, ...v.signals.map((s) => el("tr", { class: s.stale ? "faded" : null },
      el("td", null, LABELS[s.analyst] ?? s.analyst, s.stale ? el("div", { class: "badge warn" }, "out of date") : null),
      el("td", null, el("span", { class: `sig ${s.direction}` }, s.direction)),
      el("td", { class: "num" }, s.score.toFixed(2)),
      el("td", { class: "num" }, `${Math.round(s.confidence * 100)}%`),
      el("td", null, s.reason,
        s.figures.length ? el("div", { class: "dim small mono figures" }, ...s.figures.map((f) =>
          el("span", null, tipText(f.name, f.meaning), ` ${Number.isInteger(f.value) ? f.value : f.value.toFixed(3)}`))) : null)))));
}

function fundamentals(v) {
  const f = v.fundamentals;
  const note = v.fundamentals_note ? el("p", { class: "dim small" }, v.fundamentals_note) : null;
  if (!f) return el("div", null, note ?? el("p", { class: "dim" }, "No fundamentals yet: press Refresh fundamentals."));
  return el("div", null, note,
    el("p", { class: "dim small" }, `From ${f.source}, ${f.as_of}. Market cap in ${v.base_currency}.`),
    el("table", { class: "results" }, el("tbody", null, ...f.figures.map((x) => el("tr", null,
      el("td", null, tipText(x.label, x.meaning)),
      el("td", { class: "num" }, figure(x.value, x.kind, v.base_currency)))))));
}

async function peers(pane, symbol, message = "") {
  const box = pane.querySelector("#c-peers");
  let p;
  try {
    p = await api(`/api/companies/${encodeURIComponent(symbol)}/peers`);
  } catch (err) {
    box.replaceChildren(el("p", { class: "dim" }, err.message));
    return;
  }
  const count = el("input", { type: "number", min: String(p.range[0]), max: String(p.range[1]), value: String(p.count),
    style: "width: 70px", "aria-label": "Peer count" });
  const saved = el("span", { class: "dim small" }, message);
  const save = el("button", { type: "button", onclick: async () => {
    try {
      const r = await sendJson("/api/settings/peers", { count: Number(count.value) }, "PUT");
      peers(pane, symbol, `${r.note} (written to ${r.path})`);
    } catch (err) { showError(err.message); }
  } }, "Save");
  box.replaceChildren(
    el("div", { class: "row" }, tip("Compare against", "peers"), count, "peers", save, saved),
    p.basis ? el("p", { class: "dim small" }, `${p.basis}${p.fell_back ? " (industry group too small, so the whole sector)" : ""}`)
      : el("p", { class: "dim small" }, p.note),
    el("div", { class: "peers" }, ...p.peers.map((r) =>
      el("span", { class: "chip" }, companyLink(r.symbol), ` ${r.name}`))));
}

async function drawChart(pane, symbol, base) {
  const box = pane.querySelector("#c-chart");
  if (chart) { chart.remove(); chart = null; }
  let body;
  try {
    body = await api(`/api/companies/${encodeURIComponent(symbol)}/prices?days=365`);
  } catch (err) {
    box.replaceChildren(el("p", { class: "dim" }, err.message));
    return;
  }
  if (!pane.contains(box)) return;
  const rows = body.rows;
  // Plotted in the base currency only when every day has a rate; otherwise in the listing's own.
  const useBase = rows.length > 0 && rows.every((r) => r.close_base != null);
  const points = rows.map((r) => ({ time: r.date, value: useBase ? r.close_base : r.close }));
  const caption = !rows.length ? "Last year's closes"
    : useBase ? `Last year's closes in ${base}`
    : `Last year's closes in ${body.currency} (some days have no ${base} rate yet)`;
  box.replaceChildren(el("div", { class: "dim small" }, caption),
    ...(useBase ? body.credits : []).map((c) => el("div", { class: "dim small" }, c)));
  if (points.length < 2) {
    box.append(el("p", { class: "dim" }, "Not enough prices to chart yet."));
    return;
  }
  const holder = el("div");
  box.append(holder);
  chart = lineChart(holder, points);
}

async function load(pane, symbol, refresh = false) {
  const mine = ++seq;
  const body = pane.querySelector("#c-body");
  body.replaceChildren(el("p", { class: "dim" }, refresh ? "Fetching fundamentals..." : "Loading..."));
  let v;
  try {
    v = await api(`/api/companies/${encodeURIComponent(symbol)}${refresh ? "?refresh=true" : ""}`);
  } catch (err) {
    if (mine === seq) body.replaceChildren(el("p", { class: "dim" }, err.message));
    return;
  }
  if (mine !== seq || !pane.contains(body)) return;
  current = v.profile.symbol;
  body.replaceChildren(
    header(v, pane, current), summary(v),
    el("div", { id: "c-chart", class: "card" }),
    researchSection(current),
    el("h3", null, "Analysts"), signals(v),
    el("h3", null, "Fundamentals"), fundamentals(v),
    el("h3", null, "Peers"), el("div", { id: "c-peers" }),
    ...v.credits.map((c) => el("p", { class: "dim small" }, c)),
    v.profile.summary ? el("p", { class: "dim summary" }, v.profile.summary) : "");
  drawChart(pane, current, v.base_currency);
  peers(pane, current);
}

function render(pane, arg) {
  const symbol = arg ?? current;
  const input = el("input", { placeholder: "Symbol, e.g. 7203.T", size: "18", value: symbol ?? "", "aria-label": "Company symbol" });
  const open = () => { if (input.value.trim()) location.hash = `company/${encodeURIComponent(input.value.trim())}`; };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
  pane.replaceChildren(
    el("div", { class: "row" }, input, el("button", { type: "button", onclick: open }, "Open"),
      el("span", { class: "dim small" }, "or click a symbol in the Universe or Watchlist tab")),
    el("div", { id: "c-body" }));
  if (symbol) load(pane, symbol);
  else pane.querySelector("#c-body").replaceChildren(el("p", { class: "dim" }, "Pick a company to see its analysis."));
}

export default { id: "company", label: "Company", render };
