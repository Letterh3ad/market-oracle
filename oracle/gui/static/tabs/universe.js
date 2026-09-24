import { api } from "../lib/api.js";
import { el, showError, clearError, companyLink } from "../lib/dom.js";
import { tip } from "../lib/tip.js";
import { sendJson, busyButton, runJob } from "../lib/ui.js";
import { refresh as refreshHeader } from "../lib/status.js";
import { fillPicker } from "../lib/watchlists.js";

const PAGE = 50;
const LABELS = {
  market_cap: "Size", category_group: "Category group", industry: "Industry", exchange: "Exchange",
  relevance: "Relevance", name: "Name", symbol: "Symbol",
};
const label = (field) => LABELS[field] ?? field.charAt(0).toUpperCase() + field.slice(1);

// Survives tab switches, so coming back shows the same search.
const state = { q: "", filters: [], tags: [], sort: "", desc: false, offset: 0, list: "main", field: null };
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
    ...options.filters.map((f) => el("option", { value: f, selected: f === state.field ? "" : null }, label(f))));
  const values = el("datalist", { id: "u-values" });
  const value = el("input", { list: "u-values", placeholder: "value", size: "18", "aria-label": "Filter value" });
  const loadValues = async () => {
    try {
      const rows = await api(`/api/universe/facets/${field.value}`);
      values.replaceChildren(...rows.map((r) =>
        el("option", { value: r.value }, `${r.label ? r.label + ", " : ""}${r.companies.toLocaleString()} companies`)));
    } catch (err) { showError(err.message); }
  };
  field.addEventListener("change", () => { state.field = field.value; loadValues(); });
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
  const rows = [
    el("div", { class: "row" }, field, value, values, addFilter, tagInput, addTag, tip("", "tags")),
    el("div", { class: "row" }, el("span", { class: "dim" }, "Sort"), sortSelect, direction, tip("", "sort_direction")),
  ];
  // replaceChildren would print a null as the text "null".
  if (chips.length) rows.push(el("div", { class: "row" }, ...chips));
  pane.querySelector("#u-controls").replaceChildren(...rows);
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
      const w = await sendJson(`/api/watchlists/${encodeURIComponent(state.list)}/items`, { symbol: h.symbol });
      e.target.textContent = w.added ? "Watching" : "Already watched";
      e.target.disabled = true;
    } catch (err) { showError(err.message); }
  } }, "Watch");
  return el("tr", { class: h.delisted || h.dead ? "delisted" : null },
    el("td", null, companyLink(h.symbol)), el("td", null, h.name, others, h.delisted ? " (delisted)" : null,
      h.dead ? " (no price data lately)" : null),
    el("td", null, h.country ?? ""), el("td", null, h.exchange ?? ""),
    el("td", null, h.sector ?? h.category ?? ""), el("td", null, (h.market_cap ?? "?").replace(" Cap", "")),
    tagCell, el("td", null, watchBtn));
}

