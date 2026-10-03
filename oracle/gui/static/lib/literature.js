// The literature index (research-lab 03): look up a paper or method, every method with its funnel
// status, one method's rule, sources and marks (go / drop / dig deeper), recording a source, and the
// search log. A section of the Research tab; marks and records made here are the user's.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el, safeUrl } from "./dom.js";
import { sendJson } from "./ui.js";

const when = (iso) => iso.slice(0, 16).replace("T", " ");
const list = (text) => text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
const QUALITIES = ["replicated", "peer-reviewed", "working-paper", "practitioner", "abstract-only"];

function cite(s) {
  const who = [s.authors, s.year].filter(Boolean).join(" ");
  return `${who ? `${who}, ` : ""}${s.title} (${s.quality}${s.read_on ? `, read ${s.read_on}` : ""})`;
}

function sourceItem(s, score, open) {
  const href = s.url && safeUrl(s.url);
  return el("li", { "data-source": String(s.id) },
    score != null ? el("span", { class: "mono" }, `${score.toFixed(2)} `) : null,
    href ? el("a", { href, target: "_blank", rel: "noopener" }, cite(s)) : cite(s),
    el("span", { class: "small dim" }, ` ${s.keys.join(", ")}`),
    s.methods.length ? el("span", { class: "small" }, " yields ",
      ...s.methods.flatMap((ref, i) => [i ? ", " : "", refButton(ref, open)])) : null);
}

function refButton(ref, open) {
  return el("button", { type: "button", class: "link mono", onclick: () => open(ref) }, ref);
}

