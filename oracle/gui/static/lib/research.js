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

function verdict(p) {
  const v = p.verdict;
  return el("div", { class: "card" },
    el("div", { class: "dim small" }, tip("Verdict", "verdict"),
      el("span", { class: "badge warn" }, "unvalidated ", tip("", "research_unvalidated"))),
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
      el("span", { class: "badge warn" }, "unvalidated ", tip("", "research_unvalidated")),
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

function researchSection(symbol) {
  const body = el("div", null, el("p", { class: "dim" }, "Loading research..."));
  const status = el("span", { class: "dim small" }, "about a minute");
  const box = el("div", { class: "card", id: "c-research" });
  // Late answers paint only while this section is still on screen.
  const show = (r) => { if (box.isConnected) body.replaceChildren(...view(r.run, r.failed).filter(Boolean)); };
  const reload = () => api(`/api/companies/${encodeURIComponent(symbol)}/research`)
    .then(show)
    .catch((err) => { if (box.isConnected) body.replaceChildren(el("p", { class: "dim" }, err.message)); });
  // Follows the job until it ends, however the page came to it (a click, or a run already going).
  const follow = async (job) => {
    run.disabled = true;
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
    run.disabled = false;
    status.textContent = job.state === "failed" || job.state === "interrupted" ? `Failed: ${job.error}`
      : job.state === "cancelled" ? "Cancelled." : "";
    reload();
  };
  const run = el("button", { type: "button", onclick: async () => {
    try {
      clearError();
      await follow(await api(`/api/jobs/research?symbol=${encodeURIComponent(symbol)}`, { method: "POST" }));
    } catch (err) {
      showError(err.message);
      run.disabled = false;
    }
  } }, "Research");
  box.append(el("div", { class: "row" }, el("h3", null, tip("Research", "research")), run, status), body);
  reload();
  api("/api/activity").then(({ jobs }) => {
    const going = jobs.find((j) => j.kind === "research" && j.subject === symbol && ACTIVE.includes(j.state));
    if (going) follow(going);
  }).catch(() => {});
  return box;
}

export { researchSection };
