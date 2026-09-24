import { api } from "./api.js";
import { el, showError } from "./dom.js";
import { busyButton, runJob } from "./ui.js";

// Exchange rates in the Data sources panel: each currency in use, its history on click,
// and Update FX (every configured source in order, or only the ticked ones).
const picked = new Set();
let box = null;

const credits = (lines) => lines.map((c) => el("p", { class: "dim small" }, c));

async function showHistory(currency, days, into) {
  try {
    const body = await api(`/api/fx/${encodeURIComponent(currency)}?days=${days}`);
    into.replaceChildren(
      el("table", { class: "small" },
        el("thead", null, el("tr", null, el("th", null, "Date"),
          el("th", { class: "num" }, `${body.currency} per ${body.base}`), el("th", null, "Source"))),
        el("tbody", null, ...body.rates.map((r) => el("tr", null, el("td", null, r.date),
          el("td", { class: "num" }, r.units_per_base.toFixed(4)), el("td", null, r.source))))),
      ...credits(body.credits));
  } catch (err) { showError(`fx history: ${err.message}`); }
}

function rateRow(c, days, history) {
  return el("tr", null,
    el("td", null, el("button", { type: "button", class: "small-btn", title: "Show history",
      onclick: () => showHistory(c.currency, days.value, history) }, c.currency)),
    el("td", { class: "num" }, c.units_per_base == null ? "none" : c.units_per_base.toFixed(4)),
    el("td", null, c.date ?? ""), el("td", null, c.source ?? ""),
    el("td", null, c.age_days == null ? "" : `${c.age_days}d`,
      c.stale ? el("span", { class: "badge warn" }, "stale") : null));
}

function updateButton(note) {
  return busyButton("Update FX", async (onState) => {
    const query = [...picked].map((s) => `source=${encodeURIComponent(s)}`).join("&");
    try {
      const result = await runJob(`/api/jobs/fx${query ? `?${query}` : ""}`, onState);
      note.replaceChildren(...(result.updates.length ? result.updates.map((u) => el("div", null,
        u.source ? `${u.currency}: ${u.added} new rates from ${u.source}` : `${u.currency}: no rates (${u.error})`))
        : [el("div", null, "No foreign-currency prices stored yet.")]), ...credits(result.credits));
      await load();
    } catch (err) { showError(`fx update: ${err.message}`); }
  });
}

async function load() {
  if (!box) return;
  let body;
  try { body = await api("/api/fx"); } catch (err) { showError(`fx: ${err.message}`); return; }
  const history = el("div");
  const note = el("div", { class: "result" });
  const days = el("select", { "aria-label": "Days of history" },
    ...[10, 30, 90, 365].map((d) => el("option", { value: d }, `${d} days`)));
  const sources = body.sources.map((s) => el("label", { class: "small" },
    el("input", { type: "checkbox", checked: picked.has(s) ? "" : null,
      onchange: (e) => { if (e.target.checked) picked.add(s); else picked.delete(s); } }), ` ${s}`));
  box.replaceChildren(
    el("h3", null, `Exchange rates into ${body.base}`),
    body.currencies.length ? el("table", null,
      el("thead", null, el("tr", null, el("th", null, "Currency"), el("th", { class: "num" }, `Per ${body.base}`),
        el("th", null, "Date"), el("th", null, "Source"), el("th", null, "Age"))),
      el("tbody", null, ...body.currencies.map((c) => rateRow(c, days, history))))
      : el("p", { class: "dim" }, "No foreign-currency prices stored yet."),
    ...credits(body.credits),
    el("div", { class: "row" }, updateButton(note), el("span", { class: "dim small" }, "Only from:"), ...sources,
      el("span", { class: "dim small" }, "History:"), days),
    note, history);
}

function mount(container) {
  box = container;
  // Loaded on opening, so ages and new currencies are current without polling.
  container.closest("details").addEventListener("toggle", (e) => { if (e.target.open) load(); });
}

export { mount };
