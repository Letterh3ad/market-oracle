// The Company tab's linked companies: who the news ties this one to (supplier, customer,
// competitor, partner) or the same industry, the headlines behind each, and Delete.
import { api } from "./api.js";
import { el, showError, clearError, companyLink, safeUrl } from "./dom.js";
import { repaint } from "./repaint.js";

const path = (symbol) => `/api/companies/${encodeURIComponent(symbol)}/links`;

function headlines(items) {
  if (!items.length) return null;
  return el("ul", { class: "small" }, ...items.map((e) => {
    const link = safeUrl(e.link);
    const title = link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title;
    return el("li", null, `${(e.published_at ?? "undated").slice(0, 10)} `, title,
      e.outlet ? el("span", { class: "dim" }, ` (${e.outlet})`) : null);
  }));
}

// `changed` runs after a Delete.
function linksTable(symbol, links, changed) {
  if (!links.length) {
    return el("p", { class: "dim" }, "No linked companies yet. Discovery keeps the companies the news ties to "
      + "the ones you watch; research on this company then also searches the strongest two.");
  }
  const remove = (x) => el("button", { type: "button", class: "small-btn",
    title: "A wrong link: delete it, and it is never recorded again", onclick: async () => {
      try {
        clearError();
        await api(`/api/companies/${encodeURIComponent(symbol)}/links/${x.id}`, { method: "DELETE" });
        changed();
      } catch (err) { showError(err.message); }
    } }, "Delete");
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "Symbol"), el("th", null, "Name"),
      el("th", null, "Link and headlines"), el("th", null, "Last seen"), el("th", null, ""))),
    el("tbody", null, ...links.map((x) => el("tr", null,
      el("td", null, companyLink(x.symbol)), el("td", { class: "name" }, x.name ?? ""),
      el("td", null, x.why, headlines(x.evidence)),
      el("td", null, x.last_seen.slice(0, 10)),
      el("td", null, remove(x))))));
}

// Fills `box` once the links arrive, and again after each Delete; a late answer for a box no
// longer on screen is dropped.
async function showLinks(box, symbol) {
  let body;
  try {
    body = await api(path(symbol));
  } catch (err) {
    if (box.isConnected) box.replaceChildren(el("p", { class: "dim" }, err.message));
    return;
  }
  if (box.isConnected) repaint(box, [linksTable(symbol, body.links, () => showLinks(box, symbol))]);
}

export { linksTable, showLinks };
