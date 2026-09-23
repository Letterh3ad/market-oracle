// The Company tab's research section: run research, read the report, follow the trace.
import { api } from "./api.js";
import { el, showError, clearError, safeUrl } from "./dom.js";
import { tip } from "./tip.js";
import { busyButton } from "./ui.js";

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

function report(r) {
  const p = r.report;
  if (!p) return el("p", { class: "dim" }, r.error ? `This run failed: ${r.error}` : "This run wrote no report.");
  return el("div", null,
    el("div", { class: "row cards" }, side("Bull case", "bull_case", p.bull), side("Bear case", "bear_case", p.bear)),
    p.findings.length ? el("div", null, el("div", { class: "dim small" }, tip("Findings", "research_findings")),
      el("ul", null, ...p.findings.map((c) => claimLine(c, ROLES[c.role] ?? c.role)))) : null,
    p.risks.length ? el("div", null, el("div", { class: "dim small" }, tip("Risks", "risks")),
      el("ul", null, ...p.risks.map((c) => claimLine(c)))) : null,
    p.invalidated_if ? el("p", null, tip("Invalidated if", "invalidated_if"), ` ${p.invalidated_if}`) : null);
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

function researchSection(symbol) {
  const body = el("div", null, el("p", { class: "dim" }, "Loading research..."));
  const box = el("div", { class: "card", id: "c-research" });
  // Late answers paint only while this section is still on screen.
  const show = (r) => { if (box.isConnected) body.replaceChildren(...view(r.run, r.failed).filter(Boolean)); };
  const reload = () => api(`/api/companies/${encodeURIComponent(symbol)}/research`)
    .then(show)
    .catch((err) => { if (box.isConnected) body.replaceChildren(el("p", { class: "dim" }, err.message)); });
  const run = busyButton("Research", async () => {
    try {
      show(await api(`/api/jobs/research?symbol=${encodeURIComponent(symbol)}`, { method: "POST" }));
      clearError();
    } catch (err) {
      showError(err.message);
      reload();
    }
  });
  box.append(el("div", { class: "row" }, el("h3", null, tip("Research", "research")), run,
    el("span", { class: "dim small" }, "about a minute")), body);
  reload();
  return box;
}

export { researchSection };
