// The Ledgers tab's Signal tests card: the testbed (S&P 500 members as they were on each day,
// their bars from 2010 and sectors, in the research database), the buttons that build or refresh
// it as a job, its coverage per year: member-days with and without bars (the missing share) and
// the members never priced; then the pre-registered signals, each with Run test and its newest
// result (power, missing share, train, confirm, verdict). The SEC fundamentals the fundamental
// signals read are fetched from here too, with their coverage per year.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { followJob } from "./ui.js";

const ACTIVE = ["queued", "running"];
const share = (x) => `${(x * 100).toFixed(1)}%`;

function yearsTable(c) {
  const head = [["Year"], ["Members", null, 1], ["Member-days", null, 1], ["With bars", null, 1], ["Missing", null, 1],
    ["No bars", null, 1]];
  const rows = c.years.map((y) => el("tr", null,
    el("td", null, String(y.year)),
    el("td", { class: "num" }, String(y.members)),
    el("td", { class: "num" }, String(y.member_days)),
    el("td", { class: "num" }, String(y.with_bars)),
    el("td", { class: `num${y.missing_share > 0.1 ? " down" : ""}` }, share(y.missing_share)),
    el("td", { class: "num" }, String(y.unpriced))));
  return table(head, rows, "Nothing built yet: press Build testbed.");
}

function underPricedTable(c) {
  const head = [["Ticker"], ["Member from"], ["To"], ["Member-days", null, 1], ["Why"]];
  const rows = c.under_priced.map((u) => el("tr", null,
    el("td", { class: "mono" }, u.ticker),
    el("td", { class: "small" }, u.first_day),
    el("td", { class: "small" }, u.last_day),
    el("td", { class: "num" }, String(u.member_days)),
    el("td", { class: "small" }, u.note)));
  return table(head, rows, "Every member has bars.");
}

const FUNDAMENTAL = ["sue", "gross_profitability", "earnings_yield"];

function fundamentalsTable(c) {
  const head = [["Year"], ["Members", null, 1], ["With a CIK", null, 1], ["With facts", null, 1],
    ...FUNDAMENTAL.map((n) => [`${n}: members / filings`, null, 1])];
  const rows = (c.years ?? []).map((y) => el("tr", null,
    el("td", null, String(y.year)),
    el("td", { class: "num" }, String(y.members)),
    el("td", { class: "num" }, String(y.with_cik)),
    el("td", { class: "num" }, String(y.with_facts)),
    ...FUNDAMENTAL.map((n) => el("td", { class: "num" }, `${y.scored?.[n] ?? 0} / ${y.events?.[n] ?? 0}`))));
  return table(head, rows, "No fundamentals yet: press Fetch SEC fundamentals.");
}

const num = (x, digits = 4) => (x == null ? "-" : `${x > 0 ? "+" : ""}${x.toFixed(digits)}`);
const fixed = (x, digits = 4) => (x == null ? "-" : x.toFixed(digits));
const pct = (x) => (x == null ? "-" : share(x));

function windowCell(w) {
  return `IC ${num(w.ic)}, t ${fixed(w.t, 2)}, ${w.positive}+ of ${w.counted} segments: ${w.verdict_text ?? w.verdict}`;
}

function segmentsTable(w) {
  const head = [["From"], ["To"], ["IC", null, 1], ["t", null, 1], ["Days", null, 1], ["Counted"]];
  const rows = w.segments.map((s) => el("tr", null,
    el("td", { class: "small" }, s.start), el("td", { class: "small" }, s.end),
    el("td", { class: "num" }, num(s.ic)), el("td", { class: "num" }, fixed(s.t, 2)),
    el("td", { class: "num" }, String(s.days)), el("td", { class: "small" }, s.counted ? "yes" : s.why)));
  return table(head, rows, "No segments.");
}

