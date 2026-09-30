// The Company tab's Industry panel: each related company (competitors from the universe and
// the peers, links from the news or added by you), how and since when, what it is doing now
// from stored data, Delete on a link and an Add link form.
import { api } from "./api.js";
import { el, showError, clearError, companyLink } from "./dom.js";
import { pct, tone } from "./fmt.js";
import { headlines } from "./links.js";
import { repaint } from "./repaint.js";
import { sendJson } from "./ui.js";

function relationLine(x) {
  const when = x.first_known ? `first known ${x.first_known.slice(0, 10)}` : "static field";
  return el("div", null, x.why, el("span", { class: "dim" }, ` (${x.source}, ${when})`));
}

function move(v, days) {
  return el("span", { class: v == null ? "dim" : tone(v) }, `${days}d ${pct(v)}`);
}

function doingNow(now) {
  const dates = [["last earnings", now.last_earnings], ["next earnings", now.next_earnings]]
    .map(([label, d]) => `${label} ${d ?? "not known"}`).join(", ");
  return el("div", null,
    el("div", null, move(now.move_5d, 5), " ", move(now.move_20d, 20),
      el("span", { class: "dim" }, now.last_close ? ` to ${now.last_close}` : " (no stored prices)")),
    el("div", { class: "dim small" }, dates),
    headlines(now.headlines) ?? el("div", { class: "dim small" }, "no stored headlines"));
}

function addForm(symbol, relations, changed) {
  const other = el("input", { placeholder: "Symbol", size: "10", "aria-label": "Linked company symbol" });
  const relation = el("select", { "aria-label": "How it is related" },
    ...relations.map((r) => el("option", { value: r }, `is a ${r} of ${symbol}`)));
  const add = el("button", { type: "button", onclick: async () => {
    if (!other.value.trim()) return;
    try {
      clearError();
      await sendJson(`/api/companies/${encodeURIComponent(symbol)}/links`, { other: other.value.trim(), relation: relation.value });
      changed();
    } catch (err) { showError(err.message); }
  } }, "Add link");
  return el("div", { class: "row" }, other, relation, add);
}

// `changed` runs after an Add or a Delete.
function industryPanel(symbol, body, changed) {
  const remove = (r) => r.link_id == null ? null : el("button", { type: "button", class: "small-btn",
    title: "A wrong link: delete it, and it is never recorded again", onclick: async () => {
      try {
        clearError();
        await api(`/api/companies/${encodeURIComponent(symbol)}/links/${r.link_id}`, { method: "DELETE" });
        changed();
      } catch (err) { showError(err.message); }
    } }, "Delete");
  const caption = el("p", { class: "dim small" },
    body.basis ? `Competitors by ${body.basis}.` : (body.note ?? "No competitors."));
  const table = body.related.length ? el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "Symbol"), el("th", null, "Name"),
      el("th", null, "How related, and since when"), el("th", null, "Doing now"), el("th", null, ""))),
    el("tbody", null, ...body.related.map((r) => el("tr", null,
      el("td", null, companyLink(r.symbol)),
      el("td", { class: "name" }, r.name ?? "", el("div", { class: "dim small" }, r.market_cap ?? "")),
      el("td", null, ...r.relations.map(relationLine), headlines(r.evidence)),
      el("td", null, doingNow(r.now)),
      el("td", null, remove(r))))))
    : el("p", { class: "dim" }, "No related companies yet.");
  return el("div", null, caption, table, addForm(symbol, body.relations, changed));
}

// Fills `box` once the map arrives, and again after each change; a late answer for a box no
// longer on screen is dropped.
async function showIndustry(box, symbol) {
  let body;
  try {
    body = await api(`/api/companies/${encodeURIComponent(symbol)}/industry`);
  } catch (err) {
    if (box.isConnected) box.replaceChildren(el("p", { class: "dim" }, err.message));
    return;
  }
  if (box.isConnected) repaint(box, [industryPanel(symbol, body, () => showIndustry(box, symbol))]);
}

export { industryPanel, showIndustry };
