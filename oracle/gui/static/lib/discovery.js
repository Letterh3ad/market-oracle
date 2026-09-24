// The Watchlist tab's Discovery section: companies found beside the watched ones, why each
// was found, and what to do with it.
import { api } from "./api.js";
import { el, showError, clearError, companyLink, safeUrl } from "./dom.js";
import { busyButton, runJob, sendJson } from "./ui.js";
import { repaint } from "./repaint.js";

function discoverySummary(r) {
  if (r.note) return [`Nothing to do: ${r.note}.`];
  return [
    r.added.length ? null : `Nothing new to add this time (looked around ${r.seeds.join(", ")}).`,
    ...r.added.map((a) => `Added ${a.symbol} (${a.name}): ${a.why}.`),
    ...r.skipped.map((s) => `Skipped ${s.name}: ${s.reason}.`),
    ...r.source_errors.map((e) => `Could not use ${e}.`),
  ].filter(Boolean);
}

function evidence(items) {
  if (!items.length) return null;
  return el("ul", { class: "small" }, ...items.map((e) => {
    const link = safeUrl(e.link);
    const title = link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title;
    return el("li", null, title, e.outlet ? el("span", { class: "dim" }, ` (${e.outlet})`) : null);
  }));
}

// `changed` runs after a Move or Remove, so the page can reload what else it shows.
function discoveryTable(body, changed) {
  const s = body.settings;
  if (!body.items.length) {
    return el("p", { class: "dim" }, "Nothing discovered yet. Discover now looks for companies related to the ones "
      + `you watch and adds up to ${s.per_tick} a run, ${s.max_total} in all.`);
  }
  const act = (label, hint, send) => el("button", { type: "button", class: "small-btn", title: hint, onclick: async () => {
    try {
      clearError();
      await send();
      changed();
    } catch (err) { showError(err.message); }
  } }, label);
  const path = (symbol) => `/api/discovery/${encodeURIComponent(symbol)}`;
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "Symbol"), el("th", null, "Name"),
      el("th", null, "Why it was found"), el("th", null, "Added"), el("th", null, ""))),
    el("tbody", null, ...body.items.map((i) => el("tr", null,
      el("td", null, companyLink(i.symbol)), el("td", { class: "name" }, i.name ?? ""),
      el("td", null, i.why, evidence(i.evidence)),
      el("td", null, (i.added_at ?? "").slice(0, 10)),
      el("td", null,
        act("Move to my watchlist", "Watch it on your main list; it leaves this list",
          () => sendJson(`${path(i.symbol)}/move`)),
        act("Remove", "Take it off this list; discovery never adds it again",
          () => api(path(i.symbol), { method: "DELETE" })))))));
}

function discoverySection(changed = () => {}) {
  const result = el("div", { class: "result" });
  const box = el("div", { id: "d-list" });
  const refresh = async () => {
    try {
      repaint(box, [discoveryTable(await api("/api/discovery"), () => { refresh(); changed(); })]);
    } catch (err) { showError(err.message); }
  };
  const button = busyButton("Discover now", async (onState) => {
    try {
      const r = await runJob("/api/jobs/discover", onState);
      clearError();
      result.replaceChildren(...discoverySummary(r).map((line) => el("div", null, line)));
    } catch (err) { showError(err.message); }
    refresh();
    changed();
  });
  refresh();
  return el("section", { class: "discovery" },
    el("h3", null, "Discovery"),
    el("p", { class: "dim small" }, "Companies related to the ones you watch: the same industry, or named beside "
      + "them in the news. Each is checked to be a real listing with recent prices, then put on the \"discovered\" "
      + "list, which is priced and researched like your own. Settings has how many and the never list."),
    el("div", { class: "row" }, button), result, box);
}

export { discoverySection, discoverySummary, discoveryTable };
