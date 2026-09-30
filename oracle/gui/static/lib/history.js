// The Ledgers tab's History coverage card: the news, filings and price history the backfill stored
// back in time for a list and its industry maps, per company and quarter, how much of each
// company's news names it, the name its news is searched by, and the button that starts (or
// resumes) the backfill as a job.
import { api } from "./api.js";
import { table } from "./compare.js";
import { el } from "./dom.js";
import { sendJson } from "./ui.js";
import { fillPicker } from "./watchlists.js";

const ACTIVE = ["queued", "running"];

function searched(s) {
  if (s.skip) return `not searched${s.note ? ` (${s.note})` : ""}`;
  return `${s.query} (${s.source})`;
}

function coverageTable(c, pick) {
  const head = [["Company"], ["On"], ["Searched as"], ["Bars from"], ...c.quarters.map((q) => [q, null, 1])];
  const rows = c.rows.map((r) => el("tr", null,
    el("td", { class: "mono", title: r.name },
      el("a", { href: "#", onclick: (e) => { e.preventDefault?.(); pick(r); } }, r.symbol)),
    el("td", { class: "small" }, r.main ? "main list" : `map of ${r.of.join(", ")}`),
    el("td", { class: "small" }, searched(r.search)),
    el("td", { class: "small" }, r.first_bar ?? "none"),
    ...c.quarters.map((q) => {
      const x = r.cells[q];
      const bad = r.thin.includes(q) || r.noisy.includes(q);
      const share = x?.on_topic == null ? "" : `, ${Math.round(x.on_topic * 100)}% name it`;
      return el("td", { class: `num${bad ? " down" : ""}`, title: x ? `${x.outlets} outlets${share}` : "nothing stored" },
        x ? `${x.news}/${x.filings}${r.noisy.includes(q) ? " noisy" : ""}` : "0/0");
    })));
  return table(head, rows, "No companies: the list is empty.");
}

function historySection() {
  let started = false;
  const list = el("select", { "aria-label": "History list", onchange: () => load() });
  const note = el("div", { class: "form-note" });
  const summary = el("p", { class: "small" });
  const lastRun = el("p", { class: "small" });
  const body = el("div");
  const say = (text, bad = true) => { note.className = bad ? "form-note down" : "form-note"; note.replaceChildren(text); };

  const symbol = el("input", { "aria-label": "Search name company", placeholder: "symbol", style: "width: 90px" });
  const query = el("input", { "aria-label": "Search name query", placeholder: '"Petrobras" or "MAN Truck" OR "MAN SE"',
    style: "width: 280px" });
  const aliases = el("input", { "aria-label": "Search name aliases", placeholder: "names in headlines, comma separated",
    style: "width: 200px" });
  const skip = el("input", { type: "checkbox", "aria-label": "Do not search its news" });

  function pick(r) {
    symbol.value = r.symbol;
    query.value = r.search.skip ? "" : r.search.query;
    aliases.value = r.search.aliases.join(", ");
    skip.checked = r.search.skip;
  }

  const save = el("button", { type: "button", onclick: async () => {
    try {
      const done = await sendJson(`/api/history/names/${encodeURIComponent(symbol.value.trim())}`,
        { query: query.value, aliases: aliases.value.split(",").map((a) => a.trim()).filter((a) => a), skip: skip.checked },
        "PUT");
      say(`${done.symbol}: saved; ${done.forgotten} news windows will be asked again on the next backfill.`, false);
      await load(true);
    } catch (err) {
      say(err.message);
    }
  } }, "Save search name");

  const clear = el("button", { type: "button", onclick: async () => {
    try {
      const done = await api(`/api/history/names/${encodeURIComponent(symbol.value.trim())}`, { method: "DELETE" });
      say(`${done.symbol}: back to the default name; ${done.forgotten} news windows will be asked again.`, false);
      await load(true);
    } catch (err) {
      say(err.message);
    }
  } }, "Use default name");

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
      + "hours, waits while live work runs or a scheduled tick is near, and resumes where it stopped. Follow or "
      + "cancel it in the ", el("a", { href: "#activity" }, "Activity tab"), "."), false);
  }

  async function load(fresh = false) {
    let c;
    try {
      const q = new URLSearchParams({ watchlist: list.value });
      if (fresh) q.set("fresh", "true");
      c = await api(`/api/history/coverage?${q}`);
    } catch (err) {
      say(`Could not load the history coverage: ${err.message}`);
      return;
    }
    const thin = c.rows.reduce((n, r) => n + r.thin.length, 0);
    const noisy = c.rows.reduce((n, r) => n + r.noisy.length, 0);
    summary.replaceChildren(`${c.rows.length} companies from ${c.start}; ${c.windows_done} news windows done, `
      + `${c.windows_left} windows or companies left. ${thin} company quarters are thin (fewer than ${c.thin_below} `
      + `news articles) and ${noisy} noisy (under ${Math.round(c.noisy_below * 100)}% of headlines name the company), `
      + `both in red. Caveat: ${c.caveat}.`);
    const r = c.last_run;
    lastRun.className = r && (r.stopped || r.failures.length) ? "small down" : "small";
    lastRun.replaceChildren(r ? `Last run, ${String(r.finished_at).slice(0, 16).replace("T", " ")} UTC: ${r.summary}.`
      : "No backfill has run yet.");
    body.replaceChildren(el("details", null, el("summary", null, "Per company and quarter (news/filings)"),
      coverageTable(c, pick)));
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
      + "quarters of the list's own companies are searched month by month."),
    el("div", { class: "row" }, "List ", list, " ", start),
    note, lastRun, summary,
    el("div", { class: "row small" }, "Search name: ", symbol, query, aliases,
      el("label", null, skip, " do not search its news"), save, clear),
    el("p", { class: "dim small" }, "Click a company to edit the name its news is searched by: quote each name, join "
      + "several with OR, add context words after them (\"MAN Truck\" OR \"MAN SE\"). Aliases are the names a "
      + "headline about it uses; they measure how much of its news is about it. Saving asks its news again."),
    body);
  return { node, start: begin };
}

export { historySection };
