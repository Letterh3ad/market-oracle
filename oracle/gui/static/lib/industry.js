// The Company tab's Industry panel: each related company (competitors from the universe and
// the peers, links from the news or added by you), how and since when, what it is doing now
// from stored data, Delete on a link and an Add link form.
import { api } from "./api.js";
import { el, showError, clearError, companyLink, safeUrl } from "./dom.js";
import { pct, tone } from "./fmt.js";
import { headlines } from "./links.js";
import { repaint } from "./repaint.js";
import { busyButton, runJob, sendJson } from "./ui.js";

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

const TIE_TIP = "Read from the company's stored headlines and US 10-Ks. A keyword rule and Laya each say what "
  + "the other company is; a tie is linked only where both agree and the sentence is no list of companies. On 100 "
  + "hand-labelled ties Laya alone did worse than always saying \"no tie\"; linked ties were right 13 times in 19, "
  + "mostly competitors and partners: for suppliers and customers only 3 in 5, and it finds perhaps a fifth of the "
  + "real ties. Ties only the rule names wait here for you; a deleted tie is never linked again.";
const pctOf = (p) => `${Math.round(p * 100)}%`;

function findButtons(symbol, enabled, changed) {
  if (!enabled) return el("span", { class: "dim small" }, "Laya is off (Settings, [laya] enabled): ties are not read.");
  const run = (query, label) => busyButton(label, async (onState) => {
    try {
      clearError();
      await runJob(`/api/jobs/edges?${query}`, onState);
    } catch (err) {
      showError(err.message);
    }
    changed();
  });
  return el("div", { class: "row" }, run(`symbol=${encodeURIComponent(symbol)}`, "Find suppliers and customers"),
    run("watchlist=main", "Find for the main list"));
}

function tieRow(symbol, e, changed) {
  const act = (label, method, path, title) => el("button", { type: "button", class: "small-btn", title, onclick: async () => {
    try {
      clearError();
      await api(path, { method });
      changed();
    } catch (err) { showError(err.message); }
  } }, label);
  const link = safeUrl(e.link);
  const source = link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title;
  const pending = e.state === "unconfirmed";
  return el("tr", null,
    el("td", null, companyLink(e.other), el("div", { class: "dim small" }, e.other_name ?? "")),
    el("td", null, `a ${e.relation}`, el("div", { class: "dim small" }, pending ? "unconfirmed" : e.state)),
    el("td", null, `"${e.evidence}"`, el("div", { class: "dim small" }, `${e.known_at.slice(0, 10)} `, source,
      e.documents > 1 ? ` (and ${e.documents - 1} later document${e.documents > 2 ? "s" : ""})` : "")),
    el("td", { class: "dim small" }, `rule ${e.rule}, Laya ${e.choice} ${pctOf(e.prob)}`),
    el("td", null,
      pending ? act("Confirm", "POST", `/api/companies/${encodeURIComponent(symbol)}/edges/${e.id}/confirm`,
        "Right: keep it as a link, dated by its document") : null,
      pending ? act("Delete", "DELETE", `/api/companies/${encodeURIComponent(symbol)}/edges/${e.id}`, "Wrong: drop it")
        : null));
}

// The ties read from documents: linked ones with their evidence, unconfirmed ones with Confirm and Delete.
function tiesSection(symbol, body, changed) {
  const head = el("div", { class: "row" }, findButtons(symbol, body.enabled, changed),
    el("span", { class: "badge warn", title: TIE_TIP }, "uncalibrated"));
  if (!body.edges.length) {
    return el("div", null, head, el("p", { class: "dim" }, "No ties read yet from headlines or 10-Ks."));
  }
  return el("div", null, head, el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "Company"), el("th", null, "Is"),
      el("th", { title: TIE_TIP }, "Evidence"), el("th", null, "Typed by"), el("th", null, ""))),
    el("tbody", null, ...body.edges.map((e) => tieRow(symbol, e, changed)))));
}

// Fills `box` once the map and the ties arrive, and again after each change; a late answer for a
// box no longer on screen is dropped.
async function showIndustry(box, symbol) {
  let body, ties;
  try {
    [body, ties] = await Promise.all([api(`/api/companies/${encodeURIComponent(symbol)}/industry`),
      api(`/api/companies/${encodeURIComponent(symbol)}/edges`)]);
  } catch (err) {
    if (box.isConnected) box.replaceChildren(el("p", { class: "dim" }, err.message));
    return;
  }
  const again = () => showIndustry(box, symbol);
  if (box.isConnected) {
    repaint(box, [industryPanel(symbol, body, again), el("h4", null, "Suppliers and customers from filings and news"),
      tiesSection(symbol, ties, again)]);
  }
}

export { industryPanel, showIndustry, tiesSection };
