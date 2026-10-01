// The Ledgers tab's Signal tests card: the testbed (S&P 500 members as they were on each day,
// their bars from 2010 and sectors, in the research database), the buttons that build or refresh
// it as a job, and its coverage per year: member-days with and without bars (the missing share)
// and the members never priced.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";

const ACTIVE = ["queued", "running"];
const share = (x) => `${(x * 100).toFixed(1)}%`;

function yearsTable(c) {
  const head = [["Year"], ["Members", null, 1], ["Member-days", null, 1], ["With bars", null, 1], ["Missing", null, 1],
    ["Never priced", null, 1]];
  const rows = c.years.map((y) => el("tr", null,
    el("td", null, String(y.year)),
    el("td", { class: "num" }, String(y.members)),
    el("td", { class: "num" }, String(y.member_days)),
    el("td", { class: "num" }, String(y.with_bars)),
    el("td", { class: `num${y.missing_share > 0.1 ? " down" : ""}` }, share(y.missing_share)),
    el("td", { class: "num" }, String(y.unpriced))));
  return table(head, rows, "Nothing built yet: press Build testbed.");
}

function unpricedTable(c) {
  const head = [["Ticker"], ["Member from"], ["To"], ["Member-days", null, 1], ["Why"]];
  const rows = c.unpriced.map((u) => el("tr", null,
    el("td", { class: "mono" }, u.ticker),
    el("td", { class: "small" }, u.first_day),
    el("td", { class: "small" }, u.last_day),
    el("td", { class: "num" }, String(u.member_days)),
    el("td", { class: "small" }, u.note)));
  return table(head, rows, "Every member has bars.");
}

function signalTestsSection() {
  let started = false;
  const note = el("div", { class: "form-note" });
  const snapshot = el("p", { class: "small" });
  const lastRun = el("p", { class: "small" });
  const summary = el("p", { class: "small" });
  const body = el("div");
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  function running(job) {
    say(el("span", null, `The testbed build is ${job.state}${job.progress ? `: ${job.progress}` : ""}. The first build `
      + "takes hours, waits while live work runs or a scheduled tick is near, and resumes where it stopped. Follow "
      + "or cancel it in the ", el("a", { href: "#activity" }, "Activity tab"), "."), false);
  }

  const startButton = (label, refresh) => {
    const b = el("button", { type: "button", onclick: async () => {
      b.disabled = true;
      try {
        running(await api(`/api/jobs/testbed${refresh ? "?refresh=true" : ""}`, { method: "POST" }));
      } catch (err) {
        say(err.message);
      } finally {
        b.disabled = false;
      }
    } }, label);
    return b;
  };

  async function load() {
    let c;
    try {
      c = await api("/api/testbed/coverage");
    } catch (err) {
      say(`Could not load the testbed coverage: ${err.message}`);
      return;
    }
    const s = c.snapshot;
    snapshot.replaceChildren(s ? `Membership: ${s.source} at ${s.revision ? s.revision.slice(0, 7) : "an unknown revision"}, `
      + `fetched ${String(s.fetched_at).slice(0, 16).replace("T", " ")} UTC, ${s.changes} rows from ${s.first_day} `
      + `to ${s.last_day}.` : "Membership: not fetched yet.");
    const r = c.last_run;
    lastRun.className = r && (r.stopped || r.failures.length) ? "small down" : "small";
    lastRun.replaceChildren(r ? `Last build, ${String(r.finished_at).slice(0, 16).replace("T", " ")} UTC: ${r.summary}.`
      : "No build has run yet.");
    summary.replaceChildren(`${c.members} members from ${c.start}, ${c.fetched} fetched, ${c.sectors} with a sector; `
      + `trading days from ${c.calendar}. Missing share ${share(c.missing_share)} of member-days. Caveat: ${c.caveat}.`);
    body.replaceChildren(yearsTable(c),
      el("details", null, el("summary", null, `Members never priced (${c.unpriced.length})`), unpricedTable(c)));
  }

  async function follow() {
    try {
      const jobs = (await api("/api/activity")).jobs;
      const job = jobs.find((j) => j.kind === "testbed" && ACTIVE.includes(j.state));
      if (job) running(job);
    } catch {
      // Activity is only a hint here.
    }
  }

  async function begin() {
    if (started) return;
    started = true;
    await load();
    follow();
  }

  const node = el("div", { class: "card" },
    el("h2", null, "Signal tests"),
    el("p", { class: "dim small" }, "The testbed signals are tested on: every S&P 500 member as it was on each day "
      + "since 2010 (not today's list taken back), with daily bars and GICS sectors, kept in the research database "
      + "apart from the live one. Members Yahoo no longer lists (delisted, or renamed since) have no bars: the "
      + "missing share below is printed beside every result."),
    el("div", { class: "row" }, startButton("Build testbed", false), " ", startButton("Refresh testbed", true)),
    note, snapshot, lastRun, summary, body);
  return { node, start: begin };
}

export { signalTestsSection };