function signalsTable(signals, buttons) {
  const head = [["Signal"], ["Horizon", null, 1], ["Power train / confirm (observed)", null, 1],
    ["Missing train / confirm", null, 1], ["Train (reported only)"], ["Confirm"], ["Verdict"], [""]];
  const rows = signals.flatMap((s) => {
    const r = s.result;
    const label = el("td", null, el("span", { class: "mono" }, s.name), el("div", { class: "dim small" }, s.label));
    if (!r) {
      return [el("tr", null, label, el("td", { class: "num" }, s.horizons.join(", ")),
        el("td", { class: "small", colspan: 5 }, "Not run yet."), el("td", null, ...buttons(s.name)))];
    }
    return r.confirm.map((c, k) => {
      const t = r.train[k];
      return el("tr", null, k ? el("td") : label,
        el("td", { class: "num" }, String(c.horizon)),
        el("td", { class: "num" }, `${fixed(t.observed_power)} / ${fixed(c.observed_power)}`,
          el("div", { class: "dim small" }, `formula ${fixed(t.power)} / ${fixed(c.power)}`)),
        el("td", { class: "num" }, `${pct(t.missing_share)} / ${pct(c.missing_share)}`),
        el("td", { class: "small" }, windowCell(t)),
        el("td", { class: "small" }, windowCell(c)),
        el("td", { class: r.passed ? "up" : r.verdict === "FAIL" ? "down" : "dim" }, k ? "" : r.verdict_text ?? r.verdict),
        el("td", null, ...(k ? [] : buttons(s.name))));
    });
  });
  return table(head, rows, "No signals are registered.");
}

function resultDetails(s) {
  const r = s.result;
  const windows = [...r.train, ...r.confirm];
  return el("details", null, el("summary", null, `${s.name}: segments, power and the run (${String(r.run_at).slice(0, 16)
    .replace("T", " ")} UTC)`),
  el("p", { class: "small" }, `Testbed ${r.first_day} to ${r.last_day}, ${r.tickers} members; ${r.snapshot ?? "no snapshot"}.`
    + `${r.cutoff ? ` Model-read: only days after its training cutoff ${r.cutoff} count.` : ""}`),
  ...windows.flatMap((w) => [
    el("p", { class: "small" }, `${w.window === "train" ? "Train" : "Confirm"} ${w.start} to ${w.end}, ${w.horizon}-day `
      + `horizon: ${w.days} IC days, ${fixed(w.names, 0)} names a day, ${fixed(w.effective_days, 0)} effective days; `
      + `power ${fixed(w.observed_power)} (formula ${fixed(w.power)}); missing share ${pct(w.missing_share)}.`),
    segmentsTable(w)]),
  el("p", { class: "dim small" }, `Caveat: ${r.caveat}`));
}

const stamp = (at) => String(at).slice(0, 16).replace("T", " ");

function trainOnlyLines(signals) {
  return signals.filter((s) => s.train_result).flatMap((s) => s.train_result.train.map((w) => el("p", { class: "small" },
    el("span", { class: "mono" }, s.name), `: Train only, ${stamp(s.train_result.run_at)} UTC: ${windowCell(w)} `
      + `(power ${fixed(w.observed_power)}, missing share ${pct(w.missing_share)}; confirm not read).`)));
}

