// The Ledgers tab's History coverage card: the news, filings and price history the backfill stored
// back in time for a list and its industry maps, per company and quarter, and the button that
// starts (or resumes) the backfill as a job.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { fillPicker } from "./watchlists.js";

const ACTIVE = ["queued", "running"];

function coverageTable(c) {
  const head = [["Company"], ["On"], ["Bars from"], ...c.quarters.map((q) => [q, null, 1])];
  const rows = c.rows.map((r) => el("tr", null,
    el("td", { class: "mono", title: r.name }, r.symbol),
    el("td", { class: "small" }, r.main ? "main list" : `map of ${r.of.join(", ")}`),
    el("td", { class: "small" }, r.first_bar ?? "none"),
    ...c.quarters.map((q) => {
      const x = r.cells[q];
      const thin = r.thin.includes(q);
      return el("td", { class: `num${thin ? " down" : ""}`, title: x ? `${x.outlets} outlets` : "nothing stored" },
        x ? `${x.news}/${x.filings}` : "0/0");
    })));
  return table(head, rows, "No companies: the list is empty.");
}

function historySection() {
  let started = false;
  const list = el("select", { "aria-label": "History list", onchange: () => load() });
  const note = el("div", { class: "form-note" });
  const summary = el("p", { class: "small" });
  const body = el("div");
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  const start = el("button", { type: "button", onclick: async () => {
    start.disabled = true;
    try {
      const job = await api(`/api/jobs/history?${new URLSearchParams({ watchlist: list.value })}`, { method: "POST" });
      running(job);
    } catch (err) {
      say(err.message);
    } finally {
      start.disabled = false;
    }
  } }, "Backfill history");

  function running(job) {
    say(el("span", null, `The history backfill is ${job.state}${job.progress ? `: ${job.progress}` : ""}. It takes `
      + "hours, waits while live work runs and resumes where it stopped. Follow or cancel it in the ",
    el("a", { href: "#activity" }, "Activity tab"), "."), false);
  }

  async function load() {
    let c;
    try {
      c = await api(`/api/history/coverage?${new URLSearchParams({ watchlist: list.value })}`);
    } catch (err) {
      say(`Could not load the history coverage: ${err.message}`);
      return;
    }
    const thin = c.rows.reduce((n, r) => n + r.thin.length, 0);
    summary.replaceChildren(`${c.rows.length} companies from ${c.start}; ${c.windows_done} news windows done, `
      + `${c.windows_left} windows or companies left. ${thin} company quarters are thin (fewer than ${c.thin_below} `
      + `news articles, in red). Caveat: ${c.caveat}.`);
    body.replaceChildren(el("details", null, el("summary", null, "Per company and quarter (news/filings)"),
      coverageTable(c)));
  }

  async function follow() {
    try {
      const jobs = (await api("/api/activity")).jobs;
      const job = jobs.find((j) => j.kind === "history" && ACTIVE.includes(j.state));
      if (job) running(job);
    } catch {
      // Activity is only a hint here.
    }
  }

  async function begin() {
    if (started) return;
    started = true;
    try {
      fillPicker(list, await api("/api/watchlists"), "main");
    } catch (err) {
      say(err.message);
    }
    await load();
    follow();
  }

  const node = el("div", { class: "card" },
    el("h2", null, "History coverage"),
    el("p", { class: "dim small" }, "News, SEC filings and daily prices back in time for a list and every company on "
      + "its industry maps, so a backtest can ask whether the news ever predicted anything. Articles keep their "
      + "real published date; undated ones are dropped. Google News gives at most 100 results a search, so busy "
      + "quarters are searched month by month."),
    el("div", { class: "row" }, "List ", list, " ", start),
    note, summary, body);
  return { node, start: begin };
}

export { historySection };
