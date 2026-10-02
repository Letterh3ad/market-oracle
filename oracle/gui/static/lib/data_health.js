// The Data health card (ai-track 03): the newest data-check run's count per check, each check's
// flagged rows behind a drill-down, and a button that runs the checks as a job. They also run
// nightly after the scheduled tick. Flags only: nothing here edits market data.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { followJob } from "./ui.js";

const stamp = (at) => String(at).slice(0, 16).replace("T", " ");

function rowsTable(c) {
  const rows = c.rows.map((f) => el("tr", null,
    el("td", { class: "small" }, f.db), el("td", { class: "mono" }, f.symbol || "-"),
    el("td", { class: "small" }, f.day ?? "-"),
    el("td", { class: "small" }, f.detail, f.explanation ? el("div", { class: "dim small" }, f.explanation) : null)));
  const kept = c.count > c.rows.length ? ` (the first ${c.rows.length} of ${c.count} are kept)` : "";
  return el("div", null, table([["Database"], ["Symbol"], ["Day"], ["Finding"]], rows, "No flags."),
    kept ? el("p", { class: "dim small" }, kept.trim()) : null);
}

function checkRow(c) {
  const head = el("summary", null, el("strong", { class: c.count ? "down" : "up" }, String(c.count)), ` ${c.label}`);
  return c.count ? el("details", null, head, rowsTable(c)) : el("p", { class: "small" }, el("span", { class: "up" }, "0"),
    ` ${c.label}`);
}

function dataHealthSection() {
  const note = el("div", { class: "form-note" });
  const body = el("div");
  const followed = new Set();
  let started = false;

  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  async function load() {
    let d;
    try {
      d = await api("/api/data/checks");
    } catch (err) {
      say(`Could not load the data checks: ${err.message}`);
      return;
    }
    const settings = el("p", { class: "dim small" }, `${d.nightly ? "They run nightly after the scheduled tick." :
      "Nightly runs are off ([data_checks] nightly)."} Explanations are ${d.explain ? "on: a local Ollama model "
      + "explains the first flags of each run" : "off ([data_checks] explain in Settings)"}.`);
    const r = d.run;
    if (!r) {
      body.replaceChildren(settings, el("p", { class: "small" }, "No data check has run yet: press Run data checks."));
      return;
    }
    body.replaceChildren(
      el("p", { class: "small" }, `Last run ${stamp(r.run_at)} UTC, ${r.seconds} s: ${r.summary}.`), settings,
      ...r.notes.map((n) => el("p", { class: "dim small" }, n)),
      ...r.checks.map(checkRow));
  }

  function follow(job) {
    if (followed.has(job.id)) return;
    followed.add(job.id);
    const state = (j) => say(el("span", null, `The data checks are ${j.state}${j.progress ? `: ${j.progress}` : ""}. `
      + "Follow them in the ", el("a", { href: "#activity" }, "Activity tab"), "."), false);
    state(job);
    followJob(job, state).then(() => say("The data checks finished.", false), (err) => say(err.message))
      .finally(() => { followed.delete(job.id); load(); });
  }

  const run = el("button", { type: "button", onclick: async () => {
    run.disabled = true;
    try {
      follow(await api("/api/jobs/data-check", { method: "POST" }));
    } catch (err) {
      say(err.message);
    } finally {
      run.disabled = false;
    }
  } }, "Run data checks");

  const node = el("div", { class: "card" },
    el("h2", null, "Data health"),
    el("p", { class: "dim small" }, "Checks over the live and research databases: split-sized jumps with no recorded "
      + "split, stale or repeated bars, zero or negative prices, fund or junk tickers in stock sets, missing "
      + "dividends, rename gaps, duplicate share classes and testbed member-days missing by year. They only flag; "
      + "market data is never edited. Open a check to see its flagged rows."),
    el("div", { class: "row" }, run), note, body);

  const followAll = (jobs) => jobs.filter((j) => j.kind === "data_check" && ["queued", "running"].includes(j.state))
    .forEach(follow);

  // A run already going (the nightly one, or one started before the tab was opened) is followed too.
  function start() {
    if (started) return Promise.resolve();
    started = true;
    api("/api/activity").then(({ jobs }) => followAll(jobs ?? []), () => {});
    return load();
  }

  return { node, start, reload: load, follow: followAll };
}

export { dataHealthSection };