function newTag(h, pane) {
  const input = el("input", { class: "tag-input", placeholder: "+ tag", size: "6", "aria-label": `Tag ${h.symbol}` });
  input.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" || !input.value.trim()) return;
    try { await sendJson(`/api/companies/${encodeURIComponent(h.symbol)}/tags`, { tag: input.value }); run(pane); }
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

function describe(u) {
  if (!u.loaded) return "Not loaded yet: press Refresh universe (downloads about 17 MB).";
  return `${u.listings.toLocaleString()} listings, ${u.companies.toLocaleString()} companies, `
    + `${u.delisted.toLocaleString()} delisted; refreshed ${new Date(u.refreshed_at).toLocaleString()}`;
}

async function showStatus(pane) {
  const line = pane.querySelector("#u-status-text");
  try {
    const u = await api("/api/universe/status");
    if (line.isConnected) line.textContent = describe(u);
    return u;
  } catch (err) {
    showError(err.message);
  }
}

function refreshButton(pane) {
  return busyButton("Refresh universe", async (onState) => {
    try {
      const u = await runJob("/api/jobs/universe", onState);
      if (!pane.querySelector("#u-status-text")) return;
      pane.querySelector("#u-status-text").textContent = `Refreshed. ${describe(u)}`;
      refreshHeader();
      await start(pane);
    } catch (err) { showError(`universe refresh: ${err.message}`); }
  });
}

// What suffix-blind grouping would join; the job only reads, so it is safe to press any time.
function mergesSection() {
  const out = el("div");
  const button = busyButton("Show proposed merges", async (onState) => {
    try {
      clearError();
      const r = await runJob("/api/jobs/merges", onState);
      out.replaceChildren(el("p", { class: "dim small" }, `${r.merges.length} proposed. The setting is `
        + (r.enabled ? "on: the next universe refresh applies them." : "off (Settings, [universe] suffix_blind_grouping): nothing changes.")),
        ...(r.merges.length ? [mergeTable(r.merges)] : []));
    } catch (err) { showError(err.message); }
  });
  return el("details", null, el("summary", null, "Proposed merges"),
    el("p", { class: "dim small" }, "Companies whose names differ only in legal forms (plc, Inc., AG) in one country, "
      + "which suffix-blind grouping would join. Never when both carry different ISINs."),
    el("div", { class: "row" }, button), out);
}

function mergeTable(merges) {
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "Matched on"), el("th", null, "Company"),
      el("th", null, "Listings"), el("th", null, "Exchanges"))),
    el("tbody", null, ...merges.flatMap((m) => m.companies.map((c, i) => el("tr", null,
      el("td", { class: "dim" }, i ? "" : m.matched_on.join("; ")), el("td", null, c.name),
      el("td", null, ...c.symbols.flatMap((s, k) => (k ? [" ", companyLink(s)] : [companyLink(s)]))),
      el("td", null, c.exchanges.join(", ")))))));
}

async function start(pane) {
  try {
    options ??= await api("/api/universe/options");
  } catch (err) {
    showError(err.message);
    return;
  }
  renderControls(pane);
  run(pane);
  showTags(pane);
}

// Every tag in use, so one can be picked without remembering it.
async function showTags(pane) {
  let tags;
  try { tags = await api("/api/universe/tags"); } catch (err) { showError(err.message); return; }
  const box = pane.querySelector("#u-tags");
  if (!box || !tags.length) return;
  box.replaceChildren(el("span", { class: "dim" }, "Your tags"), ...tags.map((t) => {
    const count = t.symbols.length + t.orphaned.length;
    const title = [...t.symbols, ...t.orphaned.map((o) => `${o} (no longer listed)`)].join(", ");
    return el("button", { type: "button", class: "small-btn", title, onclick: () => {
      if (!state.tags.includes(t.tag)) state.tags.push(t.tag);
      searchAgain(pane);
    } }, `${t.tag} (${count})`);
  }));
}

async function render(pane) {
  let timer;
  const box = el("input", { type: "search", placeholder: "Search companies by name or symbol", size: "40",
    value: state.q, "aria-label": "Search", oninput: (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.q = e.target.value; searchAgain(pane); }, 250);
    } });
  const target = el("select", { "aria-label": "Watchlist", onchange: (e) => { state.list = e.target.value; } });
  fillPicker(target, [], state.list);
  api("/api/watchlists").then((lists) => fillPicker(target, lists, state.list)).catch((err) => showError(err.message));
  pane.replaceChildren(
    el("div", { class: "row" }, box, el("span", { class: "dim" }, "Watch adds to "), target,
      el("span", { class: "dim small" }, "(new lists: Watchlist tab)")),
    el("div", { id: "u-tags", class: "row" }), el("div", { id: "u-controls" }), el("div", { id: "u-results" }), el("div", { id: "u-pages" }),
    mergesSection());
  pane.prepend(el("div", { class: "row" }, el("span", { id: "u-status-text", class: "dim" }, "Universe ..."),
    refreshButton(pane), tip("", "universe")));
  const u = await showStatus(pane);
  if (u?.loaded) await start(pane);
}

export default { id: "universe", label: "Universe", render };
