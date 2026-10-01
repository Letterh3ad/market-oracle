// The Signal tests card's Form 4 and 8-K part: the buttons that fetch the testbed members' insider
// trades and typed filings as a job, their coverage per year, and the pre-registered typed-event
// study (8-K items 2.02, 1.01, 2.01 and insider-buy clusters) with Run / Train only and its tables.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { followJob } from "./ui.js";

const COLUMNS = [["P", "Buys"], ["P_issuers", "Issuers buying"], ["S", "Sales"], ["4", "Form 4s"], ["8-K", "8-Ks"],
  ["8-K 2.02", "2.02"], ["8-K 1.01", "1.01"], ["8-K 2.01", "2.01"]];
const stamp = (at) => String(at).slice(0, 16).replace("T", " ");
const pct = (x, digits = 2) => (x == null ? "-" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`);
const fixed = (x, digits = 2) => (x == null ? "-" : x.toFixed(digits));

function coverageTable(c) {
  const head = [["Year"], ...COLUMNS.map(([, label]) => [label, null, 1])];
  const rows = (c.years ?? []).map((y) => el("tr", null, el("td", null, String(y.year)),
    ...COLUMNS.map(([k]) => el("td", { class: "num" }, String(y[k] ?? 0)))));
  return table(head, rows, "Nothing fetched yet: press Fetch Form 4s and 8-Ks.");
}

function studyTable(s) {
  const head = [["Event"], ["Window"], ...s.horizons.map((h) => [`${h} d${h === s.primary ? " (primary)" : ""}`, null, 1]),
    ["Verdict"]];
  const cell = (r) => el("td", { class: "num" }, r ? `${pct(r.mean)}, t ${fixed(r.t)}` : "-",
    el("div", { class: "dim small" }, r ? `${r.n} events, ${r.days} days, ${r.positive} up` : ""));
  const rows = s.types.flatMap((t) => [["train", t.train], ["confirm", t.confirm]].filter(([, rows]) => rows.length)
    .map(([w, rows], k) => el("tr", null,
      k ? el("td") : el("td", null, el("span", { class: "mono" }, t.name), el("div", { class: "dim small" }, t.label)),
      el("td", { class: "small" }, w === "train" ? "Train (sign only)" : "Confirm"),
      ...s.horizons.map((h) => cell(rows.find((r) => r.horizon === h))),
      el("td", { class: k ? "" : t.passed ? "up" : t.verdict === "FAIL" ? "down" : "dim" }, k ? "" : t.verdict))));
  return table(head, rows, "No event types.");
}

function jobButton(label, url, onJob, onError) {
  const b = el("button", { type: "button", onclick: async () => {
    b.disabled = true;
    try {
      onJob(await api(url, { method: "POST" }));
    } catch (err) {
      onError(err.message);
    } finally {
      b.disabled = false;
    }
  } }, label);
  return b;
}

function noteOf() {
  const node = el("div", { class: "form-note" });
  return { node, say: (text, bad = true) => { node.className = bad ? "form-note down" : "form-note"; node.replaceChildren(text); } };
}

function insiderEventsSection() {
  const fetchNote = noteOf();
  const fetchBody = el("div");
  const studyNote = noteOf();
  const studyBody = el("div");
  const followed = new Set();

  async function loadCoverage() {
    let c;
    try {
      c = await api("/api/insiders/coverage");
    } catch (err) {
      fetchNote.say(`Could not load the Form 4 coverage: ${err.message}`);
      return;
    }
    const r = c.last_run;
    const q = Object.keys(c.quarters ?? {}).sort();
    fetchBody.replaceChildren(
      el("p", { class: r && (r.stopped || r.failures.length) ? "small down" : "small" },
        `${c.priced ?? 0} priced members, ${c.issuers ?? 0} issuers with a CIK (${c.no_cik ?? 0} members without), `
        + `${c.filings_fetched ?? 0} issuers' filings fetched; trades for ${q.length} quarters`
        + `${q.length ? `, ${q[0]} to ${q[q.length - 1]}` : ""}. `
        + (r ? `Last fetch, ${stamp(r.finished_at)} UTC: ${r.summary}.` : "No fetch has run yet.")),
      coverageTable(c),
      ...((c.late ?? []).length ? [el("details", null, el("summary", null, `Today's CIK starts late (${c.late.length}): `
        + "no insider score before the date"), el("p", { class: "small" }, c.late.map((x) => `${x.ticker} (CIK ${x.cik}) `
        + `member from ${x.member_from}, scored from ${x.scored_from ?? "never"}`).join("; ") + "."))] : []));
  }

  async function loadStudy() {
    let kept;
    try {
      kept = await api("/api/events");
    } catch (err) {
      studyNote.say(`Could not load the event study: ${err.message}`);
      return;
    }
    const parts = [];
    for (const [key, title] of [["full", "Newest run"], ["train", "Newest train-only run"]]) {
      const s = kept[key];
      if (!s) continue;
      parts.push(el("p", { class: "small" }, `${title}, ${stamp(s.run_at)} UTC: testbed ${s.first_day} to ${s.last_day}; `
        + `insider trades through ${s.trades_through ?? "none stored"}.`), studyTable(s),
      el("p", { class: "dim small" }, `Caveat: ${s.caveat}`));
    }
    studyBody.replaceChildren(...(parts.length ? parts : [el("p", { class: "small" }, "Not run yet.")]));
  }

  function follow(job, note, what, reload) {
    if (!followed.has(job.id)) {
      followed.add(job.id);
      followJob(job, (j) => follow(j, note, what, reload)).then(() => note.say(`The ${what} finished.`, false),
        (err) => note.say(err.message)).finally(() => { followed.delete(job.id); reload(); });
    }
    note.say(el("span", null, `The ${what} is ${job.state}${job.progress ? `: ${job.progress}` : ""}. It waits while `
      + "live work runs. Follow or cancel it in the ", el("a", { href: "#activity" }, "Activity tab"), "."), false);
  }

  const fetching = (job) => follow(job, fetchNote, "Form 4 and 8-K fetch", loadCoverage);
  const studying = (job) => follow(job, studyNote, "event study", loadStudy);

  const node = el("div", null,
    el("h3", null, "Form 4s and 8-Ks"),
    el("p", { class: "dim small" }, "What insider_buys and the event study read: each priced member's Form 4 and 8-K "
      + "acceptance times and 8-K items (SEC submissions) and its Form 4 open-market purchases and sales (SEC's "
      + "quarterly insider data sets, 2009 on). A filing counts from the session after it was accepted; after the "
      + "latest published quarter there is no insider data."),
    el("div", { class: "row" },
      jobButton("Fetch Form 4s and 8-Ks", "/api/jobs/insiders", fetching, fetchNote.say), " ",
      jobButton("Refetch Form 4s and 8-Ks", "/api/jobs/insiders?refresh=true", fetching, fetchNote.say)),
    fetchNote.node, fetchBody,
    el("h3", null, "Typed-event study"),
    el("p", { class: "dim small" }, "Mean cumulative sector-excess return after 8-K items 2.02 (results), 1.01 "
      + "(material agreement), 2.01 (acquisition or disposal) and insider-buy clusters (3 or more officers and "
      + "directors buying within 30 days), from the open of the session after the filing, t clustered by day. A type "
      + "passes only when its confirm mean at the primary horizon has t above 2 in the sign train showed."),
    el("div", { class: "row" },
      jobButton("Run event study", "/api/jobs/event-study", studying, studyNote.say), " ",
      jobButton("Event study, train only", "/api/jobs/event-study?train_only=true", studying, studyNote.say)),
    studyNote.node, studyBody);

  return {
    node,
    start: () => Promise.all([loadCoverage(), loadStudy()]),
    follow: (jobs) => {
      jobs.filter((j) => j.kind === "insiders" && ["queued", "running"].includes(j.state)).forEach(fetching);
      jobs.filter((j) => j.kind === "event_study" && ["queued", "running"].includes(j.state)).forEach(studying);
    },
  };
}

export { insiderEventsSection };
