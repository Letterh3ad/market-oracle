// The Company tab's research section: run research, read the report, follow the trace.
import { api } from "./api.js";
import { el, showError, clearError, safeUrl } from "./dom.js";
import { tip } from "./tip.js";

const ROLES = { fundamental_analyst: "Fundamentals", technical_analyst: "Price action", news_analyst: "News" };

// A citation such as [a3], jumping to that item in the evidence list below.
function cites(ids) {
  return ids.map((i) => el("a", { class: "mono small", href: `#ev-${i}`, onclick: (e) => {
    e.preventDefault();
    document.getElementById(`ev-${i}`)?.scrollIntoView({ block: "center" });
  } }, ` [a${i}]`));
}

function claimLine(c, label) {
  return el("li", null, label ? el("strong", null, `${label}: `) : null, c.text, ...cites(c.cites));
}

function side(label, key, claim) {
  return el("div", { class: "card" }, el("div", { class: "dim small" }, tip(label, key)),
    claim ? el("div", null, claim.text, ...cites(claim.cites))
      : el("div", { class: "dim" }, "No case the evidence supports."));
}

const TONES = { buy: "up", sell: "down", hold: "" };

// Wherever AI output shows, until the AI account's record says otherwise.
function unvalidatedBadge() {
  return el("span", { class: "badge warn" }, "unvalidated ", tip("", "research_unvalidated"));
}

function verdict(p) {
  const v = p.verdict;
  return el("div", { class: "card" },
    el("div", { class: "dim small" }, tip("Verdict", "verdict"),
      unvalidatedBadge()),
    v ? el("div", null, el("strong", { class: TONES[v.label] }, v.label.toUpperCase()),
      ` confidence ${Math.round(v.confidence * 100)}%: ${v.reason}`, ...cites(v.cites))
      : el("div", { class: "dim" }, `No verdict: ${p.no_verdict}.`),
    v ? el("div", { class: "small" }, `Wrong if: ${v.invalidated_if}`) : null,
    p.did_not_read ? el("div", { class: "dim small" }, tip("The verdict " + p.did_not_read, "verdict_unread")) : null);
}

function report(r) {
  const p = r.report;
  if (!p) return el("p", { class: "dim" }, r.error ? `This run failed: ${r.error}` : "This run wrote no report.");
  return el("div", null,
    verdict(p),
    el("div", { class: "row cards" }, side("Bull case", "bull_case", p.bull), side("Bear case", "bear_case", p.bear)),
    p.findings.length ? el("div", null, el("div", { class: "dim small" }, tip("Findings", "research_findings")),
      el("ul", null, ...p.findings.map((c) => claimLine(c, ROLES[c.role] ?? c.role)))) : null,
    p.risks.length ? el("div", null, el("div", { class: "dim small" }, tip("Risks", "risks")),
      el("ul", null, ...p.risks.map((c) => claimLine(c)))) : null,
    p.invalidated_if ? el("p", null, tip("Invalidated if", "invalidated_if"), ` ${p.invalidated_if}`) : null,
    p.rejections ? el("p", { class: "dim small" }, tip("Rejected", "research_rejections"), `: ${p.rejections}`) : null);
}

