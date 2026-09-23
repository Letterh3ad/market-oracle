import { api } from "../lib/api.js";
import { el, showError, clearError } from "../lib/dom.js";
import { tip } from "../lib/tip.js";

const PAGE = 50;
const LABELS = {
  market_cap: "Size", category_group: "Category group", industry: "Industry", exchange: "Exchange",
  relevance: "Relevance", name: "Name", symbol: "Symbol",
};
const label = (field) => LABELS[field] ?? field.charAt(0).toUpperCase() + field.slice(1);

// Survives tab switches, so coming back shows the same search.
const state = { q: "", filters: [], tags: [], sort: "", desc: false, offset: 0, list: "main" };
let options = null;
let seq = 0;

function query() {
  const params = new URLSearchParams({ q: state.q, limit: PAGE, offset: state.offset });
  if (state.sort) params.set("sort", state.sort);
  if (state.desc) params.set("desc", "true");
  for (const [field, value] of state.filters) params.append(field, value);
  for (const t of state.tags) params.append("tag", t);
  return params;
}

async function run(pane) {
  const mine = ++seq;
  let body;
  try {
    body = await api(`/api/universe/search?${query()}`);
  } catch (err) {
    if (mine === seq) showError(err.message);
    return;
  }
  // A newer search answered already, or another tab now owns the pane.
  if (mine !== seq || !pane.querySelector("#u-results")) return;
  clearError();
  pane.querySelector("#u-results").replaceChildren(results(body.hits, pane));
  pane.querySelector("#u-pages").replaceChildren(pages(body.more, pane));
}

function searchAgain(pane) {
  state.offset = 0;
  renderControls(pane);
  run(pane);
}

function renderControls(pane) {
  const sortSelect = el("select", { "aria-label": "Sort by", onchange: (e) => {
    state.sort = e.target.value;
    if (state.sort === "relevance" || !state.sort) state.desc = false;
    searchAgain(pane);
  } }, el("option", { value: "" }, "Default"), ...options.sorts.map((s) =>
    el("option", { value: s, selected: s === state.sort ? "" : null }, label(s))));
  const oneWay = state.sort === "relevance" || !state.sort;
  const direction = el("button", {
    type: "button", disabled: oneWay ? "" : null,
    onclick: () => { state.desc = !state.desc; searchAgain(pane); },
  }, state.desc ? "Descending" : "Ascending");

  const field = el("select", { "aria-label": "Filter field" },
    ...options.filters.map((f) => el("option", { value: f }, label(f))));
  const values = el("datalist", { id: "u-values" });
  const value = el("input", { list: "u-values", placeholder: "value", size: "18", "aria-label": "Filter value" });
  const loadValues = async () => {
    try {
      const rows = await api(`/api/universe/facets/${field.value}`);
      values.replaceChildren(...rows.map((r) =>
        el("option", { value: r.value }, `${r.label ? r.label + ", " : ""}${r.companies.toLocaleString()} companies`)));
    } catch (err) { showError(err.message); }
  };
  field.addEventListener("change", loadValues);
  loadValues();
  const addFilter = el("button", { type: "button", onclick: () => {
    if (!value.value.trim()) return;
    state.filters.push([field.value, value.value.trim()]);
    searchAgain(pane);
  } }, "Add filter");
  const tagInput = el("input", { placeholder: "tag", size: "12", "aria-label": "Tag filter" });
  const addTag = el("button", { type: "button", onclick: () => {
    if (!tagInput.value.trim()) return;
    state.tags.push(tagInput.value.trim());
    searchAgain(pane);
  } }, "Require tag");

  const chips = [
    ...state.filters.map(([f, v], i) => chip(`${label(f)}: ${v}`, () => { state.filters.splice(i, 1); searchAgain(pane); })),
    ...state.tags.map((t, i) => chip(`tag: ${t}`, () => { state.tags.splice(i, 1); searchAgain(pane); })),
  ];
  pane.querySelector("#u-controls").replaceChildren(
    el("div", { class: "row" }, field, value, values, addFilter, tagInput, addTag, tip("", "tags")),
    el("div", { class: "row" }, el("span", { class: "dim" }, "Sort"), sortSelect, direction, tip("", "sort_direction")),
    chips.length ? el("div", { class: "row" }, ...chips) : null,
  );
}

