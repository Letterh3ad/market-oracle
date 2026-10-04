// The Research tab's papers card (research-lab 07): papers I'd like (sources with no legal open copy, for
// the user to fetch through their library), the open-access fetch for one source, and the inbox scan that
// matches dropped PDFs to their sources. Never a shadow library.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el, safeUrl } from "./dom.js";
import { sendJson } from "./ui.js";

const name = (s) => `#${s.id} ${s.title}`;

function papersSection() {
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const folder = el("span", { class: "mono" });
  const body = el("div");
  const scanned = el("div");
  const source = el("input", { size: 40, "aria-label": "Paper source", placeholder: "DOI, SSRN, arXiv, URL or #12" });
  const wantNote = el("input", { size: 30, "aria-label": "Wanted note", placeholder: "note (optional)" });

  async function load() {
    let r;
    try {
      r = await api("/api/lab/papers/wanted");
    } catch (err) {
      say(`Could not load papers I'd like: ${err.message}`);
      return;
    }
    folder.replaceChildren(r.inbox ?? "");
    body.replaceChildren(table([["Paper"], ["Why"], ["Added"], [""]], (r.wanted ?? []).map((w) => {
      const href = w.source.url && safeUrl(w.source.url);
      return el("tr", { "data-wanted": String(w.source.id) },
        el("td", null, href ? el("a", { href, target: "_blank", rel: "noopener" }, name(w.source)) : name(w.source),
          el("div", { class: "small mono dim" }, w.source.keys.join(", "))),
        el("td", { class: "small" }, w.reason, w.note ? el("div", { class: "dim" }, `note: ${w.note}`) : null),
        el("td", { class: "small" }, `${w.at.slice(0, 10)} by ${w.by}`),
        el("td", null,
          el("button", { type: "button", class: "small", onclick: () => fetchOne(`#${w.source.id}`) }, "Fetch again"),
          el("button", { type: "button", class: "small", onclick: () => remove(w.source.id) }, "Remove")));
    }), "No papers wanted: every recorded source with a DOI has its text, or has not been looked up."));
  }

  async function fetchOne(ident) {
    if (!ident.trim()) { say("Give a recorded source: its DOI, SSRN, arXiv or NBER id, URL or #number."); return; }
    say(`Looking up ${ident} on Unpaywall and Semantic Scholar...`, false);
    try {
      const r = await sendJson("/api/lab/papers/fetch", { source: ident.trim() });
      say(r.copy
        ? `${name(r.source)}: full text kept, ${r.pages} pages from ${r.copy.via}. ${r.note}`
        : r.wanted ? `${name(r.source)}: no legal open copy could be read, added to papers I'd like. ${r.note}`
          : `${name(r.source)}: ${r.note}`, false);
      await load();
    } catch (err) { say(err.message); }
  }

  async function remove(id) {
    try {
      await api(`/api/lab/papers/wanted?source=${encodeURIComponent(`#${id}`)}`, { method: "DELETE" });
      say(`#${id} is off the list.`, false);
      await load();
    } catch (err) { say(err.message); }
  }

  const fetchButton = el("button", { type: "button", onclick: () => fetchOne(source.value) }, "Fetch open copy");
  const wantButton = el("button", { type: "button", onclick: async () => {
    try {
      const w = await sendJson("/api/lab/papers/wanted", { source: source.value.trim(), note: wantNote.value.trim() });
      say(`${name(w.source)} is on papers I'd like.`, false);
      await load();
    } catch (err) { say(err.message); }
  } }, "Add to papers I'd like");
  const scanButton = el("button", { type: "button", onclick: async () => {
    try {
      const r = await sendJson("/api/lab/papers/inbox", {});
      scanned.replaceChildren(r.results.length
        ? el("ul", { class: "small" }, ...r.results.map((x) => el("li", { "data-inbox": x.file },
          x.source ? `${x.file}: ${name(x.source)} (by ${x.matched}, ${x.pages} pages). ${x.note}` : `${x.file}: ${x.note}`)))
        : el("p", { class: "small dim" }, "No PDFs waiting in the inbox."));
      say(`Inbox scanned: ${r.results.filter((x) => x.source).length} of ${r.results.length} matched.`, false);
      await load();
    } catch (err) { say(err.message); }
  } }, "Scan inbox");

  const node = el("div", { class: "card" }, el("h2", null, "Papers"),
    el("p", { class: "dim small" }, "Full texts come from legal open copies only (Unpaywall, then Semantic Scholar; "
      + "Unpaywall needs [research] open_access_email in Settings). A paper with no open copy goes on papers I'd "
      + "like: fetch it through your library and drop the PDF in the inbox folder, then scan it. A PDF is matched "
      + "to its source by the DOI in it or its file name, else by its title."),
    el("div", { class: "row small" }, source, fetchButton, wantNote, wantButton),
    el("div", { class: "row small" }, "Inbox ", folder, " ", scanButton),
    note, scanned, el("h3", null, "Papers I'd like"), body);
  return { node, start: load };
}

export { papersSection };
