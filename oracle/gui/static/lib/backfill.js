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
      + "Yahoo (never Tesla, only ordinary shares), and lines it has under the wrong code or far off in size, "
      + "corrected. Each press looks up a bounded batch; press again to carry on."),
    el("div", { class: "row" }, button), out);
  details.addEventListener("toggle", async () => {
    if (!details.open || out.childElementCount) return;
    try { show(await api("/api/universe/backfill")); } catch (err) { showError(err.message); }
  });
  return details;
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