function evidence(r) {
  const cited = r.evidence.filter((e) => e.cited);
  if (!cited.length) return null;
  return el("div", null, el("div", { class: "dim small" }, tip("Evidence", "evidence")),
    el("ul", { class: "small" }, ...cited.map((e) => {
      const link = safeUrl(e.link);
      const title = link ? el("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title;
      return el("li", { id: `ev-${e.id}` }, el("span", { class: "mono" }, `[a${e.id}] `),
        `${e.published ? e.published.slice(0, 10) : "undated"} ${e.outlet ?? "unknown outlet"}: `, title,
        e.query ? el("span", { class: "dim" }, ` (found by "${e.query}")`) : null,
        e.body ? el("details", null, el("summary", { class: "dim" }, "what it read"),
          el("pre", { class: "observation" }, e.body)) : null);
    })));
}

function trace(r) {
  // A final step has an observation only when the step limit forced it, and that note belongs in the trace.
  const steps = r.steps.filter((s) => s.action !== "final" || s.observation);
  return el("details", null,
    el("summary", null, tip(`How it got there (${steps.length} step${steps.length === 1 ? "" : "s"})`, "research_trace")),
    r.queries.length ? el("p", { class: "dim small" }, `Planned searches: ${r.queries.join("; ")}`) : null,
    el("ol", { class: "small" }, ...steps.map((s) => el("li", null,
      el("div", null, s.thought || "(no thought)"),
      s.action && s.action !== "final" ? el("div", { class: "mono" }, `${s.action}(${s.input})`) : null,
      el("pre", { class: "observation" }, s.observation ?? "")))),
    r.findings ? el("p", { class: "small" }, el("strong", null, "The researcher's notes: "), r.findings) : null);
}

function view(r, failed) {
  const note = failed ? el("p", { class: "badge warn" }, `The last attempt failed: ${failed.error ?? "unknown error"}`) : null;
  if (!r) return [el("p", { class: "dim" }, "No research yet. Press Research: the AI model searches the news, " +
    "filings and the web, then writes a report. It never changes the lean.")];
  if (r.status === "running") return [el("p", { class: "dim" }, "Research is running now; follow it in the Activity tab.")];
  const when = r.as_of ? `replay as of ${r.as_of}` : `researched ${r.started_at.slice(0, 16).replace("T", " ")} UTC`;
  return [
    note,
    el("div", { class: "dim small" }, `${when} by ${r.model_label}`,
      unvalidatedBadge(),
      r.status === "step_cap" ? el("span", { class: "badge" }, "stopped at the step limit") : null,
      r.status === "failed" ? el("span", { class: "badge warn" }, "failed") : null),
    report(r), evidence(r), trace(r),
  ];
}

const ACTIVE = ["queued", "running"];

function jobLine(job) {
  if (job.state === "queued") return "Queued behind other research (see Activity).";
  if (job.state === "running") return `Running${job.progress ? `: ${job.progress}` : ""}...`;
  return "";
}

// Earlier reports, newest first; each opens in place of the latest.
function historyList(rows, open) {
  if (rows.length < 2) return null;
  return el("details", null, el("summary", null, tip(`Earlier reports (${rows.length})`, "research_history")),
    el("table", { class: "results small" }, el("tbody", null, ...rows.map((h) => el("tr", null,
      el("td", null, h.day, h.as_of ? el("span", { class: "badge" }, "replay") : null),
      el("td", null, h.model_label),
      el("td", null, h.verdict ? el("strong", { class: TONES[h.verdict.label] },
        `${h.verdict.label.toUpperCase()} ${Math.round(h.verdict.confidence * 100)}%`) : el("span", { class: "dim" }, "no verdict")),
      el("td", { class: "dim" }, h.status === "done" ? "" : h.status.replace("_", " ")),
      el("td", null, el("button", { type: "button", class: "small-btn", onclick: () => open(h.id) }, "Open")))))));
}

const today = () => new Date().toISOString().slice(0, 10);

function researchSection(symbol) {
  const sym = encodeURIComponent(symbol);
  const body = el("div", null, el("p", { class: "dim" }, "Loading research..."));
  const earlier = el("div");
  const status = el("span", { class: "dim small" }, "about a minute");
  const box = el("div", { class: "card", id: "c-research" });
  // Late answers paint only while this section is still on screen.
  const show = (r) => { if (box.isConnected) body.replaceChildren(...view(r.run, r.failed).filter(Boolean)); };
  const open = (id) => api(`/api/companies/${sym}/research/${id}`).then((r) => {
    if (!box.isConnected) return;
    const back = el("button", { type: "button", class: "small-btn", onclick: reload }, "Back to the latest");
    body.replaceChildren(el("p", { class: "dim small" }, "An earlier report. ", back), ...view(r.run).filter(Boolean));
  }).catch((err) => showError(err.message));
  const reload = () => {
    api(`/api/companies/${sym}/research`).then(show)
      .catch((err) => { if (box.isConnected) body.replaceChildren(el("p", { class: "dim" }, err.message)); });
    api(`/api/companies/${sym}/research/history`).then((rows) => { if (box.isConnected) earlier.replaceChildren(historyList(rows, open) ?? ""); })
      .catch(() => {});
  };
  const buttons = [];
  // Follows the job until it ends, however the page came to it (a click, or a run already going).
  const follow = async (job) => {
    buttons.forEach((b) => { b.disabled = true; });
    while (box.isConnected && ACTIVE.includes(job.state)) {
      status.textContent = jobLine(job);
      await new Promise((resolve) => setTimeout(resolve, 2000));
      try {
        job = await api(`/api/activity/${job.id}`);
      } catch (err) {
        status.textContent = err.message;
        break;
      }
    }
    if (!box.isConnected) return;
    buttons.forEach((b) => { b.disabled = false; });
    status.textContent = job.state === "failed" || job.state === "interrupted" ? `Failed: ${job.error}`
      : job.state === "cancelled" ? "Cancelled." : "";
    reload();
  };
  const start = async (params) => {
    try {
      clearError();
      await follow(await api(`/api/jobs/research?${new URLSearchParams({ symbol, ...params })}`, { method: "POST" }));
    } catch (err) {
      showError(err.message);
      buttons.forEach((b) => { b.disabled = false; });
    }
  };
  const day = el("input", { type: "date", max: today(), "aria-label": "Replay as of" });
  const run = el("button", { type: "button", onclick: () => start({}) }, "Research");
  const replay = el("button", { type: "button", onclick: () => {
    if (!day.value) return showError("pick the day to replay");
    start({ as_of: day.value });
  } }, "Replay as of");
  buttons.push(run, replay);
  box.append(el("div", { class: "row" }, el("h3", null, tip("Research", "research")), run, status,
    el("span", { class: "row" }, replay, day, tip("", "research_replay"))), body, earlier);
  reload();
  api("/api/activity").then(({ jobs }) => {
    // A replay's subject is SYMBOL@day.
    const going = jobs.find((j) => j.kind === "research" && (j.subject === symbol || j.subject?.startsWith(`${symbol}@`))
      && ACTIVE.includes(j.state));
    if (going) follow(going);
  }).catch(() => {});
  return box;
}

export { researchSection, unvalidatedBadge };