function chip(text, onRemove) {
  return el("span", { class: "chip" }, text,
    el("button", { type: "button", "aria-label": `Remove ${text}`, onclick: onRemove }, "×"));
}

function results(hits, pane) {
  if (!hits.length) return el("p", { class: "dim" }, "No matches.");
  return el("table", { class: "results" },
    el("thead", null, el("tr", null,
      el("th", null, "Symbol"), el("th", null, "Name"), el("th", null, "Country"), el("th", null, "Exchange"),
      el("th", null, "Sector / category"), el("th", null, tip("Size", "market_cap")),
      el("th", null, tip("Tags", "tags")), el("th", null, ""))),
    el("tbody", null, ...hits.map((h) => row(h, pane))));
}

function row(h, pane) {
  const others = h.listings > 1 ? el("span", { class: "dim" }, ` +${h.listings - 1} `, tip("", "listings")) : null;
  const tagCell = el("td", null, ...h.tags.map((t) => chip(t, async () => {
    try { await api(`/api/companies/${encodeURIComponent(h.symbol)}/tags/${encodeURIComponent(t)}`, { method: "DELETE" }); run(pane); }
    catch (err) { showError(err.message); }
  })), newTag(h, pane));
  const watchBtn = el("button", { type: "button", onclick: async (e) => {
    try {
      const w = await api(`/api/watchlists/${encodeURIComponent(state.list)}/items`, json({ symbol: h.symbol }));
      e.target.textContent = w.added ? "Watching" : "Already watched";
      e.target.disabled = true;
    } catch (err) { showError(err.message); }
  } }, "Watch");
  return el("tr", { class: h.delisted ? "delisted" : null },
    el("td", { class: "mono" }, h.symbol), el("td", null, h.name, others, h.delisted ? " (delisted)" : null),
    el("td", null, h.country ?? ""), el("td", null, h.exchange ?? ""),
    el("td", null, h.sector ?? h.category ?? ""), el("td", null, (h.market_cap ?? "?").replace(" Cap", "")),
    tagCell, el("td", null, watchBtn));
}

function newTag(h, pane) {
  const input = el("input", { class: "tag-input", placeholder: "+ tag", size: "6", "aria-label": `Tag ${h.symbol}` });
  input.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || !input.value.trim()) return;
    try { await api(`/api/companies/${encodeURIComponent(h.symbol)}/tags`, json({ tag: input.value })); run(pane); }
    catch (err) { showError(err.message); }
  });
  return input;
}

function pages(more, pane) {
  const move = (by) => { state.offset = Math.max(0, state.offset + by); run(pane); };
  return el("div", { class: "row" },
    el("button", { type: "button", disabled: state.offset ? null : "", onclick: () => move(-PAGE) }, "Previous"),
    el("span", { class: "dim" }, `from ${state.offset + 1}`),
    el("button", { type: "button", disabled: more ? null : "", onclick: () => move(PAGE) }, "Next"));
}

function json(body) {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

async function render(pane) {
  let timer;
  const box = el("input", { type: "search", placeholder: "Search companies by name or symbol", size: "40",
    value: state.q, "aria-label": "Search", oninput: (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.q = e.target.value; searchAgain(pane); }, 250);
    } });
  pane.replaceChildren(
    el("div", { class: "row" }, box, el("span", { class: "dim" }, "Watch adds to "),
      el("input", { value: state.list, size: "8", "aria-label": "Watchlist", onchange: (e) => { state.list = e.target.value.trim() || "main"; } })),
    el("div", { id: "u-controls" }), el("div", { id: "u-results" }), el("div", { id: "u-pages" }));
  try {
    options ??= await api("/api/universe/options");
  } catch (err) {
    showError(err.message);
    return;
  }
  renderControls(pane);
  run(pane);
}

export default { id: "universe", label: "Universe", render };