function signalTestsSection() {
  let started = false;
  const note = el("div", { class: "form-note" });
  const snapshot = el("p", { class: "small" });
  const lastRun = el("p", { class: "small" });
  const summary = el("p", { class: "small" });
  const body = el("div");
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  let following = null;
  const signalNote = el("div", { class: "form-note" });
  const signalsBody = el("div");
  const sayTest = (text, bad = true) => {
    signalNote.className = bad ? "form-note down" : "form-note";
    signalNote.replaceChildren(text);
  };
  const testing = new Set();

  function followTest(job) {
    if (testing.has(job.id)) return;
    testing.add(job.id);
    const show = (j) => sayTest(el("span", null, `${j.label ?? "The signal test"} is ${j.state}`
      + `${j.progress ? `: ${j.progress}` : ""}. Follow or cancel it in the `, el("a", { href: "#activity" },
      "Activity tab"), "."), false);
    show(job);
    followJob(job, show).then(() => sayTest("The signal test finished.", false), (err) => sayTest(err.message))
      .finally(() => { testing.delete(job.id); loadSignals(); });
  }

  const runButton = (name, trainOnly) => {
    const b = el("button", { type: "button", onclick: async () => {
      b.disabled = true;
      try {
        followTest(await api(`/api/jobs/signal-test?name=${encodeURIComponent(name)}${trainOnly ? "&train_only=true" : ""}`,
          { method: "POST" }));
      } catch (err) {
        sayTest(err.message);
      } finally {
        b.disabled = false;
      }
    } }, trainOnly ? "Train only" : "Run test");
    return b;
  };
  const buttons = (name) => [runButton(name, false), " ", runButton(name, true)];

  async function loadSignals() {
    let body;
    try {
      body = await api("/api/signals");
    } catch (err) {
      sayTest(`Could not load the signal tests: ${err.message}`);
      return;
    }
    signalsBody.replaceChildren(signalsTable(body.signals, buttons), ...trainOnlyLines(body.signals),
      ...body.signals.filter((s) => s.result).map(resultDetails));
  }

  function running(job) {
    if (following !== job.id) {
      following = job.id;
      // Reloads the table once the job ends, whatever its end.
      followJob(job, running).then(() => say("The testbed build finished.", false), (err) => say(err.message))
        .finally(() => { following = null; load(); });
    }
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
      + `to ${s.last_day}.${s.last_day < c.as_of ? ` Its last change is ${s.last_day}: to ${c.as_of} the members `
      + "are that day's list until the source adds later changes and a refresh reads them." : ""}`
      : "Membership: not fetched yet.");
    const r = c.last_run;
    lastRun.className = r && (r.stopped || r.failures.length) ? "small down" : "small";
    lastRun.replaceChildren(r ? `Last build, ${String(r.finished_at).slice(0, 16).replace("T", " ")} UTC: ${r.summary}.`
      : "No build has run yet.");
    summary.replaceChildren(`${c.members} members from ${c.start}, ${c.fetched} fetched, ${c.sectors} with a sector; `
      + `trading days from ${c.calendar}. Missing share ${share(c.missing_share)} of member-days. Caveat: ${c.caveat}.`);
    const dupes = c.duplicates.map((d) => el("p", { class: "small down" }, `Listed twice: ${d.tickers.join(" and ")}, `
      + `${d.first_day} to ${d.last_day} (${d.days} days): ${d.why}. A test keeps one of them.`));
    body.replaceChildren(yearsTable(c), ...dupes,
      el("details", null, el("summary", null, `Members priced on under ${Math.round(c.under_priced_below * 100)}% `
        + `of their member-days (${c.under_priced.length})`), underPricedTable(c)));
  }

  const factsNote = el("div", { class: "form-note" });
  const factsBody = el("div");
  const sayFacts = (text, bad = true) => {
    factsNote.className = bad ? "form-note down" : "form-note";
    factsNote.replaceChildren(text);
  };
  let fetching = null;

  async function loadFacts() {
    let c;
    try {
      c = await api("/api/xbrl/coverage");
    } catch (err) {
      sayFacts(`Could not load the fundamentals coverage: ${err.message}`);
      return;
    }
    const r = c.last_run;
    factsBody.replaceChildren(
      el("p", { class: r && (r.stopped || r.failures.length) ? "small down" : "small" },
        `${c.priced ?? 0} priced members, ${c.with_cik ?? 0} with a CIK, ${c.with_facts ?? 0} with facts. `
        + (r ? `Last fetch, ${stamp(r.finished_at)} UTC: ${r.summary}.` : "No fetch has run yet.")),
      fundamentalsTable(c),
      ...((c.without_cik ?? []).length ? [el("p", { class: "small" }, `No CIK in SEC's ticker map `
        + `(${c.without_cik.length}): ${c.without_cik.join(" ")}.`)] : []),
      ...((c.without_facts ?? []).length ? [el("p", { class: "small" }, `No XBRL facts at SEC `
        + `(${c.without_facts.length}): ${c.without_facts.join(" ")}.`)] : []));
  }

  function fetchingFacts(job) {
    if (fetching !== job.id) {
      fetching = job.id;
      followJob(job, fetchingFacts).then(() => sayFacts("The fundamentals fetch finished.", false),
        (err) => sayFacts(err.message)).finally(() => { fetching = null; loadFacts(); });
    }
    sayFacts(el("span", null, `The fundamentals fetch is ${job.state}${job.progress ? `: ${job.progress}` : ""}. `
      + "It waits while live work runs and resumes where it stopped. Follow or cancel it in the ",
    el("a", { href: "#activity" }, "Activity tab"), "."), false);
  }

  const factsButton = (label, refresh) => {
    const b = el("button", { type: "button", onclick: async () => {
      b.disabled = true;
      try {
        fetchingFacts(await api(`/api/jobs/xbrl${refresh ? "?refresh=true" : ""}`, { method: "POST" }));
      } catch (err) {
        sayFacts(err.message);
      } finally {
        b.disabled = false;
      }
    } }, label);
    return b;
  };

  async function follow() {
    try {
      const jobs = (await api("/api/activity")).jobs;
      const job = jobs.find((j) => j.kind === "testbed" && ACTIVE.includes(j.state));
      if (job) running(job);
      const facts = jobs.find((j) => j.kind === "xbrl" && ACTIVE.includes(j.state));
      if (facts) fetchingFacts(facts);
      jobs.filter((j) => j.kind === "signal_test" && ACTIVE.includes(j.state)).forEach(followTest);
    } catch {
      // Activity is only a hint here.
    }
  }

  async function begin() {
    if (started) return;
    started = true;
    await Promise.all([load(), loadSignals(), loadFacts()]);
    follow();
  }

  const node = el("div", { class: "card" },
    el("h2", null, "Signal tests"),
    el("p", { class: "dim small" }, "The testbed signals are tested on: every S&P 500 member as it was on each day "
      + "since 2010 (not today's list taken back), with daily bars and GICS sectors, kept in the research database "
      + "apart from the live one. Members Yahoo no longer lists (delisted, or renamed since) have no bars: the "
      + "missing share below is printed beside every result."),
    el("div", { class: "row" }, startButton("Build testbed", false), " ", startButton("Refresh testbed", true)),
    note, snapshot, lastRun, summary, body,
    el("h3", null, "SEC fundamentals"),
    el("p", { class: "dim small" }, "What SUE, gross profitability and earnings yield read: each priced member's SEC "
      + "XBRL facts, each filing's acceptance time and Yahoo's splits. A figure counts from the session after its "
      + "filing was accepted, and a restated figure never replaces what was first filed."),
    el("div", { class: "row" }, factsButton("Fetch SEC fundamentals", false), " ", factsButton("Refetch", true)),
    factsNote, factsBody,
    el("h3", null, "Pre-registered signals"),
    el("p", { class: "dim small" }, "Each signal is chosen on 2010-2019 (train, reported only) and judged on 2020 to "
      + "today (confirm): PASS needs a confirm IC above 0, t above 2 and the IC positive in more than half the counted "
      + "six-month segments. Power is the smallest IC the window could see at t 2, from its daily ICs' own spread (the "
      + "formula from names, days and horizon is beside it; it assumes names move independently and reads several "
      + "times too low). A FAIL whose IC was above 0 but under that power says so. The missing share is the window's "
      + "member-days without bars. Train only runs 2010-2019 alone, never reading the confirm. Nothing here was tuned "
      + "on results."),
    signalNote, signalsBody);
  return { node, start: begin };
}

export { signalTestsSection };
