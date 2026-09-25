import { api } from "./api.js";
import { el, showError, clearError, companyLink } from "./dom.js";
import { busyButton, runJob } from "./ui.js";

// Index members FinanceDatabase lacks, added from Yahoo a bounded batch a press; a refresh queues one too.
export function backfillSection() {
  const out = el("div");
  const show = (indexes, r) => out.replaceChildren(
    ...(r ? [el("p", { class: "dim small" }, `${r.lookups} Yahoo lookups this run`
      + (r.stopped ? `; stopped early: ${r.stopped}` : "") + ".")] : []),
    ...(r?.stale_corrections?.length ? [staleCorrections(r.stale_corrections)] : []),
    backfillTable(indexes), ...(r?.changes.length ? [changeTable(r.changes)] : []));
  const button = busyButton("Backfill from indexes", async (onState) => {
    try {
      clearError();
      const r = await runJob("/api/jobs/backfill", onState);
      show(r.indexes, r);
    } catch (err) { showError(`backfill: ${err.message}`); }
  });
  const details = el("details", null, el("summary", null, "Index backfill"),
    el("p", { class: "dim small" }, "Members of each market's main indexes that FinanceDatabase lacks, added from "
      + "Yahoo (never Tesla, only ordinary shares, no funds or investment trusts), and lines it has under the wrong code or far off in size, "
      + "corrected. Each press looks up a bounded batch; press again to carry on."),
    el("div", { class: "row" }, button), out, fundsPart(), correctionsPart());
  details.addEventListener("toggle", async () => {
    if (!details.open || out.childElementCount) return;
    try { show(await api("/api/universe/backfill")); } catch (err) { showError(err.message); }
  });
  return details;
}

// Investment trusts added before the backfill refused them: listed first, removed only on a second press.
function fundsPart() {
  const out = el("div");
  const show = async (remove) => {
    try {
      clearError();
      const r = remove ? await api("/api/universe/funds/remove", { method: "POST" }) : await api("/api/universe/funds");
      const gone = r.lines.filter((x) => !x.kept).length;
      out.replaceChildren(
        el("p", { class: "dim small" }, !r.lines.length ? "No closed-end funds among the lines added."
          : r.removed ? `Removed ${gone}.` : `${gone} to remove; held or watched lines stay.`),
        ...(r.lines.length ? [fundTable(r.lines)] : []),
        ...(!r.removed && gone ? [el("button", { onclick: () => show(true) }, `Remove ${gone}`)] : []));
    } catch (err) { showError(`investment trusts: ${err.message}`); }
  };
  return el("div", null, el("div", { class: "row" },
    el("button", { onclick: () => show(false) }, "Find investment trusts")), out);
}

function fundTable(lines) {
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, ...["Listing", "Name", "Index", ""].map((h) => el("th", null, h)))),
    el("tbody", null, ...lines.map((x) => el("tr", null, el("td", null, x.symbol), el("td", null, x.name),
      el("td", null, x.index_code), el("td", { class: "dim small" }, x.kept ? `kept: ${x.kept}` : "")))));
}

// The backfill's corrections, each undoable; the stored line keeps it until the next refresh.
function correctionsPart() {
  const out = el("div");
  const show = async (done) => {
    try {
      clearError();
      const r = await api("/api/universe/corrections");
      out.replaceChildren(
        ...(done ? [el("p", { class: "dim small" }, `Undid the correction of ${done}. `
          + "Refresh the universe to lay FinanceDatabase's own row back.")] : []),
        ...(r.stale.length ? [staleCorrections(r.stale)] : []),
        r.rows.length ? correctionTable(r.rows, undo) : el("p", { class: "dim small" }, "No corrections."));
    } catch (err) { showError(`corrections: ${err.message}`); }
  };
  const undo = async (symbol) => {
    try {
      const row = await api(`/api/universe/corrections/${encodeURIComponent(symbol)}`, { method: "DELETE" });
      await show(row.symbol);
    } catch (err) { showError(`undo: ${err.message}`); }
  };
  return el("div", null, el("div", { class: "row" },
    el("button", { onclick: () => show(null) }, "Show corrections")), out);
}

function correctionTable(rows, undo) {
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, ...["Listing", "Index", "Laid over", "Why", ""].map((h) => el("th", null, h)))),
    el("tbody", null, ...rows.map((r) => el("tr", null, el("td", null, r.symbol), el("td", null, r.index_code),
      el("td", null, Object.entries(r.fields).map(([k, v]) => `${k}=${v}`).join(", ")),
      el("td", { class: "dim small" }, r.note),
      el("td", null, el("button", { onclick: () => undo(r.symbol) }, "Undo"))))));
}

// A regroup can re-key a company, so its sector correction no longer applies.
export function staleNote(stale) {
  return `Sector corrections matching no company (re-key them in oracle/universe/corrections.py): ${stale.join("; ")}`;
}

function staleCorrections(stale) {
  return el("p", { class: "dim small" }, staleNote(stale));
}

function backfillTable(indexes) {
  const heads = ["Index", "Members", "Present", "Added", "Corrected", "Failed", "Waiting", "Note"];
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, ...heads.map((h) => el("th", null, h)))),
    el("tbody", null, ...indexes.map((c) => el("tr", null, el("td", null, c.name),
      ...[c.members, c.present, c.added, c.corrected, c.failed, c.waiting].map((n) => el("td", null, String(n))),
      el("td", { class: "dim small" }, c.error || "")))));
}

function changeTable(changes) {
  return el("table", { class: "results" },
    el("thead", null, el("tr", null, el("th", null, "Change"), el("th", null, "Listing"), el("th", null, "Name"),
      el("th", null, "Index"), el("th", null, "Why"))),
    el("tbody", null, ...changes.map((c) => el("tr", null, el("td", null, c.kind),
      el("td", null, c.kind === "failed" ? c.symbol : companyLink(c.symbol)), el("td", null, c.name),
      el("td", null, c.index), el("td", { class: "dim small" }, c.note)))));
}
