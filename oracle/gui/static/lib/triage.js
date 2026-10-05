// The Research tab's funnel stages (research-lab 05): Open Source Asset Pricing's data (Download in the compute
// window, Import from a folder), a method's OSAP signal, Triage (stage 1) and Power (stage 2). Their results show in
// the results table (results.js), which `changed` reloads and whose Pick fills the method here.
import { api } from "./api.js";
import { el } from "./dom.js";
import { followJob, sendJson } from "./ui.js";

const enc = encodeURIComponent;
const outcomeTone = (o) => (o === "pass" ? "up" : "down");

function statusText(s) {
  const kinds = Object.entries(s.signals).map(([k, n]) => `${n} ${k}`).join(", ") || "nothing imported yet";
  const variants = s.variants.map((v) => `${v.variant === "op" ? "original portfolios" : "large caps"}: `
    + `${v.signals} signals, ${v.first} to ${v.last}`).join("; ");
  return `OSAP release ${s.release}: ${kinds}${variants ? `. ${variants}` : ""}. Downloads run in ${s.window}: `
    + `${s.window_open ? "open now" : `opens ${s.next_window}`}.`;
}

const signed = (x, digits = 2) => `${x >= 0 ? "+" : ""}${x.toFixed(digits)}`;

// The same lines `oracle lab triage` and `oracle lab power` print under a stage's summary.
function detailLines(d) {
  const lines = [];
  for (const [key, label] of [["original", "original portfolio"], ["large", "large caps"]]) {
    if (!d[key]) continue;
    const spans = d[key].spans.filter((s) => s.mean != null)
      .map((s) => `${s.label} ${signed(s.mean)} t ${(s.t || 0).toFixed(2)} (${s.n} m)`).join("; ");
    lines.push(`${label}: ${spans}${d[key].decay == null ? "" : `; decay ${Math.round(d[key].decay * 100)}%`}`);
  }
  if (d.paper && d.paper.return != null) {
    lines.push(`paper (${d.paper.authors} ${d.paper.year}): ${signed(d.paper.return)}%/month`
      + (d.paper.t != null ? ` t ${d.paper.t.toFixed(2)}` : ""));
  }
  if (d.formula_ic != null) {
    lines.push(`names-and-days formula: ${d.formula_ic.toFixed(4)} (it leaves out cross-name correlation); `
      + `expected from ${d.expected_from}`);
  }
  return lines;
}

function stageCell(e) {
  if (!e) return el("td", { class: "small dim" }, "");
  return el("td", { class: `small ${outcomeTone(e.outcome)}`, "data-stage": e.stage }, `${e.outcome}: ${e.summary}`,
    ...detailLines(e.detail || {}).map((line) => el("div", { class: "dim", "data-detail": "line" }, line)),
    el("div", { class: "dim" }, `${e.status_from} to ${e.status_to}, ${e.by}, ${String(e.at).slice(0, 10)}`));
}

function funnelSection({ changed = async () => {} } = {}) {
  const note = el("div", { class: "form-note" });
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };
  const status = el("p", { class: "small" });

  async function load() {
    try {
      status.replaceChildren(statusText(await api("/api/lab/osap")));
    } catch (err) { say(`Could not load the OSAP status: ${err.message}`); }
  }
  const reload = () => Promise.all([load(), changed()]);

  const download = el("button", { type: "button", onclick: async () => {
    download.disabled = true;
    try {
      const job = await api("/api/jobs/osap-fetch", { method: "POST" });
      say("The OSAP download is queued; follow it on the Activity tab.", false);
      await followJob(job, () => {});
      say("OSAP downloaded and imported.", false);
    } catch (err) { say(err.message); } finally { download.disabled = false; await reload(); }
  } }, "Download OSAP");
  const folder = el("input", { size: 40, "aria-label": "Folder holding OSAP's files" });
  const importer = el("button", { type: "button", onclick: async () => {
    try {
      const done = await sendJson("/api/lab/osap/import", { folder: folder.value.trim() });
      say(`Imported ${done.imports.map((i) => `${i.file} (${i.rows} rows)`).join(", ")}.`, false);
      await reload();
    } catch (err) { say(err.message); }
  } }, "Import");

  const ref = el("input", { size: 8, "aria-label": "Method ref" });
  const signal = el("input", { size: 14, "aria-label": "OSAP signal (acronym)" });
  const run = (label, call) => el("button", { type: "button", onclick: async () => {
    const r = ref.value.trim();
    if (!r) { say("Give a method ref (e.g. T-15)."); return; }
    try {
      const msg = await call(r);
      say(msg, false);
      await reload();
    } catch (err) { say(err.message); }
  } }, label);
  const map = run("Set OSAP signal", async (r) => {
    const done = await sendJson(`/api/lab/methods/${enc(r)}/osap`, { signal: signal.value.trim() || null }, "PUT");
    return done.signal ? `${r} is OSAP ${done.signal}.` : `${r} has no OSAP signal.`;
  });
  const byHand = el("select", { "aria-label": "Triage by hand" },
    ...[["", "from OSAP"], ["pass", "by hand: pass"], ["stop", "by hand: stop"]].map(([v, t]) => el("option", { value: v }, t)));
  const why = el("input", { size: 40, "aria-label": "Triage note (the evidence; needed by hand)" });
  const stageText = (e) => `${e.stage} ${e.outcome}: ${e.summary} (${e.status_from} to ${e.status_to}).`;
  const triage = run("Triage", async (r) => stageText(await sendJson(`/api/lab/methods/${enc(r)}/triage`,
    { by_hand: byHand.value || null, note: why.value })));
  const kind = el("select", { "aria-label": "Power for a" },
    ...["signal", "strategy"].map((k) => el("option", { value: k }, k)));
  const horizon = el("input", { size: 4, value: "20", "aria-label": "Horizon (trading days)" });
  const expected = el("input", { size: 8, "aria-label": "Expected IC or yearly excess (blank: from OSAP)" });
  const te = el("input", { size: 6, "aria-label": "Tracking error a year (strategy)" });
  const source = el("input", { size: 30, "aria-label": "Source of the expected size" });
  const num = (x) => (x.value.trim() === "" ? null : Number(x.value));
  const power = run("Check power", async (r) => stageText(await sendJson(`/api/lab/methods/${enc(r)}/power`,
    { kind: kind.value, horizon: Number(horizon.value || 20), expected: num(expected), tracking_error: num(te),
      source: source.value.trim() })));

  const node = el("div", { class: "card" }, el("h2", null, "Funnel stages: triage and power"),
    el("p", { class: "dim small" }, "Stage 1 (triage): the method's Open Source Asset Pricing long-short return in large "
      + "caps (ME above the NYSE 20th percentile) since 2000 must be positive at t 1.5, shown with the original sample "
      + "and the split before and after publication; a method with no surviving large-cap effect stops (fail). A method "
      + "OSAP lacks is triaged by hand with the evidence. Stage 2 (power): the expected effect against the smallest the "
      + "testbed sees at the bar a test registered now needs; one it cannot see stops as could not see (not testable). "
      + "Only methods marked go are triaged."),
    status, el("div", { class: "row small" }, download, " or import from a folder ", folder, " ", importer),
    el("div", { class: "row small" }, "Method ", ref, " OSAP signal ", signal, " ", map),
    el("div", { class: "row small" }, byHand, " note ", why, " ", triage),
    el("div", { class: "row small" }, kind, " horizon ", horizon, " expected ", expected, " tracking error ", te,
      " source ", source, " ", power),
    note);
  return { node, start: load, pick: (r) => { ref.value = r; } };
}

export { funnelSection, stageCell };