function literatureSection() {
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const query = el("input", { type: "search", size: 50, "aria-label": "Look up", placeholder: "title, method, ref or id" });
  const found = el("div");
  const statusPick = el("select", { "aria-label": "Status" }, el("option", { value: "" }, "every status"));
  const listBody = el("div");
  const detail = el("div", { "data-method": "" });
  const searchesBody = el("div");
  let started = false;
  let shown = null;

  async function open(ref) {
    try {
      showMethod(await api(`/api/lab/methods/${encodeURIComponent(ref)}`));
    } catch (err) {
      say(err.message);
    }
  }

  function showMethod(m) {
    shown = m.ref;
    const markNote = el("input", { size: 40, "aria-label": "Mark note" });
    const marks = [["go", "Go"], ["drop", "Drop"], ["dig", "Dig deeper"]].map(([how, label]) => {
      const b = el("button", { type: "button" }, label);
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          const done = await sendJson(`/api/lab/methods/${encodeURIComponent(m.ref)}/mark`, { how, note: markNote.value });
          say(`${done.ref} is now ${done.status}.`, false);
          showMethod(done);
          await loadMethods();
        } catch (err) {
          say(err.message);
        } finally {
          b.disabled = false;
        }
      });
      return b;
    });
    const facts = [m.family, m.origin, `status ${m.status} (by ${m.status_by}, ${when(m.status_at)} UTC)`,
      m.rating != null ? `rating ${m.rating}` : null, m.archive_status ? `archive: ${m.archive_status}` : null,
      m.test_key ? `test ${m.test_key}` : null, m.duplicate_of ? `duplicate of ${m.duplicate_of}` : null];
    detail.replaceChildren(el("div", { class: "card" },
      el("h3", null, `${m.ref} ${m.name}`),
      el("p", { class: "small dim" }, facts.filter(Boolean).join("; ")),
      ...[["Rule", m.definition], ["Evidence", m.evidence], ["Why", m.why], ["Result", m.result]]
        .filter(([, text]) => text).map(([label, text]) => el("p", null, el("strong", null, `${label}: `), text)),
      m.aliases.length ? el("p", { class: "small" }, `Also called: ${m.aliases.join(", ")}`) : null,
      m.see_also.length ? el("p", { class: "small" }, "See also: ",
        ...m.see_also.flatMap((ref, i) => [i ? ", " : "", refButton(ref, open)])) : null,
      el("h3", null, "Sources"),
      m.sources.length ? el("ul", { class: "small" }, ...m.sources.map((s) => sourceItem(s, null, open)))
        : el("p", { class: "small dim" }, "None recorded."),
      el("div", { class: "row wrap" }, markNote, ...marks),
      m.marks.length ? el("ul", { class: "small" }, ...m.marks.map((k) => el("li", null,
        `${when(k.at)} ${k.mark}: ${k.status_from} → ${k.status_to} by ${k.by}${k.note ? `: ${k.note}` : ""}`))) : null));
  }

  function hitRows(hits) {
    return hits.map((h) => el("tr", { "data-ref": h.ref },
      el("td", null, refButton(h.ref, open)), el("td", null, h.status), el("td", { class: "num" }, h.score.toFixed(2)),
      el("td", null, h.name), el("td", { class: "small dim" }, h.matched)));
  }

  async function lookup() {
    if (!query.value.trim()) return;
    try {
      const r = await api(`/api/lab/lookup?q=${encodeURIComponent(query.value)}`);
      found.replaceChildren(
        el("p", { class: "small dim" }, r.note + (r.key ? ` (${r.key})` : "")),
        r.sources.length ? el("ul", { class: "small" }, ...r.sources.map((h) => sourceItem(h.source, h.score, open))) : null,
        table([["Method"], ["Status"], ["Score", null, 1], ["Name"], ["Matched on"]], hitRows(r.methods), "No methods."));
    } catch (err) {
      say(err.message);
    }
  }

  async function loadMethods() {
    try {
      const r = await api(`/api/lab/methods${statusPick.value ? `?status=${encodeURIComponent(statusPick.value)}` : ""}`);
      if (statusPick.children.length === 1) {
        statusPick.append(...r.statuses.map((s) => el("option", { value: s }, s)));
      }
      listBody.replaceChildren(el("p", { class: "small dim" }, `${r.methods.length} methods`),
        table([["Ref"], ["Status"], ["Rating", null, 1], ["Name"], ["Family"], ["Test"]], r.methods.map((m) =>
          el("tr", { "data-ref": m.ref }, el("td", null, refButton(m.ref, open)), el("td", null, m.status),
            el("td", { class: "num" }, m.rating ?? ""), el("td", null, m.name), el("td", { class: "small dim" }, m.family),
            el("td", { class: "small mono" }, m.test_key ?? ""))), "No methods with this status."));
    } catch (err) {
      say(`Could not load the methods: ${err.message}`);
    }
  }
  statusPick.addEventListener("change", loadMethods);

  const field = (label, attrs = {}) => el("input", { "aria-label": label, size: 30, ...attrs });
  const rec = {
    id: field("Source id or URL"), title: field("Title", { size: 50 }), authors: field("Authors"),
    year: field("Year", { type: "number", size: 6 }), read_on: field("Read on", { type: "date" }), note: field("Note"),
    methods: field("Yields methods", { placeholder: "T-15, A-08" }), new_method: field("New method"),
    family: field("New method family"), definition: field("New method rule", { size: 60 }),
    aliases: field("New method aliases", { placeholder: "comma separated" }),
  };
  const quality = el("select", { "aria-label": "Quality" }, el("option", { value: "" }, "quality (abstract-only if new)"),
    ...QUALITIES.map((q) => el("option", { value: q }, q)));
  const recordButton = el("button", { type: "button" }, "Record");
  const recorded = el("div");
  recordButton.addEventListener("click", async () => {
    const text = (k) => rec[k].value.trim() || null;
    const body = { id: text("id"), title: text("title"), authors: text("authors"),
      year: rec.year.value ? Number(rec.year.value) : null, quality: quality.value || null, note: text("note"),
      read_on: text("read_on"), methods: list(rec.methods.value), new_method: text("new_method"),
      family: rec.family.value.trim(), definition: rec.definition.value.trim(), aliases: list(rec.aliases.value) };
    recordButton.disabled = true;
    try {
      const r = await sendJson("/api/lab/sources", body);
      say([r.source ? `${r.created ? "New" : "Updated"} source #${r.source.id}.` : "",
        r.method ? `New method ${r.method.ref}.` : ""].filter(Boolean).join(" "), false);
      recorded.replaceChildren(
        r.source ? el("ul", { class: "small" }, sourceItem(r.source, null, open)) : null,
        r.nearest.length ? el("div", null, el("p", { class: "small dim" }, "Nearest methods (check for a duplicate):"),
          table([["Method"], ["Status"], ["Score", null, 1], ["Name"], ["Matched on"]], hitRows(r.nearest), "")) : null);
      Object.values(rec).forEach((input) => { input.value = ""; });
      await loadMethods();
    } catch (err) {
      say(err.message);
    } finally {
      recordButton.disabled = false;
    }
  });

  const s = { query: field("Search query", { size: 40 }), where: field("Searched where", { placeholder: "SSRN, Google Scholar" }),
    found: field("Found sources", { size: 50, placeholder: "ids or URLs of recorded sources, comma separated" }),
    note: field("Search note") };
  const logButton = el("button", { type: "button" }, "Log search");
  logButton.addEventListener("click", async () => {
    logButton.disabled = true;
    try {
      const r = await sendJson("/api/lab/searches", { query: s.query.value, where: s.where.value,
        found: list(s.found.value), note: s.note.value });
      say(`Logged search #${r.id} (${r.found.length} found).`, false);
      Object.values(s).forEach((input) => { input.value = ""; });
      await loadSearches();
    } catch (err) {
      say(err.message);
    } finally {
      logButton.disabled = false;
    }
  });

  async function loadSearches() {
    try {
      const r = await api("/api/lab/searches");
      searchesBody.replaceChildren(r.searches.length ? el("ul", { class: "small" }, ...r.searches.map((x) => el("li", null,
        `${when(x.at)} ${x.place}: "${x.query}" by ${x.by}${x.note ? ` (${x.note})` : ""}`,
        x.found.length ? el("ul", null, ...x.found.map((f) => sourceItem(f, null, open))) : null)))
        : el("p", { class: "small dim" }, "No searches logged yet."));
    } catch (err) {
      say(`Could not load the searches: ${err.message}`);
    }
  }

  const lookupButton = el("button", { type: "button", onclick: lookup }, "Look up");
  const node = el("div", { class: "card", "data-literature": "" },
    el("h2", null, "Literature index"),
    el("p", { class: "dim small" }, "Every method the lab knows of, the papers and posts it came from, and the searches "
      + "run for more. Look up by DOI, SSRN, arXiv, NBER or a link (exact), or by title, method name or ref (fuzzy)."),
    el("div", { class: "row wrap" }, query, lookupButton), found, note, detail,
    el("h3", null, "Methods"), el("div", { class: "row" }, statusPick), listBody,
    el("details", null, el("summary", null, "Record a source"),
      el("div", { class: "row wrap" }, rec.id, rec.title, rec.authors, rec.year, quality, rec.read_on),
      el("div", { class: "row wrap" }, rec.note, rec.methods),
      el("div", { class: "row wrap" }, rec.new_method, rec.family, rec.definition, rec.aliases),
      el("div", { class: "row" }, recordButton), recorded),
    el("details", null, el("summary", null, "Search log"),
      el("div", { class: "row wrap" }, s.query, s.where, s.found, s.note, logButton), searchesBody));

  function start() {
    if (started) return Promise.resolve();
    started = true;
    return Promise.all([loadMethods(), loadSearches()]);
  }

  return { node, start, open, shown: () => shown };
}

export { literatureSection };
